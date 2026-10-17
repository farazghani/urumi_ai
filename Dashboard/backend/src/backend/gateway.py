"""Independent, bounded Streamable HTTP connections; no shared client auth state."""
import asyncio
import time
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from . import store


def transport(server):
    headers = {server['auth_header']: store.unseal(server['credential'])} if server['auth_header'] and server['credential'] else {}
    return StreamableHttpTransport(server['url'], headers=headers)


def public(server):
    return {k:v for k,v in server.items() if k != 'credential'} | {'has_credential': bool(server['credential'])}


async def inspect(server):
    started = time.monotonic()
    result = public(server) | {'tools': [], 'status': 'disabled', 'latency': None}
    if not server['enabled']:
        return result
    try:
        async with asyncio.timeout(server['timeout']):
            async with Client(transport(server)) as client:
                tools = await client.list_tools()
        for tool in tools:
            item = tool.model_dump(mode='json', exclude_none=True)
            item['name'] = f"s{server['id']}__{tool.name}"
            result['tools'].append(item)
        result.update(status='online', latency=round((time.monotonic()-started)*1000))
    except Exception:
        # Upstream exceptions can include auth headers/URLs; never return raw exceptions.
        result.update(status='offline', error='Connection failed or timed out. Check the server URL, credentials and timeout.')
    return result


async def discover():
    return await asyncio.gather(*(inspect(s) for s in store.query('SELECT * FROM upstreams ORDER BY id')))


async def call(user, name, arguments, source='tool.call'):
    started = time.monotonic()
    try:
        prefix, original = name.split('__', 1)
        server = store.query('SELECT * FROM upstreams WHERE id=? AND enabled=1', (int(prefix.removeprefix('s')),), one=True)
        if not prefix.startswith('s') or not server:
            raise ValueError()
    except (ValueError, AttributeError):
        store.audit(user, source, str(name)[:150], 'error')
        return {'content':[{'type':'text','text':'Unknown tool or upstream disabled.'}], 'isError':True}
    try:
        async with asyncio.timeout(server['timeout']):
            async with Client(transport(server)) as client:
                result = await client.call_tool(original, arguments, raise_on_error=False)
        output = {'content':[c.model_dump(mode='json',exclude_none=True) for c in result.content], 'isError':result.is_error}
        if result.structured_content is not None:
            output['structuredContent'] = result.structured_content
    except Exception:
        output = {'content':[{'type':'text','text':f"{server['name']} could not complete {original} within its connection/call budget ({server['timeout']}s). Check upstream availability and arguments."}], 'isError':True}
    store.audit(user, source, name, 'error' if output.get('isError') else 'success', round((time.monotonic()-started)*1000))
    return output
