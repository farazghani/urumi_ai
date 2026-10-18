"""Integration tests with live Streamable HTTP upstreams and a model fixture."""
import asyncio
import importlib
import json
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from backend import store
module=importlib.import_module('backend.app')

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0)); return s.getsockname()[1]

@pytest.fixture(scope='module')
def upstreams():
    children=[]; urls=[]
    for kind in ('clock','calculator'):
        port=free_port(); urls.append(f'http://127.0.0.1:{port}/mcp')
        code=f"from backend.upstreams import make_server; make_server('{kind}').run(transport='http',host='127.0.0.1',port={port},show_banner=False)"
        children.append(subprocess.Popen([sys.executable,'-c',code],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
    try:
        for url in urls:
            for _ in range(100):
                try: httpx.get(url,timeout=.3); break
                except httpx.TransportError: time.sleep(.1)
            else: raise RuntimeError('Upstream did not start')
        yield urls
    finally:
        for p in children: p.terminate()
        for p in children: p.wait(timeout=10)

@pytest.fixture
def workspace(tmp_path,monkeypatch,upstreams,mariadb):
    monkeypatch.setattr(store,'DATA',tmp_path)
    monkeypatch.setenv('ADMIN_PASSWORD','test-admin-password-123')
    with store.db() as db, db.cursor() as cursor:
        cursor.execute('SET FOREIGN_KEY_CHECKS=0')
        for table in ('sessions','api_keys','audit','upstreams','settings','users'):
            cursor.execute(f'DROP TABLE IF EXISTS `{table}`')
    module.attempts.clear()
    with TestClient(module.app) as client:
        store.execute('DELETE FROM upstreams')
        for i,url in enumerate(upstreams): store.execute('INSERT INTO upstreams(id,name,url,timeout) VALUES(?,?,?,?)',(i+1,['Clock','Calculator'][i],url,2))
        assert client.post('/api/auth/login',json={'email':'admin@urumi.local','password':'test-admin-password-123'}).status_code==200
        yield client

def customer(name='Customer',email='customer@example.com'):
    c=TestClient(module.app)
    assert c.post('/api/auth/signup',json={'email':email,'password':'customer-password-123','name':name}).status_code==200
    return c

def test_auth_roles_and_logout(workspace):
    c=customer()
    assert c.get('/api/me').json()['role']=='customer'
    assert c.get('/api/users').status_code==403
    assert c.post('/api/servers',json={'name':'bad','url':'http://localhost:5/mcp'}).status_code==403
    assert c.put('/api/settings/llm',json={'model':'fixture/tool-model'}).status_code==403
    assert c.post('/api/auth/signup',json={'name':'x','email':'customer@example.com','password':'long-password'}).status_code==409
    assert c.post('/api/auth/logout').status_code==200
    assert c.get('/api/me').status_code==401
    assert 'customer-password-123' not in store.query('SELECT password FROM users WHERE role="customer"',one=True)['password']

def test_key_approval_isolation_and_revocation(workspace):
    c=customer(); other=customer('Other','other@example.com')
    key=c.post('/api/keys',json={'name':'Claude'}).json(); headers={'Authorization':'Bearer '+key['token']}
    message={'jsonrpc':'2.0','id':1,'method':'tools/list'}
    assert workspace.post('/mcp',headers=headers,json=message).status_code==401
    assert other.get('/api/keys').json()==[]
    assert other.patch('/api/keys/'+str(key['id']),json={'status':'revoked'}).status_code==403
    assert c.patch('/api/keys/'+str(key['id']),json={'status':'approved'}).status_code==403
    assert workspace.patch('/api/keys/'+str(key['id']),json={'status':'approved'}).status_code==200
    assert len(workspace.post('/mcp',headers=headers,json=message).json()['result']['tools'])==4
    assert key['token'] not in json.dumps(c.get('/api/keys').json())
    workspace.patch('/api/keys/'+str(key['id']),json={'status':'suspended'})
    assert workspace.post('/mcp',headers=headers,json=message).status_code==401
    workspace.patch('/api/keys/'+str(key['id']),json={'status':'approved'})
    assert c.patch('/api/keys/'+str(key['id']),json={'status':'revoked'}).status_code==200
    assert workspace.post('/mcp',headers=headers,json=message).status_code==401
    assert workspace.patch('/api/keys/'+str(key['id']),json={'status':'approved'}).status_code==409

def test_real_tools_failure_isolation_and_audit(workspace):
    store.execute('INSERT INTO upstreams(name,url,timeout) VALUES(?,?,?)',('Dead','http://127.0.0.1:1/mcp',1))
    started=time.monotonic(); listing=workspace.get('/api/servers').json()
    assert time.monotonic()-started<4
    assert [s['status'] for s in listing]==['online','online','offline']
    names=[t['name'] for s in listing for t in s['tools']]
    assert 's1__info' in names and 's2__info' in names
    result=workspace.post('/api/tools/call',json={'name':'s2__calculate','arguments':{'operation':'multiply','a':84,'b':12}}).json()
    assert result['isError'] is False
    assert result['structuredContent']['result']==1008
    assert workspace.post('/api/tools/call',json={'name':'s3__info'}).json()['isError'] is True
    assert workspace.post('/api/tools/call',json={'name':'s1__missing'}).json()['isError'] is True
    logs=workspace.get('/api/audit').json()
    assert any(e['target']=='s2__calculate' and e['status']=='success' for e in logs)
    assert workspace.get('/api/overview').json()['calls']==3
    store.execute('UPDATE upstreams SET enabled=0 WHERE id=2')
    assert workspace.post('/api/tools/call',json={'name':'s2__info'}).json()['isError'] is True

def test_upstream_credentials_encryption_and_validation(workspace):
    payload={'name':'Protected','url':'http://127.0.0.1:9/mcp','auth_header':'Authorization','credential':'Bearer top-secret-123','enabled':False}
    sid=workspace.post('/api/servers',json=payload).json()['id']
    saved=store.query('SELECT * FROM upstreams WHERE id=?',(sid,),one=True)
    assert saved['credential']!='Bearer top-secret-123'
    assert store.unseal(saved['credential'])=='Bearer top-secret-123'
    assert 'top-secret-123' not in workspace.get('/api/servers').text
    payload.pop('credential'); workspace.put('/api/servers/'+str(sid),json=payload)
    assert store.query('SELECT credential FROM upstreams WHERE id=?',(sid,),one=True)['credential']==saved['credential']
    payload['credential']='';workspace.put('/api/servers/'+str(sid),json=payload)
    assert not store.query('SELECT credential FROM upstreams WHERE id=?',(sid,),one=True)['credential']
    assert workspace.post('/api/servers',json={'name':'bad','url':'file:///etc/passwd'}).status_code==422
    assert workspace.post('/api/servers',json={'name':'bad','url':'http://user:secret@localhost/mcp'}).status_code==422
    assert workspace.post('/api/keys',json={'name':'x'},headers={'Origin':'https://evil.test'}).status_code==403
    assert workspace.delete('/api/servers/'+str(sid)).status_code==200

def test_mcp_sdk_client_compatibility(workspace):
    token=workspace.post('/api/keys',json={'name':'SDK test'}).json()['token']
    port=free_port(); server=uvicorn.Server(uvicorn.Config(module.app,host='127.0.0.1',port=port,log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    async def run():
        transport=StreamableHttpTransport(f'http://127.0.0.1:{port}/mcp',headers={'Authorization':'Bearer '+token})
        async with Client(transport) as client:
            tools=await client.list_tools()
            assert len(tools)==4
            result=await client.call_tool('s1__info',{})
            assert not result.is_error and 'Clock' in result.content[0].text
    try: asyncio.run(run())
    finally: server.should_exit=True;thread.join(timeout=10)

class ModelHandler(BaseHTTPRequestHandler):
    requests=[]
    error_status=None
    error_in_success=False
    def log_message(self,*args): pass
    def reply(self,data,status=200):
        body=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path=='/api/v1/models':
            self.reply({'data':[
                {'id':'fixture/tool-model','name':'Tool model','supported_parameters':['tools','tool_choice'],'pricing':{'prompt':'0','completion':'0'}},
                {'id':'fixture/no-tools','name':'No tools','supported_parameters':[]}]})
        elif self.path=='/api/v1/key':
            if self.headers.get('Authorization')=='Bearer provider-secret': self.reply({'data':{'is_free_tier':True}})
            else: self.reply({'error':{'message':'invalid private credential','code':401}},401)
        else: self.reply({'error':{'code':404}},404)
    def do_POST(self):
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append({'path':self.path,'authorization':self.headers.get('Authorization'),'payload':data})
        if self.error_status:
            self.reply({'error':{'code':self.error_status,'message':'private provider-secret body'}},200 if self.error_in_success else self.error_status)
            return
        assert self.path=='/api/v1/chat/completions'
        if any(t['function']['name']=='connection_check' for t in data.get('tools',[])):
            message={'role':'assistant','content':None,'tool_calls':[{'id':'connection','type':'function','function':{'name':'connection_check','arguments':'{}'}}]}
        elif data['messages'][-1]['role']=='tool':
            message={'role':'assistant','content':'84 × 12 = 1008.'}
        else:
            message={'role':'assistant','content':None,'reasoning_details':[{'type':'reasoning.encrypted','data':'opaque-state'}],'tool_calls':[{'id':'call_test','type':'function','function':{'name':'s2__calculate','arguments':json.dumps({'operation':'multiply','a':84,'b':12})}}]}
        self.reply({'choices':[{'message':message}]})

@pytest.fixture
def router(workspace,monkeypatch):
    ModelHandler.requests=[];ModelHandler.error_status=None;ModelHandler.error_in_success=False
    server=ThreadingHTTPServer(('127.0.0.1',0),ModelHandler)
    monkeypatch.setattr(module.openrouter,'BASE_URL',f'http://127.0.0.1:{server.server_port}/api/v1')
    monkeypatch.setattr(module.openrouter,'_cache',{'expires':0,'models':[]})
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try: yield ModelHandler
    finally: server.shutdown();server.server_close()

def configure(workspace):
    r=workspace.put('/api/settings/llm',json={'provider':'openrouter','model':'fixture/tool-model','api_key':'provider-secret'})
    assert r.status_code==200,r.text
    return r

def test_chat_tool_loop_runs_as_customer(workspace,router):
    configure(workspace)
    assert 'provider-secret' not in workspace.get('/api/settings/llm').text
    c=customer();r=c.post('/api/chat',json={'message':'Multiply 84 by 12'})
    assert r.status_code==200,r.text
    data=r.json()
    assert data['message']=='84 × 12 = 1008.'
    assert data['tools'][0]['result']['structuredContent']['result']==1008
    assert len(router.requests)==2
    first,second=router.requests
    assert first['path']=='/api/v1/chat/completions'
    assert first['authorization']=='Bearer provider-secret'
    assert first['payload']['provider']=={'require_parameters':True}
    assert second['payload']['messages'][-1]['role']=='tool'
    assert second['payload']['messages'][-2]['reasoning_details'][0]['data']=='opaque-state'
    assert 'tools' in second['payload']
    logs=c.get('/api/audit').json()
    assert any(e['action']=='chat.tool' and e['email']=='customer@example.com' for e in logs)
    assert all(e['email']=='customer@example.com' for e in logs)

def test_openrouter_only_and_missing_key(workspace):
    assert workspace.put('/api/settings/llm',json={'provider':'ollama','model':'fixture/tool-model','api_key':'key'}).status_code==422
    assert workspace.put('/api/settings/llm',json={'model':'fixture/tool-model','base_url':'https://openrouter.ai/api/v1/chat/completions'}).status_code==422
    assert workspace.put('/api/settings/llm',json={'model':'fixture/tool-model'}).status_code==400
    r=workspace.post('/api/chat',json={'message':'hello'})
    assert r.status_code==400 and 'OpenRouter is not configured' in r.text

def test_catalog_save_and_key_lifecycle(workspace,router):
    assert workspace.get('/api/settings/llm/models').json()['models']==[{'id':'fixture/tool-model','name':'Tool model','free':True}]
    assert workspace.put('/api/settings/llm',json={'model':'fixture/no-tools','api_key':'provider-secret'}).status_code==400
    bad=workspace.put('/api/settings/llm',json={'model':'fixture/tool-model','api_key':'invalid-key'})
    assert bad.status_code==502 and 'rejected the API key' in bad.text
    assert workspace.get('/api/me').status_code==200
    assert not workspace.get('/api/settings/llm').json()['configured']
    assert configure(workspace).json()=={'provider':'openrouter','model':'fixture/tool-model','has_api_key':True,'configured':True}
    encrypted=json.loads(store.query("SELECT value FROM settings WHERE `key`='llm'",one=True)['value'])['api_key']
    assert encrypted!='provider-secret' and store.unseal(encrypted)=='provider-secret'
    assert workspace.put('/api/settings/llm',json={'model':'fixture/tool-model','api_key':None}).status_code==200
    assert workspace.get('/api/settings/llm').json()['has_api_key']
    assert workspace.put('/api/settings/llm',json={'model':'fixture/tool-model','api_key':''}).status_code==200
    assert not workspace.get('/api/settings/llm').json()['configured']

def test_legacy_full_endpoint_migrates_without_duplicate_path(workspace,router):
    store.execute("REPLACE INTO settings VALUES('llm',?)",(json.dumps({'base_url':'https://openrouter.ai/api/v1/chat/completions','model':'fixture/tool-model','api_key':store.seal('provider-secret')}),))
    assert workspace.get('/api/settings/llm').json()['configured']
    r=workspace.post('/api/chat',json={'message':'multiply'})
    assert r.status_code==200,r.text
    assert router.requests[0]['path']=='/api/v1/chat/completions'
    store.execute("UPDATE settings SET value=? WHERE `key`='llm'",(json.dumps({'base_url':'https://different-provider.test/v1','model':'other/model','api_key':store.seal('other-secret')}),))
    assert not workspace.get('/api/settings/llm').json()['has_api_key']

def test_connection_probe_does_not_execute_gateway_tools_or_save(workspace,router):
    r=workspace.post('/api/settings/llm/test',json={'model':'fixture/tool-model','api_key':'provider-secret'})
    assert r.status_code==200 and r.json()['ok']
    assert router.requests[0]['payload']['tool_choice']=='required'
    assert not workspace.get('/api/settings/llm').json()['configured']
    assert not any(a['action']=='chat.tool' for a in workspace.get('/api/audit').json())
    c=customer()
    assert c.post('/api/settings/llm/test',json={'model':'fixture/tool-model','api_key':'provider-secret'}).status_code==403

@pytest.mark.parametrize('status,phrase',[(401,'rejected the API key'),(402,'insufficient credits'),(403,'denied access'),(404,'unavailable'),(429,'rate limit'),(502,'provider behind OpenRouter failed'),(503,'currently available')])
def test_provider_errors_are_actionable_and_do_not_leak_secrets(workspace,router,status,phrase):
    configure(workspace);router.error_status=status
    r=workspace.post('/api/chat',json={'message':'multiply'})
    assert r.status_code==(502 if status==401 else status)
    assert phrase in r.text
    assert 'provider-secret' not in r.text
    assert workspace.get('/api/me').status_code==200

def test_error_in_http_200_is_reported(workspace,router):
    configure(workspace);router.error_status=429;router.error_in_success=True
    r=workspace.post('/api/chat',json={'message':'multiply'})
    assert r.status_code==429 and 'rate limit' in r.text


def test_overview_dates_remain_json_strings(workspace):
    user=store.query("SELECT * FROM users WHERE role='admin'",one=True)
    store.audit(user,'tool.call','date-check',duration=8)
    result=workspace.get('/api/overview')
    assert result.status_code==200
    assert result.json()['daily'][-1]['day']==time.strftime('%Y-%m-%d',time.gmtime())
    assert isinstance(workspace.get('/api/audit').json()[0]['created'],str)


def test_transactions_roll_back_and_enforce_foreign_keys(workspace):
    before=len(store.query('SELECT id FROM upstreams'))
    with pytest.raises(store.IntegrityError):
        with store.db() as db, db.cursor() as cursor:
            cursor.execute("INSERT INTO upstreams(name,url) VALUES('rollback','http://example.invalid')")
            cursor.execute("INSERT INTO sessions VALUES('invalid-user',99999,9999999999)")
    assert len(store.query('SELECT id FROM upstreams'))==before
