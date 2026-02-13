from meshcore_console.core.models import DeviceStatus, Message, Peer


def test_models_construct() -> None:
    status = DeviceStatus(node_id="node-a", connected=True)
    peer = Peer(peer_id="peer-a", display_name="Peer A")
    message = Message(message_id="msg-a", sender_id="peer-a", body="hello")

    assert status.node_id == "node-a"
    assert peer.display_name == "Peer A"
    assert message.body == "hello"
