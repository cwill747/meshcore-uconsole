"""Tests for DM routing path fix (Issue #98)."""

from __future__ import annotations

from meshcore_console.meshcore.operations import _text_message_contact


class FakeContact:
    """Minimal contact object matching openhop_core's Contact shape."""

    def __init__(self, out_path: bytes | list[int] | None = None) -> None:
        self.out_path = out_path


class TestTextMessageContact:
    """_text_message_contact normalises byte paths to list[int]."""

    def test_none_path(self) -> None:
        c = FakeContact(None)
        result = _text_message_contact(c)
        assert result.out_path is None
        # Same object when no normalisation needed
        assert result is c

    def test_list_path(self) -> None:
        c = FakeContact([1, 2, 3])
        result = _text_message_contact(c)
        assert result.out_path == [1, 2, 3]
        assert result is c

    def test_bytes_path(self) -> None:
        c = FakeContact(b"\x01\x02\x03")
        result = _text_message_contact(c)
        assert result.out_path == [1, 2, 3]
        # Shallow copy — different object
        assert result is not c

    def test_empty_bytes_path(self) -> None:
        c = FakeContact(b"")
        result = _text_message_contact(c)
        assert result.out_path == []

    def test_original_unchanged(self) -> None:
        """Original contact's bytes out_path must not be mutated."""
        c = FakeContact(b"\xde\xad")
        _text_message_contact(c)
        assert c.out_path == b"\xde\xad"
