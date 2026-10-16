"""Optional login from the chat: ok_login (SMS code, asked with a form when the client supports it),
ok_login_verify (the code typed in the chat), ok_account, ok_logout. The token never appears in a reply."""

from __future__ import annotations

import os
import time
from typing import Annotated, Any

import httpx
from mcp.server.mcpserver import AcceptedElicitation, Context, Elicit, ElicitationResult, Resolve
from pydantic import BaseModel, Field

from . import auth, http
from .http import ApiError
from .registry import tool

Phone = Annotated[
    str,
    Field(min_length=10, max_length=20, description="The user's Iranian mobile number, e.g. '09121234567'."),
]
RESEND_SECONDS = 120  # Okala's own resend timer; also stops an agent from spamming someone's phone

MAX_SMS_PER_HOUR = 3  # process-wide cap on codes sent (any numbers), against an agent spraying SMS

_sent: dict[str, float] = {}  # mobile -> time the last code was sent
_sms_times: list[float] = []  # time of every code sent in the last hour


class SmsCode(BaseModel):
    code: str = Field(description="The login code Okala sent by SMS", min_length=4, max_length=8)


def _mobile(raw: str) -> str:
    try:
        return auth.mobile_number(raw)
    except auth.AuthError as e:
        raise ApiError(str(e)) from None


async def _verify(mobile: str, code: str) -> dict[str, Any]:
    try:
        login = await auth.verify_otp(http.client(), mobile, code)
    except auth.AuthError as e:
        raise ApiError(str(e)) from None
    except httpx.HTTPError as e:
        raise ApiError(f"Could not reach okala.com ({type(e).__name__}). Try again.") from e
    http.use_login(login)
    _sent.pop(mobile, None)
    return {
        "logged_in": True,
        "phone": auth.masked(mobile),
        "message": "Logged in. ok_search now uses Okala's own text search (also inside one store with store_id).",
    }


def _can_show_form(ctx: Context) -> bool:
    """The client declared form elicitation (a bare `elicitation: {}` counts, url-only does not)."""
    caps = ctx.client_capabilities
    elicitation = caps.elicitation if caps is not None else None
    return elicitation is not None and (elicitation.form is not None or elicitation.url is None)


async def _send_code(mobile: str) -> None:
    """SMS a code unless one went to this number in the last 2 minutes. The SDK may run the resolver
    below once per round (newer protocol: the client retries the call with the answer), so this guard
    is also what keeps a retry from sending a second SMS."""
    if time.monotonic() - _sent.get(mobile, -RESEND_SECONDS) < RESEND_SECONDS:
        return
    _sms_times[:] = [t for t in _sms_times if time.monotonic() - t <= 3600]
    if len(_sms_times) >= MAX_SMS_PER_HOUR:
        raise ApiError(f"At most {MAX_SMS_PER_HOUR} login codes can be sent per hour. Try again later.")
    # Recorded before the await so parallel calls cannot all pass the checks; undone if sending fails.
    now = _sent[mobile] = time.monotonic()
    _sms_times.append(now)
    try:
        await auth.request_otp(http.client(), mobile)
    except BaseException as e:
        _sms_times.remove(now)
        _sent.pop(mobile, None)
        if isinstance(e, auth.AuthError):
            raise ApiError(str(e)) from None
        if isinstance(e, httpx.HTTPError):
            raise ApiError(f"Could not reach okala.com ({type(e).__name__}). Try again.") from e
        raise


async def _sms_code(phone: str, ctx: Context) -> Elicit[SmsCode] | None:
    """Send the code, then ask for it with a form when the client can show one (None = ask in the chat)."""
    mobile = _mobile(phone)
    await _send_code(mobile)
    if not _can_show_form(ctx):
        return None
    return Elicit(f"Okala sent a login code by SMS to {auth.masked(mobile)}. Enter it:", SmsCode)


@tool("Log in to Okala (sends an SMS code)", read_only=False)
async def ok_login(phone: Phone, code: Annotated[ElicitationResult[SmsCode], Resolve(_sms_code)]) -> dict[str, Any]:
    """Log in to the user's Okala account, which unlocks Okala's real text search in ok_search
    (brands and product names, inside one store too). Optional: every other tool works as a guest.

    Only call this when the user asked to log in and gave THEIR OWN phone number. It sends an SMS code to
    that number (at most one every 2 minutes). If the client can show a form, the user types the code there
    and the login finishes in this call. Otherwise the reply says `code_sent`: ask the user for the code and
    call ok_login_verify. Nothing is ordered or changed in the account.
    """
    mobile = _mobile(phone)
    if isinstance(code, AcceptedElicitation) and isinstance(code.data, SmsCode):
        return await _verify(mobile, code.data.code)
    pending = {
        "status": "code_sent",
        "phone": auth.masked(mobile),
        "new_code_possible_in_s": max(0, int(RESEND_SECONDS - (time.monotonic() - _sent.get(mobile, 0)))),
        "next": "Ask the user for the code Okala sent by SMS, then call ok_login_verify with it.",
    }
    if not isinstance(code, AcceptedElicitation):  # the user closed the form
        pending["next"] = "The user closed the form. If they still want to log in, ask for the code."
    return pending


@tool("Finish the Okala login", read_only=False)
async def ok_login_verify(
    phone: Phone,
    code: Annotated[str, Field(min_length=4, max_length=12, description="The SMS code, e.g. '12345'.")],
) -> dict[str, Any]:
    """Finish a login started with ok_login: the code the user received by SMS. Saves the login on this
    machine (it refreshes itself); afterwards ok_search uses Okala's text search."""
    return await _verify(_mobile(phone), code)


@tool("Okala login status")
async def ok_account() -> dict[str, Any]:
    """Whether the server is logged in to Okala (and as which phone, masked). Logged in = ok_search uses
    Okala's text search; guest = category matching. To log in: ok_login."""
    login = http.token()
    if not login:
        return {
            "logged_in": False,
            "search": "category matching (guest)",
            "how_to_log_in": "ok_login with the user's phone",
        }
    return {
        "logged_in": True,
        "phone": auth.masked(login.get("mobile")),
        "search": "Okala text search",
        "source": "OKALA_MCP_TOKEN" if os.environ.get("OKALA_MCP_TOKEN") else str(auth.token_path()),
    }


@tool("Log out of Okala", read_only=False, destructive=True)
async def ok_logout() -> dict[str, Any]:
    """Delete the saved Okala login on this machine; the server continues as a guest."""
    auth.forget()
    http.use_login(None)
    return {"logged_in": False, "message": "Logged out. The saved login was deleted."}
