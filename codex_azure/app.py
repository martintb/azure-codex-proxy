import asyncio
import logging
import os
import time
from typing import Optional

import httpx
from azure.identity import (
    AzureCliCredential,
    ChainedTokenCredential,
    InteractiveBrowserCredential,
)
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .config import get_effective_resource


AZURE_SCOPE = os.environ.get(
    "AZURE_OPENAI_SCOPE",
    "https://cognitiveservices.azure.com/.default",
)
TOKEN_REFRESH_SKEW_SECONDS = int(
    os.environ.get("AZURE_OPENAI_TOKEN_REFRESH_SKEW_SECONDS", "300")
)
PROXY_HOST = os.environ.get("AZURE_OPENAI_PROXY_HOST", "127.0.0.1")
PROXY_PORT = int(os.environ.get("AZURE_OPENAI_PROXY_PORT", "4000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("azure-openai-proxy")

app = FastAPI()

credential = ChainedTokenCredential(
    AzureCliCredential(),
    InteractiveBrowserCredential(),
)

_token_value: Optional[str] = None
_token_expires_on: float = 0.0
_token_lock = asyncio.Lock()
http_client: Optional[httpx.AsyncClient] = None


def get_azure_resource() -> str:
    resource = get_effective_resource()
    if not resource:
        raise RuntimeError(
            "Azure OpenAI resource is not configured. Run 'codex-azure config set-resource' or set AZURE_OPENAI_RESOURCE."
        )
    return resource


def get_upstream_base() -> str:
    return f"{get_azure_resource()}/openai/v1"


async def get_valid_token(force_refresh: bool = False) -> str:
    global _token_value, _token_expires_on

    now = time.time()
    needs_refresh = (
        force_refresh
        or _token_value is None
        or now >= (_token_expires_on - TOKEN_REFRESH_SKEW_SECONDS)
    )

    if not needs_refresh:
        return _token_value

    async with _token_lock:
        now = time.time()
        needs_refresh = (
            force_refresh
            or _token_value is None
            or now >= (_token_expires_on - TOKEN_REFRESH_SKEW_SECONDS)
        )
        if not needs_refresh:
            return _token_value

        log.info("Refreshing Azure token")
        token = credential.get_token(AZURE_SCOPE)
        _token_value = token.token
        _token_expires_on = float(token.expires_on)
        return _token_value


def filter_request_headers(headers) -> dict:
    filtered = {}
    for key, value in headers.items():
        if key.lower() in {
            "host",
            "content-length",
            "authorization",
            "connection",
            "accept-encoding",
        }:
            continue
        filtered[key] = value
    return filtered


def filter_response_headers(headers) -> dict:
    filtered = {}
    for key, value in headers.items():
        if key.lower() in {
            "content-length",
            "content-encoding",
            "transfer-encoding",
            "connection",
        }:
            continue
        filtered[key] = value
    return filtered


async def forward_request(request: Request, path: str) -> Response:
    assert http_client is not None

    body = await request.body()
    upstream_url = f"{get_upstream_base()}/{path}"

    headers = filter_request_headers(request.headers)
    token = await get_valid_token(force_refresh=False)
    headers["Authorization"] = f"Bearer {token}"

    resp = await http_client.request(
        method=request.method,
        url=upstream_url,
        params=request.query_params,
        headers=headers,
        content=body,
    )

    if resp.status_code == 401:
        log.warning("401 from Azure; refreshing token and retrying once")
        token = await get_valid_token(force_refresh=True)
        retry_headers = dict(headers)
        retry_headers["Authorization"] = f"Bearer {token}"
        resp = await http_client.request(
            method=request.method,
            url=upstream_url,
            params=request.query_params,
            headers=retry_headers,
            content=body,
        )

    response_headers = filter_response_headers(resp.headers)
    content_type = resp.headers.get("content-type", "")

    if "application/json" in content_type:
        try:
            return JSONResponse(
                status_code=resp.status_code,
                content=resp.json(),
                headers=response_headers,
            )
        except Exception:
            pass

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=response_headers,
        media_type=resp.headers.get("content-type"),
    )


@app.on_event("startup")
async def startup() -> None:
    global http_client
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0))
    await get_valid_token(force_refresh=False)
    log.info("Proxy ready on http://%s:%s", PROXY_HOST, PROXY_PORT)


@app.on_event("shutdown")
async def shutdown() -> None:
    global http_client
    if http_client is not None:
        await http_client.aclose()
        http_client = None


@app.get("/healthz")
async def healthz():
    try:
        await get_valid_token(force_refresh=False)
        return {
            "ok": True,
            "resource": get_azure_resource(),
            "token_expires_on": _token_expires_on,
        }
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})


@app.api_route("/openai/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    try:
        return await forward_request(request, path)
    except Exception as exc:
        log.exception("Proxy error")
        return JSONResponse(status_code=502, content={"error": str(exc)})