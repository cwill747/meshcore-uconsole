from meshcore_console.mock import MockMeshcoreClient
from meshcore_console.meshcore.state import AppState


def test_refresh_populates_state() -> None:
    service = MockMeshcoreClient()
    state = AppState()

    state.refresh(service)

    assert state.status is not None
    assert state.status.connected is True
    assert len(state.peers) >= 1
