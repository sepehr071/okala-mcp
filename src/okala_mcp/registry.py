"""Collects tool functions from the tool modules; server.py registers them."""

from collections.abc import Callable
from typing import Any

TOOLS: list[tuple[Callable[..., Any], str, bool]] = []


def tool(title: str, read_only: bool = True) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark an async function as an MCP tool with a human-readable title.
    read_only=False only for the login tools (they send an SMS or change the saved login)."""

    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        TOOLS.append((fn, title, read_only))
        return fn

    return register
