"""Regression guard: skills mutation routes must forward the owner scope.

`SkillsManager.delete_skill` / `update_skill` filter on
`(sk.owner or "") != (owner or "")`, so with the default `owner=None`
they only match OWNERLESS skills. Several routes resolved the caller
(`user = _owner(request)`), used it to *find* the skill, but then called
the manager WITHOUT forwarding `owner=user`:

  * DELETE /api/skills/{id}          -> delete_skill(name)            -> 404
  * POST   /api/skills/{id}/markdown -> update_skill(name, {...})     -> 500 "Update failed"
  * PUT    /api/skills/{id}          -> update_skill(name, updates)   -> 404

For any owner-stamped skill the manager matched nothing and returned
False, so the route errored. These are source-level checks (mirroring
`test_update_skill_scalar_keys_exclude_owner`) because exercising the
routes needs the full FastAPI + auth stack.

NOTE: the check is scoped to the *specific* mutation call, not the whole
handler body — every handler also calls `skills_manager.load(owner=user)`,
which would mask a missing owner on the mutation call.
"""

import re
from pathlib import Path

import pytest

_SRC = Path("routes/skills_routes.py").read_text(encoding="utf-8")


def _handler_body(func_signature: str) -> str:
    m = re.search(
        re.escape(func_signature) + r"(.*?)(?:\n    @router|\n    def |\Z)",
        _SRC,
        re.DOTALL,
    )
    assert m, f"could not locate handler: {func_signature}"
    return m.group(1)


def _manager_call_args(body: str, manager_call: str) -> str:
    """Return the full argument string of `skills_manager.<manager_call>(...)`,
    balancing nested parens/dicts so a multi-line dict argument is included."""
    needle = f"skills_manager.{manager_call}("
    idx = body.find(needle)
    assert idx != -1, f"handler no longer calls skills_manager.{manager_call}"
    start = idx + len(needle) - 1  # at the opening '('
    depth = 0
    for i in range(start, len(body)):
        if body[i] == "(":
            depth += 1
        elif body[i] == ")":
            depth -= 1
            if depth == 0:
                return body[start:i + 1]
    raise AssertionError(f"unbalanced parens in {manager_call} call")


@pytest.mark.parametrize(
    "signature, manager_call",
    [
        ("async def delete_skill(request: Request, skill_id: str):", "delete_skill"),
        ("async def save_skill_markdown(request: Request, skill_id: str):", "update_skill"),
        ("async def update_skill(request: Request, skill_id: str, body: SkillUpdateRequest):", "update_skill"),
    ],
)
def test_skills_mutation_route_forwards_owner(signature, manager_call):
    body = _handler_body(signature)
    call_args = _manager_call_args(body, manager_call)
    assert "owner=user" in call_args, (
        f"BUG: this route calls skills_manager.{manager_call} without "
        f"owner=user. The manager scopes by owner, so owner-stamped skills "
        f"match nothing and the route errors (404/500). Forward owner=user. "
        f"Got call args: {call_args!r}"
    )
