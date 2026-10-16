import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from conftest import POINT, fixture
from mcp import Client, types

from okala_mcp import account
from okala_mcp.server import mcp

pytestmark = pytest.mark.anyio

OTP = "/api/voyager/C/CustomerAccount/OTPRegister"
TOKENS = "/api/v1/accounts/tokens"
TEXT = "/api/unicorn/v2/cumulative/search/nearby"
PHONE = "۰۹۱۲ ۱۲۳ ۴۵۶۷"  # Persian digits and spaces are fine


def okala(api):
    """Fake OTP + token endpoints: the code 12345 is right, anything else is refused."""

    def tokens(request):
        if b"otp_code=12345" in request.content:
            return httpx.Response(200, json={"access_token": "t1", "refresh_token": "r1", "expires_in": 3600})
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "کد وارد شده صحیح نیست"})

    api[OTP] = {"success": True, "message": None}
    api[TOKENS] = tokens
    api[TEXT] = fixture("text_search.json")


def saved() -> dict:
    return json.loads(Path(os.environ["OKALA_MCP_TOKEN_FILE"]).read_text())


async def test_login_in_two_steps(client, api):
    okala(api)
    out = (await client.call_tool("ok_login", {"phone": PHONE})).structured_content
    assert out["status"] == "code_sent" and out["phone"] == "0912***4567" and "ok_login_verify" in out["next"]
    body = json.loads(api.calls[0].content)
    assert body["mobile"] == "09121234567" and body["otpType"] == 0 and body["deviceTypeCode"] == 10

    again = (await client.call_tool("ok_login", {"phone": "09121234567"})).structured_content
    assert again["status"] == "code_sent" and 100 < again["new_code_possible_in_s"] <= 120
    assert len(api.calls) == 1  # no second SMS within 2 minutes

    wrong = await client.call_tool("ok_login_verify", {"phone": PHONE, "code": "11111"})
    assert wrong.is_error and "کد وارد شده صحیح نیست" in wrong.content[0].text

    out = (await client.call_tool("ok_login_verify", {"phone": PHONE, "code": "۱۲۳۴۵"})).structured_content
    assert out["logged_in"] is True and "t1" not in json.dumps(out)  # the token never reaches the chat
    assert saved() == {"access_token": "t1", "refresh_token": "r1", "expires_in": 3600, "mobile": "09121234567"}

    search = (await client.call_tool("ok_search", {"query": "نوتلا", **POINT})).structured_content
    assert search["mode"] == "text" and api.calls[-1].headers["Authorization"] == "Bearer t1"

    status = (await client.call_tool("ok_account", {})).structured_content
    assert status["logged_in"] is True and status["phone"] == "0912***4567"

    out = (await client.call_tool("ok_logout", {})).structured_content
    assert out["logged_in"] is False and not Path(os.environ["OKALA_MCP_TOKEN_FILE"]).exists()
    assert (await client.call_tool("ok_account", {})).structured_content["logged_in"] is False


async def test_sms_cap_across_numbers(client, api):
    okala(api)
    for n in range(3):
        await client.call_tool("ok_login", {"phone": f"0912123456{n}"})
    over = await client.call_tool("ok_login", {"phone": "09129999999"})
    assert over.is_error and "At most 3" in over.content[0].text
    assert len(api.calls) == 3


async def test_sms_cap_counts_resends_to_one_number(client, api):
    okala(api)
    for _ in range(3):
        await client.call_tool("ok_login", {"phone": PHONE})
        account._sent.clear()  # pretend the 2-minute resend timer ran out
    over = await client.call_tool("ok_login", {"phone": PHONE})
    assert over.is_error and "At most 3" in over.content[0].text
    assert len(api.calls) == 3


async def test_sms_cap_holds_under_parallel_calls(monkeypatch):
    sent = []

    async def slow_otp(client, mobile):
        await asyncio.sleep(0)  # yield, as a real request would
        sent.append(mobile)

    monkeypatch.setattr(account.auth, "request_otp", slow_otp)
    results = await asyncio.gather(*(account._send_code(f"0912000000{n}") for n in range(6)), return_exceptions=True)
    assert len(sent) == 3 and sum(isinstance(r, account.ApiError) for r in results) == 3


async def test_login_with_a_form(api):
    okala(api)
    asked = []

    async def answer(context, params):
        asked.append(params.message)
        return types.ElicitResult(action="accept", content={"code": "12345"})

    async with Client(mcp, raise_exceptions=True, elicitation_callback=answer) as c:
        out = json.loads((await c.call_tool("ok_login", {"phone": PHONE})).content[0].text)
    assert out["logged_in"] is True and "0912***4567" in asked[0] and saved()["access_token"] == "t1"


async def test_closed_form_leaves_the_chat_flow(api):
    okala(api)

    async def decline(context, params):
        return types.ElicitResult(action="cancel")

    async with Client(mcp, raise_exceptions=True, elicitation_callback=decline) as c:
        out = json.loads((await c.call_tool("ok_login", {"phone": PHONE})).content[0].text)
    assert out["status"] == "code_sent" and "closed the form" in out["next"]
    assert not Path(os.environ["OKALA_MCP_TOKEN_FILE"]).exists()


async def test_bad_phone_and_refused_sms(client, api):
    result = await client.call_tool("ok_login", {"phone": "0212345678901"})
    assert result.is_error and "Not an Iranian mobile number" in result.content[0].text and not api.calls

    api[OTP] = {"success": False, "message": "تعداد درخواست بیش از حد مجاز"}
    result = await client.call_tool("ok_login", {"phone": PHONE})
    assert result.is_error and "تعداد درخواست بیش از حد مجاز" in result.content[0].text


async def test_guest_search_suggests_login(client, api):
    api["/api/unicorn/v3/categories/nearby"] = fixture("cats_nearby.json")
    api["/api/unicorn/v2/products/nearby"] = fixture("products_milk.json")
    out = (await client.call_tool("ok_search", {"query": "شیر", **POINT})).structured_content
    assert out["mode"] == "category" and "ok_login" in out["tip"]
