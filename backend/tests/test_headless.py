"""Tests for headless mode functionality."""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.headless import (
    ExitCode,
    Result,
    SessionInfo,
    ensure_sessions_dir,
    list_sessions,
    save_session,
    delete_session,
    load_session,
    resolve_resume_session,
    should_restart_headless_run,
)


class TestExitCode:
    """Test exit code constants."""

    def test_exit_codes_are_correct(self):
        """Verify exit code values match gsd-2 pattern."""
        assert ExitCode.SUCCESS == 0
        assert ExitCode.ERROR == 1
        assert ExitCode.BLOCKED == 10
        assert ExitCode.CANCELLED == 11


class TestResult:
    """Test Result dataclass."""

    def test_result_to_dict(self):
        """Test Result serialization."""
        result = Result(
            exit_code=ExitCode.SUCCESS,
            interrupted=False,
            status="complete",
            text="Test output",
            data={"key": "value"}
        )

        d = result.to_dict()
        assert d["exit_code"] == ExitCode.SUCCESS
        assert d["interrupted"] is False
        assert d["status"] == "complete"
        assert d["text"] == "Test output"
        assert d["data"] == {"key": "value"}


class TestSessionManagement:
    """Test session management functions."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for sessions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Patch SESSIONS_DIR
            with patch('backend.headless.SESSIONS_DIR', Path(tmpdir)):
                yield Path(tmpdir)

    def test_ensure_sessions_dir(self, temp_dir):
        """Test that sessions directory is created."""
        # temp_dir is created by tempfile fixture
        # Just verify the function works with existing dir
        with patch('backend.headless.SESSIONS_DIR', temp_dir):
            ensure_sessions_dir()

        assert temp_dir.exists()
        assert temp_dir.is_dir()

    def test_save_and_load_session(self, temp_dir):
        """Test saving and loading a session."""
        with patch('backend.headless.SESSIONS_DIR', temp_dir):
            # Save session
            session = SessionInfo(
                id="test_session_123",
                group_id=1,
                member_id=2,
                created_at=time.time(),
                command="auto",
                status="active"
            )
            save_session(session)

            # Load session
            loaded = load_session("test_session_123")

            assert loaded is not None
            assert loaded.id == session.id
            assert loaded.group_id == session.group_id
            assert loaded.member_id == session.member_id
            assert loaded.command == session.command
            assert loaded.status == session.status

    def test_load_nonexistent_session(self, temp_dir):
        """Test loading a session that doesn't exist."""
        with patch('backend.headless.SESSIONS_DIR', temp_dir):
            loaded = load_session("nonexistent")
            assert loaded is None

    def test_delete_session(self, temp_dir):
        """Test deleting a session."""
        with patch('backend.headless.SESSIONS_DIR', temp_dir):
            # Save session
            session = SessionInfo(
                id="to_delete",
                group_id=1,
                member_id=2,
                created_at=time.time(),
                command="next",
                status="active"
            )
            save_session(session)

            # Verify exists
            assert load_session("to_delete") is not None

            # Delete
            delete_session("to_delete")

            # Verify deleted
            assert load_session("to_delete") is None

    def test_list_sessions(self, temp_dir):
        """Test listing all sessions."""
        with patch('backend.headless.SESSIONS_DIR', temp_dir):
            # Create multiple sessions
            sessions = [
                SessionInfo(id=f"session_{i}", group_id=1, member_id=1,
                           created_at=time.time() - i * 100, command="auto", status="active")
                for i in range(5)
            ]
            for s in sessions:
                save_session(s)

            # List all
            listed = list_sessions()
            assert len(listed) == 5

            # Should be sorted by created_at descending
            assert listed[0].id == "session_0"  # Most recent

    def test_list_sessions_filter_by_group(self, temp_dir):
        """Test filtering sessions by group_id."""
        with patch('backend.headless.SESSIONS_DIR', temp_dir):
            # Create sessions for different groups
            sessions = [
                SessionInfo(id="g1_s1", group_id=1, member_id=1,
                           created_at=time.time(), command="auto", status="active"),
                SessionInfo(id="g2_s1", group_id=2, member_id=1,
                           created_at=time.time(), command="next", status="active"),
                SessionInfo(id="g1_s2", group_id=1, member_id=2,
                           created_at=time.time(), command="discuss", status="blocked"),
            ]
            for s in sessions:
                save_session(s)

            # Filter by group 1
            listed_g1 = list_sessions(group_id=1)
            assert len(listed_g1) == 2
            assert all(s.group_id == 1 for s in listed_g1)

            # Filter by group 2
            listed_g2 = list_sessions(group_id=2)
            assert len(listed_g2) == 1
            assert listed_g2[0].id == "g2_s1"


class TestResumeSession:
    """Test session resolution for resume functionality."""

    def test_exact_match(self):
        """Test exact session ID match."""
        sessions = [
            SessionInfo(id="session_123", group_id=1, member_id=1,
                       created_at=time.time(), command="auto", status="active"),
        ]

        session, error = resolve_resume_session(sessions, "session_123")
        assert session is not None
        assert session.id == "session_123"
        assert error is None

    def test_prefix_match_unique(self):
        """Test unique prefix match."""
        sessions = [
            SessionInfo(id="session_123abc", group_id=1, member_id=1,
                       created_at=time.time(), command="auto", status="active"),
            SessionInfo(id="session_456def", group_id=1, member_id=1,
                       created_at=time.time(), command="next", status="active"),
        ]

        session, error = resolve_resume_session(sessions, "session_123")
        assert session is not None
        assert session.id == "session_123abc"
        assert error is None

    def test_no_match(self):
        """Test when no session matches."""
        sessions = [
            SessionInfo(id="session_123", group_id=1, member_id=1,
                       created_at=time.time(), command="auto", status="active"),
        ]

        session, error = resolve_resume_session(sessions, "nonexistent")
        assert session is None
        assert error is not None
        assert "nonexistent" in error

    def test_ambiguous_match(self):
        """Test when multiple sessions match prefix."""
        sessions = [
            SessionInfo(id="session_abc_xyz", group_id=1, member_id=1,
                       created_at=time.time(), command="auto", status="active"),
            SessionInfo(id="session_abc_123", group_id=1, member_id=1,
                       created_at=time.time(), command="next", status="active"),
        ]

        session, error = resolve_resume_session(sessions, "session_abc")
        assert session is None
        assert error is not None
        assert "Ambiguous" in error
        assert "session_abc" in error


class TestRestartLogic:
    """Test headless restart logic."""

    def test_restart_on_error(self):
        """Should restart on error status."""
        result = Result(
            exit_code=ExitCode.ERROR,
            interrupted=False,
            status="error",
            text="Some error"
        )
        assert should_restart_headless_run(result) is True

    def test_restart_on_timeout(self):
        """Should restart on timeout status."""
        result = Result(
            exit_code=ExitCode.ERROR,
            interrupted=False,
            status="timeout",
            text="Timeout"
        )
        assert should_restart_headless_run(result) is True

    def test_no_restart_on_success(self):
        """Should not restart on success."""
        result = Result(
            exit_code=ExitCode.SUCCESS,
            interrupted=False,
            status="complete",
            text="Done"
        )
        assert should_restart_headless_run(result) is False

    def test_no_restart_on_blocked(self):
        """Should not restart when blocked."""
        result = Result(
            exit_code=ExitCode.BLOCKED,
            interrupted=False,
            status="blocked",
            text="Needs human intervention"
        )
        assert should_restart_headless_run(result) is False

    def test_no_restart_on_cancelled(self):
        """Should not restart when cancelled."""
        result = Result(
            exit_code=ExitCode.CANCELLED,
            interrupted=True,
            status="cancelled",
            text="User cancelled"
        )
        assert should_restart_headless_run(result) is False


class TestIntegration:
    """Integration tests for headless mode."""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for sessions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('backend.headless.SESSIONS_DIR', Path(tmpdir)):
                yield Path(tmpdir)

    def test_full_session_lifecycle(self, temp_dir):
        """Test complete session lifecycle."""
        with patch('backend.headless.SESSIONS_DIR', temp_dir):
            # Create and save session
            session = SessionInfo(
                id="lifecycle_test",
                group_id=1,
                member_id=2,
                created_at=time.time(),
                command="auto",
                status="active"
            )
            save_session(session)

            # Load and verify
            loaded = load_session("lifecycle_test")
            assert loaded is not None
            assert loaded.status == "active"

            # Update status
            loaded.status = "completed"
            save_session(loaded)

            # Verify update
            reloaded = load_session("lifecycle_test")
            assert reloaded.status == "completed"

            # List and verify
            listed = list_sessions()
            assert any(s.id == "lifecycle_test" for s in listed)

            # Delete
            delete_session("lifecycle_test")
            assert load_session("lifecycle_test") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
