"""Tests for session.session_manager — checkpoint I/O + resume."""
from pathlib import Path

import pytest

from session.session_manager import SessionManager


@pytest.fixture
def mgr(tmp_path):
    """Fresh SessionManager rooted at tmp_path."""
    return SessionManager(
        output_dir=str(tmp_path),
        project_name="testproj",
        resolved_output_dir=tmp_path / "testproj",
    )


def test_session_id_format(mgr):
    # YYYYMMDD_HHMMSS_<4hex>
    parts = mgr.session_id.split("_")
    assert len(parts) == 3
    assert len(parts[0]) == 8   # date
    assert len(parts[1]) == 6   # time
    assert len(parts[2]) == 4   # hex suffix


def test_save_and_load_roundtrip(mgr):
    mgr.save("ba", "BA content")
    mgr.save("dev", "Dev content")
    assert mgr.is_step_done("ba")
    assert mgr.is_step_done("dev")
    assert not mgr.is_step_done("design")


def test_resume_loads_existing_checkpoints(tmp_path, mgr):
    mgr.save("ba", "saved BA content")
    # Fresh manager for same session_id
    mgr2 = SessionManager(
        output_dir=str(tmp_path), project_name="testproj",
        resolved_output_dir=tmp_path / "testproj",
        resume_session=mgr.session_id,
    )
    mgr2.load_checkpoints()
    assert mgr2.is_step_done("ba")
    assert mgr2.results["ba"] == "saved BA content"


def test_list_sessions_handles_3_part_session_id(tmp_path):
    """Regression: old parser split on '_' and broke 3-part IDs."""
    mgr = SessionManager(
        output_dir=str(tmp_path), project_name="p",
        resolved_output_dir=tmp_path / "p",
    )
    mgr.save("pm", "pm done")
    # Missing other steps → session is resumable
    sessions = SessionManager.list_sessions(str(tmp_path), "p")
    assert len(sessions) == 1
    assert sessions[0]["session_id"] == mgr.session_id
    assert "pm" in sessions[0]["completed"]
    assert "dev" in sessions[0]["missing"]
