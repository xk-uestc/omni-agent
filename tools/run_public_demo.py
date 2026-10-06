"""Subpath gateway for the existing shared tunnel's raysource origin (3011)."""
from __future__ import annotations

from contextlib import asynccontextmanager
import re

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import Response, RedirectResponse, StreamingResponse

PREFIX = '/demo'
ORIGIN = 'http://127.0.0.1:8030'
HOP_HEADERS = {'host', 'connection', 'transfer-encoding', 'content-length',
               'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'upgrade', 'content-encoding'}
# Existing frontend URLs and source-integrity checks remain rooted at /api.
# Translate transport URLs only, keeping this site's APIs within /demo.
BOOTSTRAP = """<script>
(()=>{const nativeFetch=window.fetch.bind(window);window.fetch=(input,init)=>{
 const u=new URL(input instanceof Request?input.url:input,location.href);
 if(u.origin===location.origin&&(u.pathname==='/health'||u.pathname.startsWith('/api/'))){
  u.pathname='/demo'+u.pathname;input=input instanceof Request?new Request(u,input):u;
 }return nativeFetch(input,init);
};})();
</script>"""


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(timeout=httpx.Timeout(330, connect=10),
                                trust_env=False) as client:
        app.state.client = client
        yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.api_route('/demo', methods=['GET', 'HEAD'])
async def slash():
    return RedirectResponse('/demo/', status_code=308)


@app.api_route('/demo/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
async def proxy(request: Request, path: str):
    # Use the encoded raw path so document IDs and query strings survive intact.
    raw_path = request.scope['raw_path'].decode('ascii')[len(PREFIX):]
    url = ORIGIN + raw_path
    if request.url.query:
        url += '?' + request.url.query
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    headers['accept-encoding'] = 'identity'
    upstream_request = request.app.state.client.build_request(
        request.method, url, headers=headers, content=request.stream())
    try:
        upstream = await request.app.state.client.send(upstream_request, stream=True)
    except httpx.HTTPError:
        return Response('Demo origin unavailable', status_code=502)
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_HEADERS}
    out_headers['cache-control'] = 'no-store'
    location = out_headers.get('location', '')
    if location.startswith('/') and not location.startswith('//'):
        out_headers['location'] = PREFIX + location
    if 'text/html' in upstream.headers.get('content-type', ''):
        try:
            html = (await upstream.aread()).decode('utf-8')
            html = re.sub(r'(href|src)="/(?!/)', r'\1="/demo/', html)
            html = html.replace('<head>', '<head>' + BOOTSTRAP, 1)
            return Response(html, status_code=upstream.status_code, headers=out_headers)
        finally:
            await upstream.aclose()

    async def chunks():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        finally:
            await upstream.aclose()

    out_headers['x-accel-buffering'] = 'no'
    return StreamingResponse(chunks(), status_code=upstream.status_code, headers=out_headers)


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=3011)
