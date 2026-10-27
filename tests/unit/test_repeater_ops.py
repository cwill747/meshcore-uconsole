"""Repeater operations against openhop_core 1.1.3's handler API.

Login, CLI and telemetry once called handler methods that openhop_core 1.1
no longer has (set_login_callback, set_command_response_callback, a 2-arg
set_response_callback), so every one raised before transmitting.  These tests
pin the real signatures and drive each operation through fakes that only offer
those methods.
"""

from __future__ import annotations

import asyncio
import inspect
import struct

from openhop_core.node.handlers.login_response import LoginResponseHandler
from openhop_core.node.handlers.protocol_response import ProtocolResponseHandler
from openhop_core.node.handlers.text import TextMessageHandler
from openhop_core.protocol.identity import LocalIdentity

from meshcore_console.meshcore.contact_book import Contact, ContactBook
from meshcore_console.meshcore.discovery import decode_owner_reply
from meshcore_console.meshcore.operations import (
    request_owner_info,
    request_telemetry,
    send_login,
    send_logout,
    send_repeater_command,
)


def params(fn) -> list[str]:
    return list(inspect.signature(fn).parameters)[1:]  # drop self


def test_handler_api_is_what_operations_calls() -> None:
    assert params(ProtocolResponseHandler.set_response_callback) == [
        "contact_pubkey",
        "request_tag",
        "callback",
    ]
    assert params(ProtocolResponseHandler.clear_response_callback) == [
        "contact_pubkey",
        "request_tag",
    ]
    assert params(LoginResponseHandler.register_login_callback) == ["pubkey", "callback"]
    assert params(LoginResponseHandler.remove_login_callback) == ["pubkey", "callback"]
    assert params(TextMessageHandler.register_command_response) == ["pubkey", "callback"]
    assert params(TextMessageHandler.unregister_command_response) == ["pubkey", "callback"]
    for gone in ("set_login_callback", "set_command_response_callback"):
        assert not hasattr(LoginResponseHandler, gone)
        assert not hasattr(TextMessageHandler, gone)


class FakeResponseHandler:
    def __init__(self, parsed: dict | None) -> None:
        self.parsed = parsed
        self.waiters: dict[tuple[bytes, int], object] = {}
        self.registered: list[tuple[bytes, int]] = []

    def set_response_callback(self, contact_pubkey, request_tag, callback) -> None:
        self.waiters[(bytes(contact_pubkey), request_tag)] = callback
        self.registered.append((bytes(contact_pubkey), request_tag))

    def clear_response_callback(self, contact_pubkey, request_tag) -> None:
        self.waiters.pop((bytes(contact_pubkey), request_tag), None)

    def reply(self) -> None:
        if self.parsed is not None:
            for callback in list(self.waiters.values()):
                callback(True, "reply", self.parsed)


class FakeLoginHandler:
    def __init__(self, succeed: bool) -> None:
        self.succeed = succeed
        self.pending: dict[bytes, object] = {}
        self.passwords: dict[int, str] = {}

    def register_login_callback(self, pubkey, callback) -> None:
        self.pending[bytes(pubkey)] = callback

    def remove_login_callback(self, pubkey, callback) -> None:
        if self.pending.get(bytes(pubkey)) is callback:
            del self.pending[bytes(pubkey)]

    def store_login_password(self, dest_hash, password) -> None:
        self.passwords[dest_hash] = password

    def clear_login_password(self, dest_hash) -> None:
        self.passwords.pop(dest_hash, None)

    def reply(self) -> None:
        if self.succeed:
            for callback in list(self.pending.values()):
                callback(True, {"is_admin": True, "keep_alive_interval": 0})


class FakeTextHandler:
    def __init__(self, contact: Contact) -> None:
        self.contact = contact
        self.pending: dict[bytes, object] = {}

    def register_command_response(self, pubkey, callback) -> None:
        self.pending[bytes(pubkey)] = callback

    def unregister_command_response(self, pubkey, callback) -> None:
        if self.pending.get(bytes(pubkey)) is callback:
            del self.pending[bytes(pubkey)]

    def reply(self) -> None:
        for callback in list(self.pending.values()):
            callback("v1.18.0", self.contact)


class FakeDispatcher:
    def __init__(self, handlers: list, fail: bool = False) -> None:
        self.handlers = handlers
        self.fail = fail
        self.sent: list = []

    async def send_packet(self, packet, wait_for_ack=False, **_kw) -> bool:
        if self.fail:
            raise OSError("radio gone")
        self.sent.append(packet)
        for handler in self.handlers:
            handler.reply()
        return True


class FakeNode:
    def __init__(self, contact: Contact) -> None:
        self.identity = LocalIdentity()
        self.contacts = ContactBook()
        self.contacts.add_contact(contact)


def repeater_contact() -> tuple[Contact, bytes]:
    key = LocalIdentity().get_public_key()
    return Contact(name="Bergblick", public_key=key.hex(), out_path=[], out_path_len=0), key


def run(coro):
    return asyncio.run(coro)


def test_telemetry_waits_on_pubkey_and_reflected_tag() -> None:
    contact, key = repeater_contact()
    node = FakeNode(contact)
    rh = FakeResponseHandler({"type": "telemetry", "sensors": []})
    node.dispatcher = FakeDispatcher([rh])
    node.dispatcher.protocol_response_handler = rh

    result = run(request_telemetry(node=node, contact_name="Bergblick", timeout=1))

    assert result["success"] is True
    assert rh.registered[0][0] == key
    assert rh.waiters == {}  # cleared


def test_login_registers_by_pubkey_and_always_clears_the_password() -> None:
    contact, key = repeater_contact()
    node = FakeNode(contact)
    lh = FakeLoginHandler(succeed=True)
    node.dispatcher = FakeDispatcher([lh])
    node.dispatcher.login_response_handler = lh

    result = run(send_login(node=node, peer_name="Bergblick", password="pw", timeout=1))
    assert result["success"] is True and result["is_admin"] is True
    assert lh.pending == {} and lh.passwords == {}

    lh_fail = FakeLoginHandler(succeed=False)
    node.dispatcher = FakeDispatcher([lh_fail], fail=True)
    node.dispatcher.login_response_handler = lh_fail
    try:
        run(send_login(node=node, peer_name="Bergblick", password="pw", timeout=1))
    except OSError:
        pass
    assert lh_fail.pending == {} and lh_fail.passwords == {}  # no leaked password
    assert key[0] not in lh_fail.passwords


def test_cli_command_registers_by_pubkey() -> None:
    contact, _key = repeater_contact()
    node = FakeNode(contact)
    th = FakeTextHandler(contact)
    node.dispatcher = FakeDispatcher([th])
    node.dispatcher.text_message_handler = th

    result = run(send_repeater_command(node=node, peer_name="Bergblick", command="ver", timeout=1))

    assert result["success"] is True and result["response_text"] == "v1.18.0"
    assert th.pending == {}


def test_logout_is_local_only() -> None:
    contact, _key = repeater_contact()
    node = FakeNode(contact)
    node.dispatcher = FakeDispatcher([])
    assert run(send_logout(node=node, peer_name="Bergblick"))["success"] is True
    assert node.dispatcher.sent == []


def owner_reply(name: bytes, owner: bytes = b"Erik") -> bytes:
    return struct.pack("<II", 1234, 5678) + name + b"\n" + owner


def test_owner_query_is_a_direct_anon_request() -> None:
    contact, key = repeater_contact()
    node = FakeNode(contact)
    rh = FakeResponseHandler({"type": "binary", "hex": owner_reply(b"Bergblick").hex()})
    node.dispatcher = FakeDispatcher([rh])
    node.dispatcher.protocol_response_handler = rh

    parsed = run(request_owner_info(node=node, contact=contact, timeout=1))

    assert decode_owner_reply(parsed) == ("Bergblick", "Erik")
    assert node.dispatcher.sent[0].is_route_direct()  # firmware answers direct only
    assert rh.registered[0][0] == key and rh.waiters == {}


def test_owner_query_times_out_to_none() -> None:
    contact, _key = repeater_contact()
    node = FakeNode(contact)
    rh = FakeResponseHandler(None)
    node.dispatcher = FakeDispatcher([rh])
    node.dispatcher.protocol_response_handler = rh
    assert run(request_owner_info(node=node, contact=contact, timeout=0.05)) is None
    assert rh.waiters == {}


def test_decode_owner_reply_wherever_openhop_put_the_bytes() -> None:
    data = owner_reply("Bergblick Süd".encode())
    assert decode_owner_reply({"type": "binary", "hex": data.hex()}) == ("Bergblick Süd", "Erik")
    assert decode_owner_reply({"type": "telemetry", "raw_bytes": data[4:]})[0] == "Bergblick Süd"
    assert decode_owner_reply({"type": "binary", "hex": owner_reply(b"", b"").hex()}) is None
    assert decode_owner_reply({"type": "binary", "hex": owner_reply(b"x" * 40).hex()}) is None
    assert decode_owner_reply({}) is None


def test_dm_floods_while_the_route_is_unknown() -> None:
    from meshcore_console.meshcore.operations import send_text

    contact, _key = repeater_contact()
    contact.out_path_len = -1  # no route learned yet
    node = FakeNode(contact)
    node.dispatcher = FakeDispatcher([])
    run(send_text(node=node, peer_name="Bergblick", message="hi"))
    assert not node.dispatcher.sent[0].is_route_direct()

    contact.out_path_len = 0  # direct neighbour
    run(send_text(node=node, peer_name="Bergblick", message="hi"))
    assert node.dispatcher.sent[1].is_route_direct()


class AckAfter:
    """send_packet that ACKs only from the n-th attempt on."""

    def __init__(self, ack_from: int) -> None:
        self.ack_from = ack_from
        self.sent: list = []
        self.crcs: list[int] = []

    async def send_packet(self, packet, wait_for_ack=False, expected_crc=None, **_kw) -> bool:
        self.sent.append(packet)
        self.crcs.append(expected_crc)
        return len(self.sent) >= self.ack_from


def test_dm_retries_then_floods_when_the_direct_route_fails() -> None:
    from meshcore_console.meshcore.operations import send_text

    contact, _key = repeater_contact()  # direct, zero-hop route known
    node = FakeNode(contact)
    node.dispatcher = AckAfter(ack_from=3)

    result = run(send_text(node=node, peer_name="Bergblick", message="hi"))

    assert result["success"] is True and result["attempts"] == 3
    sent = node.dispatcher.sent
    assert sent[0].is_route_direct() and sent[1].is_route_direct()
    assert not sent[2].is_route_direct()  # stale route dropped, last try floods
    assert node.contacts.get_by_name("Bergblick").out_path_len == -1
    assert len(set(node.dispatcher.crcs)) == 3  # each attempt expects its own ACK


def test_dm_reports_no_ack() -> None:
    from meshcore_console.meshcore.operations import send_text

    contact, _key = repeater_contact()
    node = FakeNode(contact)
    node.dispatcher = AckAfter(ack_from=99)
    result = run(send_text(node=node, peer_name="Bergblick", message="hi"))
    assert result == {"success": False, "attempts": 3, "crc": node.dispatcher.crcs[-1]}
