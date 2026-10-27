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

from openhop_core.node.handlers.login_response import LoginResponseHandler
from openhop_core.node.handlers.protocol_response import ProtocolResponseHandler
from openhop_core.node.handlers.text import TextMessageHandler
from openhop_core.protocol.identity import LocalIdentity

from meshcore_console.meshcore.contact_book import Contact, ContactBook
from meshcore_console.meshcore.operations import (
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
