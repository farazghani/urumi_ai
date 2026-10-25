"""Two real, independent Streamable HTTP demo upstreams. No external API keys needed."""
import argparse
import math
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from fastmcp import FastMCP
from starlette.responses import PlainTextResponse

def make_server(kind):
    server=FastMCP(kind)
    @server.custom_route('/health', methods=['GET'])
    async def health(_request):
        return PlainTextResponse('ok')
    if kind=='clock':
        @server.tool()
        def current_time(timezone: str='Asia/Kolkata') -> dict:
            """Get the current date and time in any IANA timezone."""
            return {'timezone':timezone,'datetime':datetime.now(ZoneInfo(timezone)).isoformat()}
        @server.tool()
        def info() -> str:
            """Identify this upstream. Demonstrates namespacing of duplicate tool names."""
            return 'Clock: current date and time across timezones.'
    else:
        @server.tool()
        def calculate(operation: str, a: float, b: float) -> dict:
            """Perform add, subtract, multiply, divide, or power on two numbers."""
            if not all(math.isfinite(x) and abs(x)<=1e12 for x in (a,b)): raise ValueError('Numbers must be finite and within 1e12')
            if operation=='add': result=a+b
            elif operation=='subtract': result=a-b
            elif operation=='multiply': result=a*b
            elif operation=='divide': result=a/b
            elif operation=='power' and abs(b)<=100: result=a**b
            else: raise ValueError('Unsupported operation or exponent out of bounds')
            if isinstance(result,complex) or not math.isfinite(result): raise ValueError('Result is not a finite real number')
            return {'result':result}
        @server.tool()
        def info() -> str:
            """Identify this upstream. Demonstrates namespacing of duplicate tool names."""
            return 'Calculator: arithmetic operations on two numbers.'
    return server

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('kind',choices=['clock','calculator']); args=parser.parse_args()
    make_server(args.kind).run(transport='http',host=os.getenv('MCP_HOST','127.0.0.1'),port=int(os.getenv('MCP_PORT',8002 if args.kind=='clock' else 8003)),show_banner=False)
