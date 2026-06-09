import httpx

from src.search import content as content_module
from src.search.content import fetch_webpage_content


def _no_cache(monkeypatch, tmp_path):
    """Point the content cache at an empty temp dir so tests don't hit disk state."""
    monkeypatch.setattr(content_module, "CONTENT_CACHE_DIR", tmp_path)


def test_http_error_status_returns_empty_result_instead_of_raising(monkeypatch, tmp_path):
    """A 403 (or any 4xx/5xx) must degrade to a failure dict, not propagate and 500 the chat."""
    _no_cache(monkeypatch, tmp_path)

    url = "https://trademarks.ipo.gov.uk/ipo-tmtext"

    def fake_get(u, *, headers, timeout):
        request = httpx.Request("GET", u)
        return httpx.Response(403, request=request, content=b"Forbidden")

    monkeypatch.setattr(content_module, "_get_public_url", fake_get)

    result = fetch_webpage_content(url)

    assert result["success"] is False
    assert result["url"] == url
    assert "403" in result["error"]
