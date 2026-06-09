"""Regression guard for the skills DELETE route dropping the owner scope.

`SkillsManager.delete_skill(skill_id, owner=None)` filters on
`(sk.owner or "") != (owner or "")`, so it only removes a skill whose
`owner` matches the argument. The DELETE `/api/skills/{skill_id}` route
resolves the caller (`user = _owner(request)`) and uses it to *find* the
skill, but then called `skills_manager.delete_skill(match["name"])`
WITHOUT forwarding `owner=user`. For any owner-stamped skill that means
the manager compares `"<owner>" != ""`, skips every file, returns False,
and the route raises 404 "Skill not found" — making every owned skill
undeletable through the UI.

This test asserts the route forwards an owner argument to delete_skill.
It is a source-level check (mirroring
`test_update_skill_scalar_keys_exclude_owner`) because exercising the
route requires the full FastAPI + auth stack.
"""

import re
from pathlib import Path


def test_delete_skill_route_forwards_owner():
    src = Path("routes/skills_routes.py").read_text(encoding="utf-8")

    m = re.search(
        r"async def delete_skill\(request: Request, skill_id: str\):(.*?)"
        r"(?:\n    @router|\n    def setup|\Z)",
        src,
        re.DOTALL,
    )
    assert m, "could not locate the delete_skill route handler"
    body = m.group(1)

    # Grab the full call line (the args contain a nested `.get("name")`,
    # so a `[^)]*` capture would stop at the inner paren — match the line).
    call = re.search(r"skills_manager\.delete_skill\(.*", body)
    assert call, "delete route no longer calls skills_manager.delete_skill"

    args = call.group(0)
    assert "owner" in args, (
        "BUG: the DELETE /api/skills/{skill_id} route calls delete_skill "
        "without an owner= argument. delete_skill scopes by owner, so every "
        "owner-stamped skill returns False -> 404 and cannot be deleted in "
        "the UI. Forward owner=user to delete_skill."
    )
