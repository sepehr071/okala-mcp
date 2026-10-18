"""Optional Okala login: phone + SMS code (OTP), token kept in a local file, refreshed on HTTP 401.

Without a token every tool still works as a guest; with one, ok_search uses Okala's own text search.
The token is only used to read: no tool writes to the cart, orders or account.

Two ways in, sharing `request_otp` / `verify_otp`:
- chat: the ok_login / ok_login_verify tools (account.py), the normal path for MCP users;
- CLI, two steps so it also works without an interactive terminal:
    okala-mcp login 09121234567          # sends the SMS code
    okala-mcp login 09121234567 12345    # verifies it and saves the token
    okala-mcp logout                     # deletes the saved token
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import httpx

BASE = "https://apigateway.okala.com/api"
# Public constants of the web client (same for every user), from the site's JS bundle.
CLIENT = {"client_id": "customer_client_id", "client_secret": "u_M{'57j!%LI21#", "client_name": "customer_client_name"}
DEVICE_TYPE_CODE = 10  # desktop web
PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


class AuthError(ValueError):
    """Okala refused a login step; the message is meant for the user."""


def token_path() -> Path:
    return Path(os.environ.get("OKALA_MCP_TOKEN_FILE") or Path.home() / ".okala-mcp" / "token.json")


def load() -> dict[str, Any] | None:
    """The saved login, or OKALA_MCP_TOKEN (access token only, no refresh)."""
    if os.environ.get("OKALA_MCP_TOKEN"):
        return {"access_token": os.environ["OKALA_MCP_TOKEN"]}
    try:
        data = json.loads(token_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("access_token") else None


def save(data: dict[str, Any]) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = {k: data[k] for k in ("access_token", "refresh_token", "expires_in", "mobile") if data.get(k) is not None}
    # Created 0600 from the start (no readable window); the chmod covers a file that already existed.
    # On Windows the mode is not enforced: the file is plain text in the user's profile.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        f.write(json.dumps(keep))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def forget() -> None:
    token_path().unlink(missing_ok=True)


def mobile_number(raw: str) -> str:
    """'۰۹۱۲ ۱۲۳ ۴۵۶۷', '+98 912...', '912...' -> '09121234567'."""
    digits = re.sub(r"\D", "", raw.translate(PERSIAN_DIGITS))
    if digits.startswith("98"):
        digits = "0" + digits[2:]
    elif digits.startswith("9"):
        digits = "0" + digits
    if not re.fullmatch(r"09\d{9}", digits):
        raise AuthError(f"Not an Iranian mobile number: {raw!r} (expected 09xxxxxxxxx).")
    return digits


def masked(mobile: str | None) -> str | None:
    return f"{mobile[:4]}***{mobile[-4:]}" if mobile else None


def _form(**fields: Any) -> dict[str, Any]:
    return {**CLIENT, "scope": "offline_access", **fields}


def _json(r: httpx.Response) -> dict[str, Any]:
    try:
        body = r.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def request_otp(client: httpx.AsyncClient, mobile: str) -> None:
    """Ask Okala to SMS a login code to `mobile` (Okala allows a resend after about 2 minutes)."""
    r = await client.post(
        f"{BASE}/voyager/C/CustomerAccount/OTPRegister",
        json={
            "mobile": mobile,
            "deviceTypeCode": DEVICE_TYPE_CODE,
            "confirmTerms": True,
            "notRobot": False,
            "otpType": 0,
            "ValidationCodeCreateReason": 5,
            "OtpApp": 0,
            "IsAppOnly": False,
        },
        headers={"X-Skip-Authorization": "true"},
    )
    body = _json(r)
    if r.status_code != 200 or not body.get("success"):
        raise AuthError(f"Okala refused to send the code (HTTP {r.status_code}): {body.get('message') or r.text[:200]}")


async def verify_otp(client: httpx.AsyncClient, mobile: str, code: str) -> dict[str, Any]:
    """Exchange the SMS code for a token pair and save it; returns the saved login."""
    code = re.sub(r"\D", "", code.translate(PERSIAN_DIGITS))
    if not code:
        raise AuthError("The SMS code should be digits, e.g. 12345.")
    r = await client.post(
        f"{BASE}/v1/accounts/tokens",
        data=_form(
            grant_type="customer_grant_type",
            mobile_number=mobile,
            otp_code=code,
            device_type_code=DEVICE_TYPE_CODE,
        ),
        headers={"X-Skip-Authorization": "true"},
    )
    body = _json(r)
    if r.status_code != 200 or not body.get("access_token"):
        msg = body.get("error_description") or body.get("message") or body.get("error") or r.text[:200]
        raise AuthError(f"Login failed (HTTP {r.status_code}): {msg}. Check the code, or ask for a new one.")
    login = {**body, "mobile": mobile}
    save(login)
    return login


async def refresh(client: httpx.AsyncClient, current: dict[str, Any]) -> dict[str, Any] | None:
    """New token pair from the refresh token; None when there is none or Okala refuses it."""
    if not current.get("refresh_token"):
        return None
    try:
        r = await client.post(
            f"{BASE}/v1/accounts/tokens",
            data=_form(grant_type="refresh_token", refresh_token=current["refresh_token"]),
            headers={"X-Skip-Authorization": "true"},
        )
    except httpx.HTTPError:
        return None
    new = _json(r) if r.status_code == 200 else {}
    if not new.get("access_token"):
        return None
    new = {
        **new,
        "refresh_token": new.get("refresh_token") or current["refresh_token"],
        "mobile": current.get("mobile"),
    }
    if not os.environ.get("OKALA_MCP_TOKEN"):
        save(new)
    return new


async def _cli(args: list[str]) -> str:
    mobile = mobile_number(args[1])
    from . import http  # late: http imports auth

    c = http.client()  # same Chrome-like client as the tools
    if len(args) == 2:
        await request_otp(c, mobile)
        return f"SMS code sent to {mobile}. Now run: okala-mcp login {mobile} <code>"
    await verify_otp(c, mobile, args[2])
    return f"Logged in. Token saved to {token_path()}; ok_search now uses Okala's text search."


def cli(args: list[str]) -> None:
    """`login <phone> [code]` / `logout`."""
    if args[:1] == ["logout"]:
        forget()
        print(f"Logged out (deleted {token_path()}).")
        return
    if len(args) not in (2, 3):
        raise SystemExit("Usage: okala-mcp login <phone> [sms-code] | okala-mcp logout")
    try:
        print(asyncio.run(_cli(args)))
    except (AuthError, httpx.HTTPError) as e:
        raise SystemExit(str(e) or type(e).__name__) from None


if __name__ == "__main__":
    cli(sys.argv[1:])
