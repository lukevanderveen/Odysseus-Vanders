"""MCP tool descriptions must occupy one line each in the agent prompt.

Spotify-style servers ship descriptions that embed their own bullet lists
("Manages playback:\n- get: ...\n- start: ..."). Emitting those verbatim
breaks every consumer that parses the prompt text line by line (the tool
index turned each embedded bullet into a phantom tool, and two servers
both listing a "get" action produced duplicate ids that crashed the upsert).
"""

from src.mcp_manager import McpManager


def _manager_with(description: str) -> McpManager:
    mgr = McpManager()
    mgr._connections = {"spotify": {"name": "Spotify", "status": "connected"}}
    mgr._tools = {"spotify": [{"name": "SpotifyPlayback", "description": description}]}
    return mgr


def test_multiline_description_is_flattened_to_one_tool_line():
    mgr = _manager_with("Manages the current playback with the following actions:\n- get: Get info.\n- start: Play.")

    text = mgr.get_tool_descriptions_for_prompt()

    tool_lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("- ")]
    assert len(tool_lines) == 1
    assert tool_lines[0].startswith("- mcp__spotify__SpotifyPlayback:")
    assert "- get:" in tool_lines[0]  # content preserved, just no longer on its own line
