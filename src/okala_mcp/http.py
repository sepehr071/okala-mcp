"""Shared async HTTP client for the Okala API gateway (apigateway.okala.com/api).

Every tool goes through `api`, which returns the parsed JSON body as-is: the envelope differs per
service (opex `{success, data}`, review `{isSuccess, data}`, carousel own top level, bare objects and
arrays for pdp / brands / ParsiMap). `_send` caps concurrency, retries a GET (dropped connection, timeout,
HTTP 429/5xx, with jittered backoff), passes a bot-check page through a real browser when one is installed,
and turns HTTP failures and `success: false` bodies into `ToolError` messages the model can act on.
"""

from __future__ import annotations

import asyncio
import os
import random
import re
import time
import uuid
from typing import Any

import httpx
from httpx_curl_cffi import AsyncCurlTransport
from mcp.server.mcpserver.exceptions import ToolError

from . import auth

BASE = "https://apigateway.okala.com/api"
SITE = "https://www.okala.com"

HEADERS = {
    # User-Agent, sec-ch-ua and TLS come from the impersonated Chrome profile (see _get_client).
    "Accept": "application/json",
    "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
    "Origin": SITE,
    "Referer": f"{SITE}/",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",  # www.okala.com -> apigateway.okala.com
    "priority": "u=1, i",
    "source": "okala",  # required by titanic (announcements), harmless elsewhere
    "ui-version": "2.0",  # sent by the web app on every call
    "x-user-unique-id": str(uuid.uuid4()),
}

MAX_CONCURRENCY = 2
JITTER = (0.15, 0.6)  # seconds before each call
MAX_RETRIES = 2
RETRY_DELAY = 0.5  # base of the exponential backoff, seconds
MAX_RETRY_WAIT = 10  # cap for Retry-After and backoff, seconds

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
_proxy_index = 0
_retired: set[asyncio.Task] = set()
_browser_lock: asyncio.Lock | None = None
_cleared: tuple[httpx.AsyncClient, float] | None = None


class _XhrTransport(AsyncCurlTransport):
    """The Chrome profile sends navigation-only headers; a None value makes curl drop them (API calls are XHR)."""

    def _create_request_params(self, req: httpx.Request) -> dict[str, Any]:
        p = super()._create_request_params(req)
        p["headers"] = [*p["headers"], (b"upgrade-insecure-requests", None), (b"sec-fetch-user", None)]
        return p


class ApiError(ToolError):
    """A failed upstream call."""


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """Swap the transport (tests use httpx.MockTransport). Drops the current client and the loaded token."""
    global _transport, _client, _limit, _refresh_lock, _token, _proxy_index, _browser_lock, _cleared
    _transport, _client, _limit, _refresh_lock, _token = transport, None, None, None, _UNSET
    _proxy_index, _browser_lock, _cleared = 0, None, None


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


def _proxies() -> list[str]:
    """OKALA_MCP_PROXY: one proxy URL, or a comma-separated list used one at a time."""
    return [p.strip() for p in os.environ.get("OKALA_MCP_PROXY", "").split(",") if p.strip()]


# Plain gateway error pages are not challenges; only a page with a known bot-check marker is.
_CHALLENGE = re.compile(
    r"just a moment|captcha|challenge|arvan|attention required|enable javascript|requested url was rejected", re.I
)


def _is_challenge(r: httpx.Response) -> bool:
    """A bot-check page: 403/429/503 with an HTML body that carries a known challenge marker."""
    return (
        r.status_code in (403, 429, 503)
        and "html" in r.headers.get("content-type", "") + r.text[:20].lower()
        and bool(_CHALLENGE.search(r.text[:5000]))
    )


def _next_proxy(client: httpx.AsyncClient) -> None:
    """Sticky pool: move to the next proxy after a block or connect error; the next call rebuilds the client.

    Only the client that failed may rotate: a slower request that failed on an old client must not skip a good proxy.
    The old client is closed late because other requests may still be using it.
    """
    global _client, _limit, _proxy_index
    if len(_proxies()) < 2 or client is not _client:
        return
    _client, _limit, _proxy_index = None, None, _proxy_index + 1
    t = asyncio.create_task(_close_later(client))
    _retired.add(t)
    t.add_done_callback(_retired.discard)


async def _close_later(client: httpx.AsyncClient) -> None:
    await asyncio.sleep(40)  # longer than the 30 s request timeout
    await client.aclose()


def _get_client() -> tuple[httpx.AsyncClient, asyncio.Semaphore]:
    global _client, _limit, _refresh_lock
    if _client is None:
        # Okala answers direct connections fine; a proxy is only used when the user opts in with
        # OKALA_MCP_PROXY. curl_cffi presents Chrome's TLS/HTTP2 fingerprint and default headers; cookies
        # (the gateway's TS01 cookie) and redirects stay with httpx. The proxy goes to the transport (a
        # client-level proxy would be ignored once a transport is given).
        proxies = _proxies()
        _client = httpx.AsyncClient(
            transport=_transport
            or _XhrTransport(
                impersonate="chrome",
                default_headers=True,
                proxy=proxies[_proxy_index % len(proxies)] if proxies else None,
                max_connections=2 * MAX_CONCURRENCY,
            ),
            headers=HEADERS,
            timeout=30,
            limits=httpx.Limits(max_connections=2 * MAX_CONCURRENCY),
            follow_redirects=True,
            trust_env=False,
        )
        _limit = asyncio.Semaphore(MAX_CONCURRENCY)
        _refresh_lock = _refresh_lock or asyncio.Lock()
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
    stale = token()  # the login this request goes out with
    r = await _request(method, url, **kwargs)
    if r.status_code == 401 and stale:
        # Expired access token: refresh once; if Okala refuses, carry on as a guest for this process.
        # Parallel calls can all see the 401: the first one refreshes, the others reuse its new token.
        client, _ = _get_client()
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
    if t := token():
        headers = {**(headers or {}), "Authorization": f"Bearer {t['access_token']}"}
    r = await _attempts(method, url, headers, **kwargs)
    if _is_challenge(r):
        _next_proxy(_get_client()[0])
        await _clear_challenge(_get_client()[0])
        r = await _attempts(method, url, headers, **kwargs)  # one retry; a second block is final
        if _is_challenge(r):
            _next_proxy(_get_client()[0])
    return r


async def _attempts(method: str, url: str, headers: dict[str, str] | None, **kwargs: Any) -> httpx.Response:
    """One call, with jitter; a GET is retried on timeout, network error, 429 and 5xx. 403 is never retried."""
    retries = MAX_RETRIES if method == "GET" else 0
    for attempt in range(retries + 1):
        client, limit = _get_client()
        wait = RETRY_DELAY * 2**attempt * random.uniform(1, 1.5)
        try:
            async with limit:
                await asyncio.sleep(random.uniform(*JITTER))
                r = await client.request(method, url, headers=headers, **kwargs)
        except httpx.TimeoutException as e:
            if attempt == retries:
                raise ApiError("okala.com did not answer in time. Try again in a moment.") from e
        except httpx.TransportError as e:
            if isinstance(e, httpx.ConnectError | httpx.ProxyError):
                _next_proxy(client)
            if attempt == retries:
                raise ApiError(
                    f"Could not reach okala.com ({type(e).__name__}). Check the internet connection, "
                    "or set OKALA_MCP_PROXY."
                ) from e
        else:
            if attempt == retries or not (r.status_code == 429 or r.status_code >= 500):
                return r
            ra = r.headers.get("retry-after", "")
            if r.status_code in (429, 503) and ra.replace(".", "", 1).isdigit():
                wait = float(ra)
        await asyncio.sleep(min(wait, MAX_RETRY_WAIT))
    raise AssertionError("unreachable")


async def _browser_cookies(proxy: str | None) -> list[dict[str, Any]]:
    """Open the home page in headless patchright, wait for the challenge to clear, return the cookies."""
    try:
        from patchright.async_api import async_playwright
    except ImportError as e:
        raise ApiError(
            "Okala served a bot-check page. Install the browser extra (`pip install okala-mcp[browser]`, or "
            "`uvx --with patchright==1.63.0 okala-mcp`) and run `patchright install chromium`."
        ) from e
    u = httpx.URL(proxy) if proxy else None
    opts = u and {"server": f"{u.scheme}://{u.host}:{u.port}", "username": u.username, "password": u.password}
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=opts)
        try:
            page = await browser.new_page()
            await page.goto(SITE, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_function(
                "!/just a moment|access denied|attention required|arvancloud/i.test(document.title)", timeout=30_000
            )
            return await browser.contexts[0].cookies()
        finally:
            await browser.close()


async def _clear_challenge(client: httpx.AsyncClient) -> None:
    """Rung 2: let a real browser pass the bot check once and share its cookies with the httpx jar."""
    global _browser_lock, _cleared
    _browser_lock = _browser_lock or asyncio.Lock()
    async with _browser_lock:  # one browser at a time; callers that waited reuse the cookies just fetched
        if _cleared and _cleared[0] is client and time.monotonic() - _cleared[1] < 30:
            return
        proxies = _proxies()
        try:
            cookies = await _browser_cookies(proxies[_proxy_index % len(proxies)] if proxies else None)
        except ApiError:
            raise
        except Exception as e:
            raise ApiError(f"Okala bot check was not cleared by the browser ({type(e).__name__}).") from e
        for c in cookies:
            client.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
        _cleared = (client, time.monotonic())


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
        return (
            "okala.com blocked the request (HTTP 403). Turn off VPN/proxy, or set OKALA_MCP_PROXY "
            "(a comma-separated list rotates on a block)."
        )
    if code == 404:
        return "Not found on okala.com (HTTP 404). Check the product id and store id."
    if code == 429:
        return "okala.com is rate limiting requests (HTTP 429). Wait a minute before retrying."
    if code >= 500:
        return f"okala.com had a server error (HTTP {code}). Try again later."
    return f"okala.com rejected the request (HTTP {code})."
