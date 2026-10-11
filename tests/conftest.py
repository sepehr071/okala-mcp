import json
import time
from pathlib import Path

import httpx
import pytest
from mcp import Client

from okala_mcp import account, http, stores
from okala_mcp.server import mcp

FIXTURES = Path(__file__).parent / "fixtures"
LAT, LON = 35.737156785003556, 51.41540956014044  # Tehran, Yousef Abad
POINT = {"lat": LAT, "lon": LON}


def fixture(name: str):
    """A recorded (trimmed) response from apigateway.okala.com."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fresh_http_client(request, monkeypatch, tmp_path):
    """The shared httpx client is bound to one event loop; each test gets a new loop.
    Unit tests run as a guest (no saved login is read); live tests use the real login if there is one."""
    if not request.node.get_closest_marker("live"):
        monkeypatch.delenv("OKALA_MCP_TOKEN", raising=False)
        monkeypatch.setenv("OKALA_MCP_TOKEN_FILE", str(tmp_path / "token.json"))
    http.set_transport(None)
    stores._cities.clear()
    account._sent.clear()
    yield
    http.set_transport(None)


@pytest.fixture(autouse=True)
def gentle(request):
    """Live tests: about one request per second against Okala."""
    yield
    if request.node.get_closest_marker("live"):
        time.sleep(1)


@pytest.fixture
def api():
    """Route table for a fake apigateway.okala.com.

    Keys are URL paths (api["/api/opex/v4/stores/nearby"]). Values: a dict/list (JSON) or a
    callable(request) -> httpx.Response. Unknown paths return the gateway's empty 404.
    Every request is appended to api.calls.
    """

    class Routes(dict):
        calls: list[httpx.Request]

        def params(self, i: int = -1) -> dict[str, str]:
            return dict(self.calls[i].url.params)

    routes = Routes()
    routes.calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        routes.calls.append(request)
        target = routes.get(request.url.path)
        if target is None:
            return httpx.Response(404)
        if callable(target):
            return target(request)
        return httpx.Response(200, json=target)

    http.set_transport(httpx.MockTransport(handler))
    return routes


@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:
        yield c
