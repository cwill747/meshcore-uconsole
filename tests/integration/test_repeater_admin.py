from meshcore_console.mock import MockMeshcoreClient


def test_admin_login() -> None:
    client = MockMeshcoreClient()
    result = client.login_to_repeater("repeater-1", "admin123")

    assert result["success"] is True
    assert result["is_admin"] is True

    state = client.get_repeater_login_state("repeater-1")
    assert state is not None
    assert state.is_admin is True
    assert state.is_guest is False


def test_guest_login() -> None:
    client = MockMeshcoreClient()
    result = client.guest_login_to_repeater("repeater-1")

    assert result["success"] is True
    assert result["is_admin"] is False

    state = client.get_repeater_login_state("repeater-1")
    assert state is not None
    assert state.is_admin is False
    assert state.is_guest is True


def test_logout() -> None:
    client = MockMeshcoreClient()
    client.login_to_repeater("repeater-1", "admin123")
    client.logout_from_repeater("repeater-1")

    assert client.get_repeater_login_state("repeater-1") is None
    assert client.list_logged_in_repeaters() == []


def test_list_logged_in_repeaters() -> None:
    client = MockMeshcoreClient()
    client.login_to_repeater("rpt-a", "pw")
    client.login_to_repeater("rpt-b", "pw")

    logged_in = client.list_logged_in_repeaters()
    assert sorted(logged_in) == ["rpt-a", "rpt-b"]


def test_send_command() -> None:
    client = MockMeshcoreClient()
    client.login_to_repeater("repeater-1", "admin123")
    result = client.send_repeater_command("repeater-1", "status")

    assert result["success"] is True
    assert "response_text" in result
    assert len(result["response_text"]) > 0


def test_save_and_get_password() -> None:
    client = MockMeshcoreClient()
    client.login_to_repeater("repeater-1", "s3cret", save_password=True)

    assert client.get_saved_repeater_password("repeater-1") == "s3cret"


def test_delete_saved_password() -> None:
    client = MockMeshcoreClient()
    client.login_to_repeater("repeater-1", "s3cret", save_password=True)
    client.delete_saved_repeater_password("repeater-1")

    assert client.get_saved_repeater_password("repeater-1") is None


def test_login_emits_event() -> None:
    client = MockMeshcoreClient()
    client.login_to_repeater("repeater-1", "admin123")
    events = client.poll_events()

    login_events = [e for e in events if e.get("type") == "repeater_login"]
    assert len(login_events) >= 1
    assert login_events[-1]["data"]["peer_name"] == "repeater-1"
    assert login_events[-1]["data"]["is_admin"] is True
