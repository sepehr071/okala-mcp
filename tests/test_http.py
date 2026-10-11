import re
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.anyio

BRANDS = "/api/azarakhsh/v1/Brand/GetPopularBrands"


async def test_all_tools_are_read_only(client):
    tools = (await client.list_tools()).tools
    assert len(tools) == 16
    login_tools = {"ok_login", "ok_login_verify", "ok_logout"}  # send an SMS / change the saved login
    for t in tools:
        assert t.name.startswith("ok_"), t.name
        assert t.annotations.read_only_hint is (t.name not in login_tools), t.name
        assert t.annotations.destructive_hint is False, t.name
        assert t.description and t.title, t.name


async def test_readme_tool_tables_match_the_server(client):
    readme = (Path(__file__).parent.parent / "README.md").read_text(encoding="utf-8")
    listed = re.findall(r"^\| `(ok_\w+)` \|", readme, re.M)
    assert sorted(listed) == sorted(t.name for t in (await client.list_tools()).tools)


async def test_headers_sent_on_every_call(client, api):
    api[BRANDS] = []
    await client.call_tool("ok_brands", {})
    await client.call_tool("ok_brands", {})
    first, second = api.calls[0].headers, api.calls[1].headers
    assert first["source"] == "okala" and "Chrome" in first["User-Agent"]
    assert re.fullmatch(r"[0-9a-f-]{36}", first["x-user-unique-id"])
    assert first["x-user-unique-id"] == second["x-user-unique-id"]  # one id per process


async def test_dropped_connection_is_retried_once(client, api):
    attempts = []

    def flaky(request):
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.", request=request)
        return httpx.Response(200, json=[])

    api[BRANDS] = flaky
    result = await client.call_tool("ok_brands", {})
    assert not result.is_error and len(attempts) == 2


async def test_network_error_after_retry(client, api):
    def down(request):
        raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING]", request=request)

    api[BRANDS] = down
    result = await client.call_tool("ok_brands", {})
    assert result.is_error and "Could not reach okala.com (ConnectError)" in result.content[0].text
    assert len(api.calls) == 2


async def test_timeout_is_not_retried(client, api):
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    api[BRANDS] = slow
    result = await client.call_tool("ok_brands", {})
    assert result.is_error and "did not answer in time" in result.content[0].text and len(api.calls) == 1


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
