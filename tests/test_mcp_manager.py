from src.mcp_manager import _format_mcp_connection_error, McpManager


def test_playwright_mcp_connection_error_includes_install_hint():
    msg = _format_mcp_connection_error(
        "Browser (Playwright)",
        "npx",
        ["-y", "@playwright/mcp@latest", "--headless"],
        RuntimeError("package not found"),
    )

    assert "package not found" in msg
    assert "Browser MCP could not start" in msg
    assert "npx -y @playwright/mcp@latest --version" in msg
    assert "restart Odysseus" in msg


def test_generic_mcp_connection_error_preserves_original_error():
    msg = _format_mcp_connection_error(
        "Custom MCP",
        "python",
        ["server.py"],
        RuntimeError("boom"),
    )

    assert msg == "boom"


class _RecordingAsyncCM:
    """Async context manager that records whether it was exited (torn down)."""

    def __init__(self, value, on_exit):
        self._value = value
        self._on_exit = on_exit

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc):
        self._on_exit()
        return False


async def test_failed_stdio_connect_tears_down_transport_in_task(monkeypatch):
    """A stdio connection that fails mid-handshake must close its transport
    stack in the same task, so anyio never tears the task group down later
    (which raised 'cancel scope in a different task')."""
    import mcp
    import mcp.client.stdio as mcp_stdio

    exited = {"stdio": False}

    def fake_stdio_client(server_params):
        return _RecordingAsyncCM(
            ("read", "write"), lambda: exited.__setitem__("stdio", True)
        )

    class FakeSession:
        def __init__(self, read, write):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def initialize(self):
            raise RuntimeError("Connection closed")

    class FakeParams:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(mcp_stdio, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(mcp, "ClientSession", FakeSession)
    monkeypatch.setattr(mcp, "StdioServerParameters", FakeParams)

    mgr = McpManager()
    ok = await mgr.connect_server(
        "srv1", "Docker", "stdio", command="docker-mcp", args=[], env={}
    )

    assert ok is False
    assert exited["stdio"] is True
    assert "srv1" not in mgr._stacks
