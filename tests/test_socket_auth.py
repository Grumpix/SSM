"""Regression tests: browser socket access must require a login and session ownership."""

from unittest.mock import patch

import pytest


@pytest.fixture
def socket_app():
    from server import app, socketio, tm

    # Keep these tests from starting actual shells or affecting other tests.
    with patch.object(tm, "create_session") as create, \
         patch.object(tm, "write") as write, \
         patch.object(tm, "resize") as resize, \
         patch.object(tm, "close") as close, \
         patch.object(tm, "session_alive", return_value=False), \
         patch.object(tm, "get_backlog", return_value=""):
        tm.sessions.clear()
        tm.owners.clear()
        yield app, socketio, tm, create, write, resize, close
        tm.sessions.clear()
        tm.owners.clear()


def _logged_in(app, socketio, owner):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["terminal_owner"] = owner
    socket = socketio.test_client(app, flask_test_client=client)
    assert socket.is_connected()
    return socket


def test_anonymous_websocket_cannot_connect(socket_app):
    app, socketio, tm, create, write, resize, close = socket_app
    client = socketio.test_client(app, flask_test_client=app.test_client())
    assert not client.is_connected()
    create.assert_not_called()


def test_logged_in_without_terminal_identity_cannot_connect(socket_app):
    app, socketio, *_ = socket_app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    socket = socketio.test_client(app, flask_test_client=client)
    assert not socket.is_connected()


def test_authenticated_client_can_create_terminal(socket_app):
    app, socketio, tm, create, *_ = socket_app
    client = _logged_in(app, socketio, "alice")
    client.emit("terminal_create", {"id": "alice-term"})
    create.assert_called_once_with("alice-term", owner="alice")
    client.disconnect()


def test_other_login_cannot_control_existing_terminal(socket_app):
    app, socketio, tm, create, write, resize, close = socket_app
    tm.sessions["private"] = object()
    tm.owners["private"] = "alice"
    bob = _logged_in(app, socketio, "bob")
    bob.emit("terminal_create", {"id": "private"})
    bob.emit("terminal_input", {"id": "private", "data": "whoami\n"})
    bob.emit("terminal_resize", {"id": "private", "cols": 80, "rows": 25})
    bob.emit("terminal_close", {"id": "private"})
    create.assert_not_called()
    write.assert_not_called()
    resize.assert_not_called()
    close.assert_not_called()
    bob.disconnect()


def test_terminal_output_only_reaches_joined_owner(socket_app):
    app, socketio, tm, *_ = socket_app
    alice = _logged_in(app, socketio, "alice")
    bob = _logged_in(app, socketio, "bob")
    alice.emit("terminal_create", {"id": "private"})
    alice.get_received()
    bob.get_received()
    socketio.emit("terminal_output", {"id": "private", "data": "secret"}, to="terminal:private")
    assert any(event["name"] == "terminal_output" for event in alice.get_received())
    assert not any(event["name"] == "terminal_output" for event in bob.get_received())
    alice.disconnect()
    bob.disconnect()
