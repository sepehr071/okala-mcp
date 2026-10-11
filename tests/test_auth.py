import json
import os
from pathlib import Path

import httpx
import pytest
from conftest import LAT, LON, POINT, fixture

from okala_mcp import auth

pytestmark = pytest.mark.anyio

TEXT = "/api/unicorn/v2/cumulative/search/nearby"
STORE_TEXT = "/api/unicorn/v2/search/store/11009"
TOKENS = "/api/v1/accounts/tokens"


@pytest.fixture
def login():
    """A saved login in the per-test token file (conftest points OKALA_MCP_TOKEN_FILE at tmp_path)."""
    auth.save({"access_token": "old", "refresh_token": "r1", "expires_in": 3600, "UserInfo": {"x": 1}})
    return Path(os.environ["OKALA_MCP_TOKEN_FILE"])


async def test_text_search_near_a_point(client, api, login):
    api[TEXT] = fixture("text_search.json")
    out = (await client.call_tool("ok_search", {"query": "نوتلا", **POINT})).structured_content
    assert api.params() == {"q": "نوتلا", "lat": str(LAT), "lon": str(LON)}
    assert api.calls[0].headers["Authorization"] == "Bearer old"
    assert out["mode"] == "text" and out["stores"] == 2  # the closed third store is left out
    assert out["suggestions"][0] == "شکلات صبحانه نوتلا"
    assert {p["store_id"] for p in out["products"]} == {52514, 9986}
    assert out["products"][0]["name"].count("نوتلا") and out["products"][0]["final_price"] < 10_000_000  # Toman
    assert "categories" not in out


async def test_text_search_in_one_store(client, api, login):
    api[STORE_TEXT] = fixture("store_search.json")
    out = (await client.call_tool("ok_search", {"query": "پنیر", "store_id": 11009})).structured_content
    assert api.params() == {"q": "پنیر", "s": "3"}
    assert out["mode"] == "text" and out["total"] == 4 and all(p["store_id"] == 11009 for p in out["products"])
    prices = [p["final_price"] for p in out["products"] if "پنیر" in p["name"]]
    assert prices == sorted(prices)


async def test_guest_store_search_points_to_login(client, api):
    result = await client.call_tool("ok_search", {"query": "پنیر", "store_id": 11009})
    assert result.is_error and "okala-mcp login" in result.content[0].text and not api.calls
    result = await client.call_tool("ok_search", {"query": "پنیر"})
    assert result.is_error and "lat + lon" in result.content[0].text


async def test_expired_token_is_refreshed_once(client, api, login):
    def search(request):
        ok = request.headers["Authorization"] == "Bearer new"
        return httpx.Response(200, json=fixture("text_search.json")) if ok else httpx.Response(401)

    def tokens(request):
        assert b"grant_type=refresh_token" in request.content and b"refresh_token=r1" in request.content
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2", "expires_in": 3600})

    api[TEXT], api[TOKENS] = search, tokens
    out = (await client.call_tool("ok_search", {"query": "نوتلا", **POINT})).structured_content
    assert out["mode"] == "text" and len(api.calls) == 3
    assert json.loads(login.read_text()) == {"access_token": "new", "refresh_token": "r2", "expires_in": 3600}


async def test_refused_refresh_falls_back_to_guest(client, api, login):
    def stores(request):  # public endpoint, but a dead token makes the gateway answer 401
        if "Authorization" in request.headers:
            return httpx.Response(401)
        return httpx.Response(200, json=fixture("nearby.json"))

    api["/api/opex/v4/stores/nearby"], api[TOKENS] = (
        stores,
        lambda r: httpx.Response(400, json={"error": "invalid_grant"}),
    )
    result = await client.call_tool("ok_stores", POINT)
    assert not result.is_error and len(api.calls) == 3
    assert "Authorization" not in api.calls[-1].headers


def test_mobile_numbers_are_normalised():
    assert auth.mobile_number("۰۹۱۲ ۱۲۳ ۴۵۶۷") == "09121234567"
    assert auth.mobile_number("+98 912 123 4567") == "09121234567"
    assert auth.mobile_number("9121234567") == "09121234567"
    with pytest.raises(auth.AuthError):
        auth.mobile_number("123")
    assert auth.masked("09121234567") == "0912***4567"


def test_env_token_wins(monkeypatch, login):
    monkeypatch.setenv("OKALA_MCP_TOKEN", "abc")
    assert auth.load() == {"access_token": "abc"}
    monkeypatch.delenv("OKALA_MCP_TOKEN")
    assert auth.load()["refresh_token"] == "r1"
    login.unlink()
    assert auth.load() is None
