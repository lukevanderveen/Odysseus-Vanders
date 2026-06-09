"""execute_api_call must merge an integration's `default_params` into every
request, with caller-supplied params taking precedence.

Some APIs (e.g. Adzuna) require credentials as query params on EVERY call
(app_id + app_key). Relying on the model to attach them each time is fragile —
a missing key yields an opaque 400. Storing them as `default_params` on the
integration makes them always-on, so the model only supplies the search terms.

Source-level check (consistent with the repo's other config invariants).
"""

import re
from pathlib import Path


def test_execute_api_call_merges_default_params():
    src = Path("src/integrations.py").read_text(encoding="utf-8")
    m = re.search(
        r"async def execute_api_call\(.*?(?=\nasync def |\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert m, "could not locate execute_api_call"
    body = m.group(0)
    assert "default_params" in body, (
        "BUG: execute_api_call does not read integration['default_params']. "
        "APIs that need always-on query credentials (e.g. Adzuna app_id/"
        "app_key) can't be configured to auto-attach them."
    )
    assert "**(params or {})" in body, (
        "default_params must be merged as the base with caller params "
        "overriding, e.g. {**defaults, **(params or {})}."
    )
