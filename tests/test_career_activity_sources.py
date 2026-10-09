"""Activity sources: local git log parsing (real repo), GitHub/Trello via a fake execute."""
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from services.career import activity_sources as acts

_HAS_GIT = shutil.which("git") is not None


@dataclass
class StubProject:
    id: str
    name: str
    path: str


def _git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture()
def repo(tmp_path):
    if not _HAS_GIT:
        pytest.skip("git not on PATH")
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "feat: add streaming parser\n\nUses an incremental tokenizer.")
    (tmp_path / "b.py").write_text("print(2)\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "fix: retry on timeout")
    return tmp_path


def test_local_git_parses_subject_body_and_date(repo):
    items, warning = acts.fetch_local_git(StubProject("p1", "Odysseus", str(repo)),
                                          datetime.now(timezone.utc) - timedelta(days=1))
    assert warning is None
    assert [i.title for i in items] == ["fix: retry on timeout", "feat: add streaming parser"]
    assert items[1].body == "Uses an incremental tokenizer."
    assert items[0].source == "git" and items[0].kind == "commit"
    assert items[0].project_id == "p1" and items[0].project_name == "Odysseus"
    assert items[0].date.startswith(str(datetime.now().year))


def test_local_git_since_filters_old_commits(repo):
    items, _ = acts.fetch_local_git(StubProject("p1", "Odysseus", str(repo)),
                                    datetime.now(timezone.utc) + timedelta(days=1))
    assert items == []


def test_non_git_path_returns_warning(tmp_path):
    items, warning = acts.fetch_local_git(StubProject("p2", "Plain", str(tmp_path)), datetime.now(timezone.utc))
    assert items == [] and "not a git repository" in warning


def test_missing_git_binary_returns_warning(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(acts, "which_tool", lambda name: None)
    items, warning = acts.fetch_local_git(StubProject("p3", "X", str(tmp_path)), datetime.now(timezone.utc))
    assert items == [] and "git is not installed" in warning


def test_activity_item_roundtrip():
    item = acts.ActivityItem("git", "p1", "Odysseus", "t", "b", "2026-10-01T00:00:00+00:00", None, "commit")
    assert acts.item_from_dict(item.to_dict()) == item
