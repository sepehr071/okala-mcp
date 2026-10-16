"""Shared async HTTP client for the Okala API gateway (apigateway.okala.com/api).

Every tool goes through `api`, which returns the parsed JSON body as-is: the envelope differs per
service (opex `{success, data}`, review `{isSuccess, data}`, carousel own top level, bare objects and
arrays for pdp / brands / ParsiMap). `_send` caps concurrency, retries a GET once (dropped connection,
HTTP 502/503/504, after a short fixed pause), and turns HTTP failures and `success: false` bodies into `ToolError` messages the model
can act on.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from . import auth

BASE = "https://apigateway.okala.com/api"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0",
    "Accept": "application/json",
    "source": "okala",  # required by titanic (announcements), harmless elsewhere
    "x-user-unique-id": str(uuid.uuid4()),
}

MAX_CONCURRENCY = 4
RETRY_DELAY = 0.5  # seconds before the one retry of a GET
RETRY_STATUS = (502, 503, 504)

# Reasons the unicorn lists give in `tags` when they answer HTTP 200 with `success: false`.
TAG_REASONS = {
    "nullCategory": "unknown category slug (get slugs from ok_categories)",
    "hitsEs": "no products found (unknown store id, or the store has nothing in this category)",
    "stores": "no Okala store delivers to this point",
}

_transport: httpx.AsyncBaseTransport | None = None
_client: httpx.AsyncClient | None = None
_limit: asyncio.Semaphore | None = None
_refresh_lock: asyncio.Lock | None = None


class ApiError(ToolError):
    """A failed upstream call."""


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client and the loaded token."""
    global _transport, _client, _limit, _refresh_lock, _token
    _transport, _client, _limit, _refresh_lock, _token = transport, None, None, None, _UNSET


_UNSET: Any = object()
_token: dict[str, Any] | None = _UNSET


def token() -> dict[str, Any] | None:
    """The saved login (see auth.py), loaded once per process; None = guest."""
    global _token
    if _token is _UNSET:
        _token = auth.load()
    return _token


def logged_in() -> bool:
    return token() is not None


def use_login(login: dict[str, Any] | None) -> None:
    """Switch this process to a new login (after ok_login) or to guest (None, after ok_logout)."""
    global _token
    _token = login


def client() -> httpx.AsyncClient:
    """The shared client, for the login calls (POSTs that do not go through `api`)."""
    return _get_client()[0]


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit, _refresh_lock
    if _client is None:
        # Okala answers direct connections fine; a proxy is only used when the user opts in
        # with OKALA_MCP_PROXY.
        _client = httpx.AsyncClient(
            transport=_transport,
            headers=HEADERS,
            timeout=30,
            follow_redirects=True,
            trust_env=False,
            proxy=os.environ.get("OKALA_MCP_PROXY") or None,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
        _refresh_lock = asyncio.Lock()
    assert _limit is not None
    return _client, _limit


async def api(path: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any:
    """GET a gateway path (`/opex/v4/stores/nearby`, ...) and return the JSON body; None for HTTP 204.

    List values in `params` become repeated query params (`StoreIds=1&StoreIds=2`).
    """
    params = {k: v for k, v in (params or {}).items() if v is not None}
    r = await _send("GET", f"{BASE}{path}", params=params, headers=headers)
    if r.status_code == 204:  # brand without a landing page, no announcement
        return None
    try:
        body = r.json()
    except ValueError as e:
        raise ApiError(f"okala.com returned a non-JSON response for {path} (HTTP {r.status_code}).") from e
    if isinstance(body, dict) and (body.get("success") is False or body.get("isSuccess") is False):
        raise ApiError(f"okala.com found nothing for this request: {_reason(body)}")
    return body


async def _send(method: str, url: str, **kwargs: Any) -> httpx.Response:
    global _token
    r = await _request(method, url, **kwargs)
    if r.status_code == 401 and token():
        # Expired access token: refresh once; if Okala refuses, carry on as a guest for this process.
        # Parallel calls can all see the 401: the first one refreshes, the others reuse its new token.
        client, _ = _get_client()
        stale = token()
        assert _refresh_lock is not None
        async with _refresh_lock:
            if token() is stale:
                _token = await auth.refresh(client, stale or {})
        r = await _request(method, url, **kwargs)
    if r.status_code >= 400:
        detail = ""
        try:
            detail = _errors(r.json())
        except ValueError:
            pass
        raise ApiError(_status_message(r.status_code) + (f" Site says: {detail}" if detail else ""))
    return r


async def _request(method: str, url: str, headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
    client, limit = _get_client()
    if t := token():
        headers = {**(headers or {}), "Authorization": f"Bearer {t['access_token']}"}
    for attempt in (1, 2):
        try:
            async with limit:
                r = await client.request(method, url, headers=headers, **kwargs)
        except httpx.TimeoutException as e:
            raise ApiError("okala.com did not answer in time. Try again in a moment.") from e
        except httpx.TransportError as e:
            if attempt == 2 or method != "GET":
                raise ApiError(
                    f"Could not reach okala.com ({type(e).__name__}). Check the internet connection, "
                    "or set OKALA_MCP_PROXY."
                ) from e
        else:
            if attempt == 2 or method != "GET" or r.status_code not in RETRY_STATUS:
                return r
        await asyncio.sleep(RETRY_DELAY)
    raise AssertionError("unreachable")


def _reason(body: dict[str, Any]) -> str:
    """Why a `success: false` body failed: its message, or the unicorn `tags` turned into words."""
    if body.get("errorMessage") or body.get("message"):
        return str(body.get("errorMessage") or body.get("message"))[:300]
    reasons = [
        TAG_REASONS[t["key"]] for t in body.get("tags") or [] if isinstance(t, dict) and t.get("key") in TAG_REASONS
    ]
    return "; ".join(reasons) or "unknown id or slug, or nothing near this point"


def _errors(body: Any) -> str:
    """Error text of the gateway's error shapes: problem+json (`title`, `errors`, `detail`), a bare JSON
    string (titanic), or a serialized ASP.NET result (`{"Value": {"Code", "Message"}}`)."""
    if isinstance(body, str):
        return body[:300]
    if not isinstance(body, dict):
        return ""
    if isinstance(body.get("Value"), dict):
        value = body["Value"]
        return f"{value.get('Code')}: {value.get('Message')}"[:300]
    parts = [str(body[k]) for k in ("title", "detail") if body.get(k) and body.get(k) != "Not Found"]
    errors = body.get("errors")
    if isinstance(errors, dict):
        parts += [f"{field}: {' '.join(map(str, msgs))}" for field, msgs in errors.items()]
    return "; ".join(parts)[:300]


def _status_message(code: int) -> str:
    if code == 400:
        return "okala.com rejected the parameters (HTTP 400)."
    if code == 401:
        return "This needs a logged-in Okala account (HTTP 401). Log in once in a terminal: `okala-mcp login <phone>`."
    if code == 403:
        return "okala.com blocked the request (HTTP 403). Turn off VPN/proxy or set OKALA_MCP_PROXY."
    if code == 404:
        return "Not found on okala.com (HTTP 404). Check the product id and store id."
    if code == 429:
        return "okala.com is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if code >= 500:
        return f"okala.com had a server error (HTTP {code}). Try again later."
    return f"okala.com rejected the request (HTTP {code})."
