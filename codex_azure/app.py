import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from azure.identity import (
    AzureCliCredential,
)
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from .config import (
    CODEX_MODEL_NAME,
    LOCAL_AUTH_HEADER,
    ensure_local_auth_token,
    get_effective_deployment,
    get_effective_proxy_host,
    get_effective_proxy_port,
    get_effective_resource,
)


AZURE_SCOPE = os.environ.get(
    "AZURE_OPENAI_SCOPE",
    "https://cognitiveservices.azure.com/.default",
)
TOKEN_REFRESH_SKEW_SECONDS = int(
    os.environ.get("AZURE_OPENAI_TOKEN_REFRESH_SKEW_SECONDS", "300")
)
PROXY_HOST = get_effective_proxy_host()
PROXY_PORT = get_effective_proxy_port()
MAX_REQUEST_BODY_BYTES = int(os.environ.get("AZURE_OPENAI_PROXY_MAX_BODY_BYTES", str(10 * 1024 * 1024)))
ALLOWED_METHODS = {"GET", "POST", "DELETE"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("azure-openai-proxy")


credential = AzureCliCredential()

_token_value: Optional[str] = None
_token_expires_on: float = 0.0
_token_lock = asyncio.Lock()
http_client: Optional[httpx.AsyncClient] = None
local_auth_token: Optional[str] = None


def get_azure_resource() -> str:
    resource = get_effective_resource()
    if not resource:
        raise RuntimeError(
            "Azure OpenAI resource is not configured. Run 'codex-azure config set-resource' or set AZURE_OPENAI_RESOURCE."
        )
    return resource


def get_upstream_base() -> str:
    return f"{get_azure_resource()}/openai/v1"


def get_azure_deployment() -> str | None:
    return get_effective_deployment()


def require_local_auth(request: Request) -> None:
    expected = local_auth_token or ensure_local_auth_token()
    presented = request.headers.get(LOCAL_AUTH_HEADER, "")
    if not expected or presented != expected:
        raise PermissionError("Missing or invalid local proxy authentication")


def rewrite_request_body(body: bytes, content_type: str) -> bytes:
    if "application/json" not in content_type:
        return body

    deployment = get_azure_deployment()
    if not deployment:
        return body

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body

    if isinstance(payload, dict) and payload.get("model") == CODEX_MODEL_NAME:
        payload["model"] = deployment
        return json.dumps(payload).encode("utf-8")

    return body


async def get_valid_token(force_refresh: bool = False) -> str:
    global _token_value, _token_expires_on

    now = time.time()
    needs_refresh = (
        force_refresh
        or _token_value is None
        or now >= (_token_expires_on - TOKEN_REFRESH_SKEW_SECONDS)
    )

    if not needs_refresh:
        assert _token_value is not None
        return _token_value

    async with _token_lock:
        now = time.time()
        needs_refresh = (
            force_refresh
            or _token_value is None
            or now >= (_token_expires_on - TOKEN_REFRESH_SKEW_SECONDS)
        )
        if not needs_refresh:
            assert _token_value is not None
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
            LOCAL_AUTH_HEADER,
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

    if request.method.upper() not in ALLOWED_METHODS:
        return JSONResponse(status_code=405, content={"error": "Method not allowed"})

    body = await request.body()
    if len(body) > MAX_REQUEST_BODY_BYTES:
        return JSONResponse(status_code=413, content={"error": "Request body too large"})

    upstream_url = f"{get_upstream_base()}/{path}"

    headers = filter_request_headers(request.headers)
    body = rewrite_request_body(body, request.headers.get("content-type", ""))
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    global http_client, local_auth_token
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0))
    local_auth_token = ensure_local_auth_token()
    try:
        await get_valid_token(force_refresh=False)
    except Exception as exc:
        log.error("Azure authentication failed during proxy startup: %s", exc)
        raise
    try:
        yield
    finally:
        if http_client is not None:
            await http_client.aclose()
            http_client = None


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz(request: Request):
    try:
        require_local_auth(request)
        await get_valid_token(force_refresh=False)
        return {"ok": True}
    except PermissionError:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Unauthorized"})
    except Exception:
        return JSONResponse(status_code=500, content={"ok": False, "error": "Unavailable"})


@app.api_route("/openai/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy(path: str, request: Request):
    try:
        require_local_auth(request)
        return await forward_request(request, path)
    except PermissionError:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    except Exception:
        log.exception("Proxy error")
        return JSONResponse(status_code=502, content={"error": "Bad gateway"})
