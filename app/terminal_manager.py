import os
import pty
import select
import subprocess
import threading
import time
import logging
import signal
import fcntl
import termios
import struct
import shutil
import re
from collections import deque
from typing import Dict, Optional

logger = logging.getLogger("TerminalManager")

# Scrollback kept per session so a client that navigates away and
# comes back (reattach by stable id) sees what it missed instead of
# a blank tab. Bounded: 200 chunks ≈ 800 KB worst case per session.
_SCROLLBACK_CHUNKS = 200
_TMUX_PREFIX = "ssm-"
_SESSION_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

class TerminalSession:
    def __init__(self, session_id: str, socketio, cmd: str = "/bin/bash"):
        self.id = session_id
        self.socketio = socketio
        self.cmd = cmd
        self.fd = None
        self.pid = None
        self.active = False
        self.thread = None
        self.scrollback: deque = deque(maxlen=_SCROLLBACK_CHUNKS)

    def start(self):
        self.pid, self.fd = pty.fork()
        
        if self.pid == 0:
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            os.chdir(os.path.expanduser('~'))
            # tmux owns the real shell, so it survives web disconnects and
            # restarts of this Python process. This PTY is only a client.
            os.execvpe("tmux", ["tmux", "new-session", "-A", "-s",
                                _TMUX_PREFIX + self.id], env)
        else:
            self.active = True
            self.thread = threading.Thread(target=self._read_loop, daemon=True)
            self.thread.start()
            logger.info(f"Started terminal session {self.id} (PID: {self.pid})")

    def _read_loop(self):
        while self.active:
            try:
                r, _, _ = select.select([self.fd], [], [], 0.1)
                if self.fd in r:
                    data = os.read(self.fd, 4096)
                    if not data:
                        break
                    text = data.decode('utf-8', errors='ignore')
                    self.scrollback.append(text)
                    self.socketio.emit('terminal_output', {
                        'id': self.id,
                        'data': text
                    }, to=f'terminal:{self.id}')
            except OSError:
                break
            except Exception as e:
                logger.error(f"Error reading from terminal {self.id}: {e}")
                break
        
        self.close()

    def write(self, data: str):
        if self.active and self.fd:
            try:
                os.write(self.fd, data.encode('utf-8'))
            except OSError:
                self.close()

    def resize(self, cols: int, rows: int):
        if self.active and self.fd:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.fd, termios.TIOCSWINSZ, winsize)
            except Exception as e:
                logger.error(f"Failed to resize terminal {self.id}: {e}")

    def close(self):
        if not self.active:
            return
            
        self.active = False
        logger.info(f"Closing terminal session {self.id}")
        
        if self.fd:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

        if self.pid:
            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, os.WNOHANG)
            except OSError:
                pass
            self.pid = None
            
        self.socketio.emit('terminal_closed', {'id': self.id}, to=f'terminal:{self.id}')

class TerminalManager:
    def __init__(self, socketio):
        self.socketio = socketio
        self.sessions: Dict[str, TerminalSession] = {}
        self.owners: Dict[str, str] = {}

    def create_session(self, session_id: str, owner: Optional[str] = None):
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ValueError("Invalid terminal session name")
        if not shutil.which("tmux"):
            raise RuntimeError("tmux is not installed (apt install tmux)")
        if session_id in self.sessions:
            if self.session_alive(session_id):
                return
            # A dead tmux client must not prevent attaching again.
            self.sessions.pop(session_id, None)
            self.owners.pop(session_id, None)

        session = TerminalSession(session_id, self.socketio)
        session.start()
        self.sessions[session_id] = session
        if owner is not None:
            self.owners[session_id] = owner

    def list_sessions(self) -> list[str]:
        """Discover surviving tmux sessions, even after a manager restart."""
        if not shutil.which("tmux"):
            raise RuntimeError("tmux is not installed (apt install tmux)")
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode:
            # tmux returns nonzero when its server has no sessions.
            if any(marker in result.stderr.lower() for marker in
                   ("no server running", "error connecting", "failed to connect to server")):
                return []
            raise RuntimeError("Could not list tmux sessions")
        return sorted(name[len(_TMUX_PREFIX):] for name in result.stdout.splitlines()
                      if name.startswith(_TMUX_PREFIX)
                      and _SESSION_ID.fullmatch(name[len(_TMUX_PREFIX):]))

    def kill_session(self, session_id: str):
        """Explicitly terminate a persistent session (closing a tab never does)."""
        if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
            raise ValueError("Invalid terminal session name")
        self.close(session_id)
        subprocess.run(["tmux", "kill-session", "-t", "=" + _TMUX_PREFIX + session_id],
                       capture_output=True, timeout=5, check=False)

    def belongs_to(self, session_id: str, owner: str) -> bool:
        return bool(owner) and self.owners.get(session_id) == owner

    def session_alive(self, session_id: str) -> bool:
        """True if the session exists and its shell is still running."""
        session = self.sessions.get(session_id)
        return bool(session) and session.active and session.pid is not None

    def get_backlog(self, session_id: str) -> str:
        """Replayable scrollback for a reattaching client ('' if none)."""
        session = self.sessions.get(session_id)
        if not session:
            return ""
        return "".join(session.scrollback)

    def write(self, session_id: str, data: str):
        if session_id in self.sessions:
            self.sessions[session_id].write(data)

    def resize(self, session_id: str, cols: int, rows: int):
        if session_id in self.sessions:
            self.sessions[session_id].resize(cols, rows)

    def close(self, session_id: str):
        if session_id in self.sessions:
            self.sessions[session_id].close()
            del self.sessions[session_id]
            self.owners.pop(session_id, None)
