"""The Messages view finds new messages by id, not by count."""

from __future__ import annotations

from meshcore_console.core.models import Message
from meshcore_console.ui_gtk.views.messages import messages_after


def window(first: int, last: int) -> list[Message]:
    return [Message(message_id=f"m{i}", sender_id="Bob", body=str(i)) for i in range(first, last)]


def test_new_messages_after_a_full_window() -> None:
    # 100 shown (m0..m99); one arrives: the newest-100 window is m1..m100
    assert [m.message_id for m in messages_after(window(1, 101), "m99")] == ["m100"]


def test_nothing_new() -> None:
    assert messages_after(window(0, 100), "m99") == []


def test_everything_is_new_when_nothing_was_shown() -> None:
    assert len(messages_after(window(0, 3), None)) == 3


def test_rebuild_when_the_last_shown_message_left_the_window() -> None:
    assert messages_after(window(150, 250), "m99") is None  # >100 arrived at once
    assert messages_after([], "m99") is None  # channel cleared
