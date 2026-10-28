"""A channel key can be given without putting it on the command line."""

from __future__ import annotations

import io

from meshcore_console import radio_cli


def test_secret_is_read_from_piped_stdin(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("00112233445566778899aabbccddeeff\n"))
    assert radio_cli.read_secret() == "00112233445566778899aabbccddeeff"


def test_secret_is_prompted_without_echo_on_a_terminal(monkeypatch) -> None:
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr("sys.stdin", Tty())
    monkeypatch.setattr(radio_cli.getpass, "getpass", lambda prompt: " aabb \n")
    assert radio_cli.read_secret() == "aabb"
