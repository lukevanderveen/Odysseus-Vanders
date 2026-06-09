"""Regression guard: execute_api_call must strip whitespace from base_url.

A trailing space in a stored integration base_url (e.g. "https://api.adzuna.com ")
makes the constructed URL "https://api.adzuna.com /v1/..." — httpx then tries to
resolve the hostname "api.adzuna.com " (with a space) and fails with the cryptic
"[Errno 11001] getaddrinfo failed". `.rstrip('/')` alone does NOT remove spaces,
so the normalization must also `.strip()`.

Source-level check (mirrors the repo's other config invariants); a behavioral
test would require a live network call.
"""

import re
from pathlib import Path


def test_execute_api_call_strips_base_url_whitespace():
    src = Path("src/integrations.py").read_text(encoding="utf-8")
    m = re.search(
        r"async def execute_api_call\(.*?(?=\nasync def |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "could not locate execute_api_call"
    body = m.group(0)
    line = re.search(r'base_url\s*=\s*integration\.get\("base_url".*', body)
    assert line, "base_url assignment not found in execute_api_call"
    assert ".strip()" in line.group(0), (
        "BUG: base_url is not whitespace-stripped. A trailing space in a stored "
        "integration URL yields 'host ' and httpx fails with 'getaddrinfo failed'. "
        "Normalize with .strip() (not just .rstrip('/'))."
    )
