"""The headless CLI radios with the saved settings, like the GTK app."""

from __future__ import annotations

from meshcore_console.meshcore.db import open_db
from meshcore_console.meshcore.settings import MeshcoreSettings
from meshcore_console.meshcore.settings_store import SettingsStore
from meshcore_console.radio_cli import load_saved_runtime_config


def save(**values) -> None:
    conn = open_db()
    settings = MeshcoreSettings()
    for key, value in values.items():
        setattr(settings, key, value)
    SettingsStore(conn).save(settings)
    conn.close()


def test_cli_uses_saved_radio_and_name() -> None:
    save(node_name="DF9ES", frequency=869_618_000, spreading_factor=8, coding_rate=8, gpio_chip=15)

    config = load_saved_runtime_config()

    assert config.node_name == "DF9ES"
    hw = config.hardware
    assert (hw.frequency, hw.spreading_factor, hw.coding_rate, hw.gpio_chip) == (
        869_618_000,
        8,
        8,
        15,
    )


def test_cli_name_override() -> None:
    save(node_name="DF9ES")
    assert load_saved_runtime_config("bench").node_name == "bench"


def test_cli_send_knows_the_stored_peers() -> None:
    from meshcore_console.core.models import Peer
    from meshcore_console.meshcore.state_store import PeerStore
    from meshcore_console.radio_cli import seed_contacts

    conn = open_db()
    PeerStore(conn).add_or_update(
        Peer(peer_id="aa" * 8, display_name="Bergblick", public_key="aa" * 32)
    )
    conn.close()

    class Session:
        from meshcore_console.meshcore.contact_book import ContactBook

        contact_book = ContactBook()

    session = Session()
    seed_contacts(session)
    assert session.contact_book.get_by_name("Bergblick").public_key == "aa" * 32


def test_listen_duration_ends_on_a_quiet_channel() -> None:
    import asyncio
    import time

    from meshcore_console.radio_cli import _run_listen

    class QuietSession:
        stopped = False

        async def start(self) -> None: ...

        def status(self) -> dict:
            return {"connected": True}

        async def listen_events(self):
            await asyncio.sleep(3600)  # nothing ever arrives
            yield {}

        async def stop(self) -> None:
            QuietSession.stopped = True

    started = time.monotonic()
    assert asyncio.run(_run_listen(QuietSession(), 1, False, 5.0)) == 0
    assert time.monotonic() - started < 3 and QuietSession.stopped
