import asyncio
import json
import re
import sys
from pathlib import Path

import httpx
import pytest
from mcp import Client

from okala_mcp import http
from okala_mcp.server import mcp

pytestmark = pytest.mark.anyio

BRANDS = "/api/azarakhsh/v1/Brand/GetPopularBrands"


async def test_all_tools_are_read_only(client):
    tools = (await client.list_tools()).tools
    assert len(tools) == 16
    login_tools = {"ok_login", "ok_login_verify", "ok_logout"}  # send an SMS / change the saved login
    for t in tools:
        assert t.name.startswith("ok_"), t.name
        assert t.annotations.read_only_hint is (t.name not in login_tools), t.name
        assert t.annotations.destructive_hint is (t.name == "ok_logout"), t.name  # it deletes the saved login
        assert t.description and t.title, t.name
        assert "title" not in t.input_schema, t.name  # pydantic's schema noise is stripped
        assert all("title" not in p for p in t.input_schema["properties"].values()), t.name


async def test_one_text_block_and_no_output_schema():
    """The real client path: one compact TextContent, no structuredContent, no outputSchema (tokens)."""
    http.set_transport(httpx.MockTransport(lambda request: httpx.Response(200, json=[])))
    async with Client(mcp, raise_exceptions=True) as c:
        assert all(t.output_schema is None for t in (await c.list_tools()).tools)
        result = await c.call_tool("ok_brands", {})
    assert len(result.content) == 1 and result.content[0].type == "text"
    assert result.structured_content is None and json.loads(result.content[0].text) == {"brands": []}


async def test_readme_tool_tables_match_the_server(client):
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    listed = re.findall(r"^\| `(ok_\w+)` \|", readme, re.M)
    assert sorted(listed) == sorted(t.name for t in (await client.list_tools()).tools)


async def test_headers_sent_on_every_call(client, api):
    api[BRANDS] = []
    await client.call_tool("ok_brands", {})
    await client.call_tool("ok_brands", {})
    first, second = api.calls[0].headers, api.calls[1].headers
    assert first["source"] == "okala" and first["Origin"] == "https://www.okala.com"
    assert first["sec-fetch-site"] == "same-site" and first["Accept-Language"].startswith("fa-IR")
    assert re.fullmatch(r"[0-9a-f-]{36}", first["x-user-unique-id"])
    assert first["x-user-unique-id"] == second["x-user-unique-id"]  # one id per process


async def test_dropped_connection_is_retried(client, api):
    attempts = []

    def flaky(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=request)
        return httpx.Response(200, json=[])

    api[BRANDS] = flaky
    result = await client.call_tool("ok_brands", {})
    assert not result.is_error and len(attempts) == 2


async def test_gateway_error_is_retried_once_but_not_a_404(client, api):
    answers = iter([503, 200])
    api[BRANDS] = lambda request: httpx.Response(next(answers), json=[])
    assert not (await client.call_tool("ok_brands", {})).is_error and len(api.calls) == 2

    api.calls.clear()
    api[BRANDS] = lambda request: httpx.Response(404)
    assert (await client.call_tool("ok_brands", {})).is_error and len(api.calls) == 1


async def test_parallel_401s_refresh_the_token_once(api):
    api[BRANDS] = lambda r: (
        httpx.Response(200, json=[]) if r.headers["Authorization"] == "Bearer new" else httpx.Response(401)
    )
    api["/api/v1/accounts/tokens"] = {"access_token": "new", "refresh_token": "r2"}
    http.use_login({"access_token": "old", "refresh_token": "r1"})
    await asyncio.gather(
        http.api("/azarakhsh/v1/Brand/GetPopularBrands"), http.api("/azarakhsh/v1/Brand/GetPopularBrands")
    )
    assert [c.url.path for c in api.calls].count("/api/v1/accounts/tokens") == 1


async def test_network_error_after_retry(client, api):
    def down(request):
        raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]", request=request)

    api[BRANDS] = down
    result = await client.call_tool("ok_brands", {})
    assert result.is_error and "Could not reach okala.com (ConnectError)" in result.content[0].text
    assert len(api.calls) == 3


async def test_timeout_is_retried_twice(client, api):
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    api[BRANDS] = slow
    result = await client.call_tool("ok_brands", {})
    assert result.is_error and "did not answer in time" in result.content[0].text and len(api.calls) == 3


async def test_403_is_not_retried(client, api):
    api[BRANDS] = lambda r: httpx.Response(403, text="<html>Request Rejected</html>")
    assert (await client.call_tool("ok_brands", {})).is_error and len(api.calls) == 1


def test_is_challenge():
    html = {"content-type": "text/html"}
    assert http._is_challenge(httpx.Response(403, text="<html>Just a moment...</html>", headers=html))
    assert http._is_challenge(httpx.Response(403, text="<html>The requested URL was rejected.</html>", headers=html))
    assert not http._is_challenge(httpx.Response(403, text="<html>Forbidden</html>", headers=html))
    assert not http._is_challenge(httpx.Response(403, json={"x": 1}))
    assert not http._is_challenge(httpx.Response(200, text="<html>Just a moment</html>", headers=html))


async def test_proxy_list_rotates_and_stale_client_does_not(monkeypatch, api):
    monkeypatch.setenv("OKALA_MCP_PROXY", "http://p1:1, http://p2:2")
    assert http._proxies() == ["http://p1:1", "http://p2:2"]
    old, _ = http._get_client()
    http._next_proxy(old)
    new, _ = http._get_client()
    assert http._proxy_index == 1 and new is not old
    http._next_proxy(old)  # a late failure on the retired client must not skip the proxy now in use
    assert http._proxy_index == 1 and http._client is new
    monkeypatch.setenv("OKALA_MCP_PROXY", "http://p1:1")
    http._next_proxy(new)  # a single proxy never rotates
    assert http._client is new


async def test_challenge_cleared_by_browser_once(monkeypatch, api):
    runs = []

    async def fake_browser(proxy):
        runs.append(proxy)
        await asyncio.sleep(0.01)
        return [{"name": "clearance", "value": "ok", "domain": ".okala.com", "path": "/"}]

    monkeypatch.setattr(http, "_browser_cookies", fake_browser)

    def guarded(request):
        if "clearance=ok" not in request.headers.get("cookie", ""):
            return httpx.Response(403, text="<html>Just a moment</html>", headers={"content-type": "text/html"})
        return httpx.Response(200, json=[])

    api[BRANDS] = guarded
    await asyncio.gather(
        http.api("/azarakhsh/v1/Brand/GetPopularBrands"), http.api("/azarakhsh/v1/Brand/GetPopularBrands")
    )
    assert len(runs) == 1  # the second caller reused the cookies the first one fetched


async def test_challenge_without_patchright(monkeypatch, client, api):
    monkeypatch.setitem(sys.modules, "patchright.async_api", None)  # makes the import fail
    api[BRANDS] = lambda r: httpx.Response(
        403, text="<html>Just a moment</html>", headers={"content-type": "text/html"}
    )
    result = await client.call_tool("ok_brands", {})
    assert result.is_error and "okala-mcp[browser]" in result.content[0].text


async def test_retry_after_is_honoured_and_capped(monkeypatch, client, api):
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    monkeypatch.setattr(http.asyncio, "sleep", fake_sleep)
    answers = iter([("3",), ("99",)])
    hits = []

    def limited(request):
        hits.append(1)
        if len(hits) <= 2:
            return httpx.Response(429, json=[], headers={"Retry-After": next(answers)[0]})
        return httpx.Response(200, json=[])

    api[BRANDS] = limited
    assert not (await client.call_tool("ok_brands", {})).is_error
    assert [w for w in waits if w] == [3.0, 10]


@pytest.mark.parametrize(
    ("status", "text"),
    [
        (404, "HTTP 404"),
        (429, "rate limiting"),
        (500, "server error (HTTP 500)"),
        (403, "blocked"),
        (401, "logged-in Okala account"),
    ],
)
async def test_http_errors_are_actionable(client, api, status, text):
    api[BRANDS] = lambda r: httpx.Response(status)  # login-only and unrouted paths answer with an empty body
    result = await client.call_tool("ok_brands", {})
    assert result.is_error and text in result.content[0].text


async def test_problem_json_errors_are_passed_on(client, api):
    body = {
        "type": "https://tools.ietf.org/html/rfc9110#section-15.5.1",
        "title": "One or more validation errors occurred.",
        "status": 400,
        "errors": {"Latitude": ["The value 'abc' is not valid."]},
    }
    api[BRANDS] = lambda r: httpx.Response(400, json=body, headers={"content-type": "application/problem+json"})
    result = await client.call_tool("ok_brands", {})
    text = result.content[0].text
    assert result.is_error and "HTTP 400" in text
    assert "One or more validation errors occurred.; Latitude: The value 'abc' is not valid." in text


async def test_other_error_shapes(client, api):
    api[BRANDS] = lambda r: httpx.Response(400, json="شناسه شهر نامعتبر است")  # titanic: a bare JSON string
    assert "شناسه شهر نامعتبر است" in (await client.call_tool("ok_brands", {})).content[0].text

    asp_result = {"Value": {"Code": "SYSTEM_EXCEPTION", "CorrelationId": "", "Message": "Brand"}, "StatusCode": 400}
    api[BRANDS] = lambda r: httpx.Response(400, json=asp_result)
    assert "SYSTEM_EXCEPTION: Brand" in (await client.call_tool("ok_brands", {})).content[0].text

    api[BRANDS] = lambda r: httpx.Response(200, text="<html>oops</html>")
    result = await client.call_tool("ok_brands", {})
    assert result.is_error and "non-JSON" in result.content[0].text


@pytest.mark.parametrize(
    ("body", "text"),
    [
        ({"data": None, "success": False, "tags": [{"key": "nullCategory", "value": True}]}, "unknown category slug"),
        ({"data": None, "success": False, "tags": [{"key": "stores", "value": 0}]}, "no Okala store delivers"),
        ({"data": None, "success": False, "tags": None, "errorMessage": None}, "unknown id or slug"),
        ({"success": False, "data": None, "message": "درخواست با خطا مواجه شد."}, "درخواست با خطا مواجه شد."),
        ({"data": None, "isSuccess": False, "message": "bad", "statusCode": 400}, "bad"),
    ],
)
async def test_success_false_bodies(client, api, body, text):
    api[BRANDS] = body
    result = await client.call_tool("ok_brands", {})
    assert result.is_error and text in result.content[0].text


async def test_204_is_none(client, api):
    from okala_mcp.http import api as call

    api["/api/titanic/v1/Announcement"] = lambda r: httpx.Response(204)
    assert await call("/titanic/v1/Announcement", {"cityId": 129}) is None


async def test_list_params_repeat(api):
    from okala_mcp.http import api as call

    api["/api/carousel/v4/offers"] = {"carousels": [], "success": True}
    await call("/carousel/v4/offers", {"storeIds": [1, 2], "skip": None})
    assert str(api.calls[0].url.query, "ascii") == "storeIds=1&storeIds=2"
