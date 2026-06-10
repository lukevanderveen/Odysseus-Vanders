"""projects_workspace_roots must be a registered app setting.

POST /api/auth/settings only persists keys present in DEFAULT_SETTINGS
(routes/auth_routes.py iterates DEFAULT_SETTINGS) — if the key is missing
there, the Settings UI's save silently drops it. This pins the contract the
Projects settings tab depends on.
"""

from src.settings import DEFAULT_SETTINGS


def test_projects_workspace_roots_is_a_registered_setting():
    assert "projects_workspace_roots" in DEFAULT_SETTINGS


def test_projects_workspace_roots_defaults_to_empty_list():
    assert DEFAULT_SETTINGS["projects_workspace_roots"] == []
