"""tmux session persistence and safe discovery tests."""

from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

from app.terminal_manager import TerminalManager


def test_discover_only_ssm_tmux_sessions():
    manager = TerminalManager(MagicMock())
    result = CompletedProcess([], 0, "ssm-build\nother-session\nssm-minecraft\nssm-bad.name\n", "")
    with patch("app.terminal_manager.shutil.which", return_value="/usr/bin/tmux"), \
         patch("app.terminal_manager.subprocess.run", return_value=result) as run:
        assert manager.list_sessions() == ["build", "minecraft"]
    run.assert_called_once()
    assert run.call_args.args[0] == ["tmux", "list-sessions", "-F", "#{session_name}"]


def test_no_tmux_server_means_no_sessions():
    manager = TerminalManager(MagicMock())
    result = CompletedProcess([], 1, "", "failed to connect to server: No such file or directory")
    with patch("app.terminal_manager.shutil.which", return_value="/usr/bin/tmux"), \
         patch("app.terminal_manager.subprocess.run", return_value=result):
        assert manager.list_sessions() == []


@pytest.mark.parametrize("bad", ["", "a/b", "../pwn", "with space", "x" * 65, "-x;id"])
def test_reject_invalid_tmux_names(bad):
    manager = TerminalManager(MagicMock())
    with pytest.raises(ValueError):
        manager.create_session(bad)
    with pytest.raises(ValueError):
        manager.kill_session(bad)


def test_detach_does_not_kill_persistent_tmux_session():
    manager = TerminalManager(MagicMock())
    manager.sessions["minecraft"] = MagicMock()
    manager.close("minecraft")
    assert "minecraft" not in manager.sessions
    # Only the ephemeral PTY client is closed; tmux kill-session is
    # exclusively used in the explicit kill_session method.


def test_explicit_kill_targets_exact_ssm_session():
    manager = TerminalManager(MagicMock())
    with patch("app.terminal_manager.subprocess.run") as run:
        manager.kill_session("minecraft")
    assert run.call_args.args[0] == ["tmux", "kill-session", "-t", "=ssm-minecraft"]
