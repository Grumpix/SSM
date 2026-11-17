"""Dashboard workspace and program management must have distinct routes."""

def _client():
    from server import app
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
        sess["terminal_owner"] = "ssm-admin"
    return client


def test_dashboard_is_terminal_workspace():
    response = _client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="overview-pane" class=" shrink-0' in html
    assert 'id="terminal-pane" class="flex flex-1' in html
    assert 'id="services-pane" class="hidden flex-1' in html
    assert 'const IS_SERVICES_PAGE = false;' in html
    assert 'href="/services"' in html


def test_services_has_program_management_and_hides_terminal_workspace():
    response = _client().get("/services")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="overview-pane" class="hidden shrink-0' in html
    assert 'id="terminal-pane" class="hidden flex-1' in html
    assert 'id="services-pane" class=" flex-1' in html
    assert 'const IS_SERVICES_PAGE = true;' in html
    assert 'id="program-grid"' in html
    assert 'onclick="openModal()"' in html


def test_services_requires_login():
    from server import app
    response = app.test_client().get("/services")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login")
