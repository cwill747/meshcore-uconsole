"""Remote repeater management tabs for the Admin view.

Overview, Neighbours, Settings and Access fetch from the selected repeater
over the air.  Nothing polls: a tab loads when it is first shown after a
login, and again only on Refresh/Load.  Each request takes seconds on
LoRa, so they all go through one RepeaterJobs queue of length one, shared
with the console (CLI replies carry no request id, so overlapping commands
could swap their answers).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable

import gi

gi.require_version("Adw", "1")
gi.require_version("Gtk", "4.0")

from gi.repository import Adw, GLib, Gtk, Pango

from meshcore_console.meshcore import repeater_settings as rs
from meshcore_console.ui_gtk.compat import build_alert_dialog, present_dialog
from meshcore_console.ui_gtk.helpers import clear_children

if TYPE_CHECKING:
    from meshcore_console.core.models import RepeaterLoginState
    from meshcore_console.core.services import MeshcoreService


# -- formatting (pure) ---------------------------------------------------------


def format_duration(secs: int) -> str:
    """3 d 5 h / 5 h 12 m / 12 m 3 s / 42 s."""
    secs = max(0, int(secs))
    days, rest = divmod(secs, 86400)
    hours, rest = divmod(rest, 3600)
    mins, s = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {mins} m"
    if mins:
        return f"{mins} m {s} s"
    return f"{s} s"


def status_rows(status: dict) -> list[tuple[str, str]]:
    """GET_STATUS counters as (label, value) rows, most useful first."""
    uptime = status.get("uptime", 0)
    tx_air, rx_air = status.get("airtime", 0), status.get("rx_airtime", 0)

    def share(air: int) -> str:
        return (
            f"{format_duration(air)} ({100 * air / uptime:.1f} %)"
            if uptime
            else format_duration(air)
        )

    return [
        ("Battery", f"{status.get('bat', 0) / 1000:.2f} V"),
        ("Uptime", format_duration(uptime)),
        ("TX airtime", share(tx_air)),
        ("RX airtime", share(rx_air)),
        ("Noise floor", f"{status.get('noise_floor', 0)} dBm"),
        (
            "Last RSSI / SNR",
            f"{status.get('last_rssi', 0)} dBm / {status.get('last_snr', 0):.1f} dB",
        ),
        (
            "Received",
            f"{status.get('nb_recv', 0)} (flood {status.get('recv_flood', 0)}, direct {status.get('recv_direct', 0)})",
        ),
        (
            "Sent",
            f"{status.get('nb_sent', 0)} (flood {status.get('sent_flood', 0)}, direct {status.get('sent_direct', 0)})",
        ),
        (
            "Duplicates",
            f"flood {status.get('flood_dups', 0)}, direct {status.get('direct_dups', 0)}",
        ),
        (
            "TX queue / full events",
            f"{status.get('tx_queue_len', 0)} / {status.get('full_evts', 0)}",
        ),
    ]


def sort_neighbours(neighbours: list[dict], by: str) -> list[dict]:
    """by: "heard" (most recent first) or "signal" (strongest first)."""
    if by == "signal":
        return sorted(neighbours, key=lambda n: -n.get("snr", 0))
    return sorted(neighbours, key=lambda n: n.get("secs_ago", 0))


# -- one request at a time -----------------------------------------------------


class RepeaterJobs:
    """Runs one repeater request at a time on a worker thread; ``done`` gets
    (result, error) on the GTK main loop.  ``on_idle`` runs after each request
    (a tab refused while busy loads then)."""

    def __init__(self, on_idle: Callable[[], None] | None = None) -> None:
        self.busy = False
        self._on_idle = on_idle

    def release(self) -> None:
        """A request finished (also for the Admin view's own login/console threads)."""
        self.busy = False
        if self._on_idle is not None:
            GLib.idle_add(lambda: self._on_idle() if not self.busy else None)

    def run(self, work: Callable[[], Any], done: Callable[[Any, str | None], None]) -> bool:
        if self.busy:
            return False
        self.busy = True

        def thread() -> None:
            result: Any = None
            error: str | None = None
            try:
                result = work()
            except Exception as exc:
                error = str(exc) or type(exc).__name__

            def finish() -> bool:
                self.release()
                done(result, error)
                return False

            GLib.idle_add(finish)

        threading.Thread(target=thread, daemon=True).start()
        return True


# -- shared tab plumbing -------------------------------------------------------


def _muted(text: str) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.add_css_class("panel-muted")
    label.set_halign(Gtk.Align.START)
    label.set_xalign(0.0)
    label.set_wrap(True)
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.set_size_request(10, -1)  # natural width must not follow the text (AGENTS.md)
    return label


class _RemoteTab(Gtk.Box):
    """A tab whose content comes from the selected repeater."""

    needs_admin = False
    what = ""  # for "Log in to see <what>."

    def __init__(self, service: MeshcoreService, jobs: RepeaterJobs) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self._service = service
        self._jobs = jobs
        self._peer_name: str | None = None
        self._state: RepeaterLoginState | None = None
        self._loaded = False
        self._auto_tried = False

        self._bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._note = _muted("")
        self._note.set_hexpand(True)
        self._bar.append(self._note)
        self.append(self._bar)

    # -- called by AdminView

    def set_repeater(self, peer_name: str | None, state: RepeaterLoginState | None) -> None:
        """A repeater was selected, or its login changed: forget what was shown."""
        changed = peer_name != self._peer_name or _login_key(state) != _login_key(self._state)
        self._peer_name, self._state = peer_name, state
        if changed:
            self._loaded = False
            self._auto_tried = False
            self._clear()
        allowed = self.allowed
        for child in self._actions():
            child.set_sensitive(allowed)
        if state is None:
            self._note.set_text(f"Log in to see {self.what}.")
        elif not allowed:
            self._note.set_text(f"Needs an admin login: {self.what} are admin only.")
        elif changed:
            self._note.set_text("")

    def on_shown(self) -> None:
        """The tab became visible: fetch once per login (after a failure, the
        user retries with Refresh; login-state updates don't re-send)."""
        if self.allowed and self.auto_load and not self._loaded and not self._auto_tried:
            # refused while busy (e.g. the login itself still running): the
            # Admin view calls on_shown() again when the request is done
            self._auto_tried = self.load()
            if not self._auto_tried:
                self._note.set_text("Waiting for the current request to finish...")

    @property
    def allowed(self) -> bool:
        return self._state is not None and (self._state.is_admin or not self.needs_admin)

    auto_load = True

    # -- for subclasses

    def load(self) -> bool:
        """Start fetching; False if refused because another request runs."""
        raise NotImplementedError

    def _clear(self) -> None:
        raise NotImplementedError

    def _actions(self) -> list[Gtk.Widget]:
        return []

    def _start(
        self, busy_text: str, work: Callable[[str], Any], done: Callable[[Any], None]
    ) -> bool:
        """Run ``work(peer_name)``; ``done(result)`` only if the same repeater
        is still selected."""
        peer_name = self._peer_name
        if peer_name is None:
            return False

        def finished(result: Any, error: str | None) -> None:
            if peer_name != self._peer_name:
                return
            for child in self._actions():
                child.set_sensitive(self.allowed)
            if error:
                self._note.set_text(f"Failed: {error}")
                return
            done(result)

        if not self._jobs.run(lambda: work(peer_name), finished):
            self._note.set_text("Busy with another request to a repeater; try again in a moment.")
            return False
        for child in self._actions():
            child.set_sensitive(False)
        self._note.set_text(busy_text)
        return True


def _login_key(state: RepeaterLoginState | None) -> tuple | None:
    return None if state is None else (state.is_admin,)


def _button(label: str, callback: Callable[[], None], css: str | None = None) -> Gtk.Button:
    button = Gtk.Button.new_with_label(label)
    if css:
        button.add_css_class(css)
    button.connect("clicked", lambda _b: callback())
    return button


def _grid() -> Gtk.Grid:
    grid = Gtk.Grid()
    grid.set_column_spacing(12)
    grid.set_row_spacing(4)
    return grid


def _cell(text: str, css: str | None = None, expand: bool = False) -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_halign(Gtk.Align.START)
    label.set_xalign(0.0)
    if css:
        label.add_css_class(css)
    if expand:
        label.set_hexpand(True)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_size_request(10, -1)
    return label


# -- Overview ------------------------------------------------------------------


class OverviewTab(_RemoteTab):
    """Live counters, firmware and owner info; clock and advert for admins."""

    what = "the repeater's status"

    def __init__(self, service: MeshcoreService, jobs: RepeaterJobs) -> None:
        super().__init__(service, jobs)
        self.set_margin_start(0)
        self.set_margin_end(0)
        self._refresh_btn = _button("Refresh", self.load)
        self._clock_btn = _button("Sync clock", self._sync_clock)
        self._clock_btn.set_tooltip_text("Set the repeater's clock to this device's time")
        self._advert_btn = _button("Send advert", self._send_advert)
        self._advert_btn.set_tooltip_text("The repeater floods an advert now")
        for button in (self._refresh_btn, self._clock_btn, self._advert_btn):
            self._bar.append(button)
        self._grid = _grid()
        self.append(self._grid)

    def set_repeater(self, peer_name: str | None, state: RepeaterLoginState | None) -> None:
        super().set_repeater(peer_name, state)
        admin = state is not None and state.is_admin
        self._clock_btn.set_visible(admin)
        self._advert_btn.set_visible(admin)

    def _actions(self) -> list[Gtk.Widget]:
        return [self._refresh_btn, self._clock_btn, self._advert_btn]

    def _clear(self) -> None:
        clear_children(self._grid)

    def load(self) -> bool:
        admin = self._state is not None and self._state.is_admin

        def work(peer: str) -> dict:
            status = self._service.get_repeater_status(peer)
            owner = self._service.get_repeater_owner_info(peer)
            clock = self._service.send_repeater_command(peer, "clock") if admin else None
            return {"status": status, "owner": owner, "clock": clock}

        return self._start("Asking the repeater...", work, self._show)

    def _show(self, result: dict) -> None:
        self._loaded = True
        self._clear()
        rows: list[tuple[str, str]] = []
        owner = result.get("owner")
        if owner:
            rows.append(("Firmware", owner.get("version") or "?"))
            if owner.get("owner_info"):
                rows.append(("Owner", owner["owner_info"]))
        clock = result.get("clock")
        if clock and clock.get("success") and clock.get("response_text"):
            rows.append(("Clock", clock["response_text"]))
        status = result.get("status")
        if status:
            rows.extend(status_rows(status))
        for i, (label, value) in enumerate(rows):
            self._grid.attach(_cell(label, "panel-muted"), 0, i, 1, 1)
            cell = _cell(value)
            cell.set_wrap(True)
            cell.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            cell.set_size_request(10, -1)
            cell.set_hexpand(True)
            cell.set_selectable(True)
            self._grid.attach(cell, 1, i, 1, 1)
        missing = [n for n, v in (("status", status), ("owner info", owner)) if not v]
        self._note.set_text(f"No reply for {' and '.join(missing)}." if missing else "")

    def _sync_clock(self) -> None:
        self._command("clock sync")

    def _send_advert(self) -> None:
        self._command("advert")

    def _command(self, command: str) -> None:
        def done(result: dict) -> None:
            text = result.get("response_text") if result.get("success") else None
            self._note.set_text(text or "No reply")

        self._start(
            f"Sending {command}...", lambda p: self._service.send_repeater_command(p, command), done
        )


# -- Neighbours ----------------------------------------------------------------


class NeighboursTab(_RemoteTab):
    """Who the repeater hears directly, with SNR and when it last heard them."""

    what = "the repeater's neighbours"

    def __init__(self, service: MeshcoreService, jobs: RepeaterJobs) -> None:
        super().__init__(service, jobs)
        self._neighbours: list[dict] = []
        self._sort = Gtk.DropDown.new_from_strings(["Last heard", "Signal"])
        self._sort.set_tooltip_text("Sort by")
        self._sort.connect("notify::selected", lambda *_a: self._render())
        self._refresh_btn = _button("Refresh", self.load)
        self._bar.append(self._sort)
        self._bar.append(self._refresh_btn)

        scroll = Gtk.ScrolledWindow.new()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._list = Gtk.ListBox.new()
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll.set_child(self._list)
        self.append(scroll)

    def _actions(self) -> list[Gtk.Widget]:
        return [self._refresh_btn]

    def _clear(self) -> None:
        self._neighbours = []
        clear_children(self._list)

    def load(self) -> bool:
        return self._start(
            "Fetching neighbours...", self._service.get_repeater_neighbours, self._show
        )

    def _show(self, neighbours: list[dict] | None) -> None:
        if neighbours is None:
            self._note.set_text("No reply.")
            return
        self._loaded = True
        self._neighbours = neighbours
        self._note.set_text(f"{len(neighbours)} neighbour{'s' if len(neighbours) != 1 else ''}")
        self._render()

    def _render(self) -> None:
        clear_children(self._list)
        by = "signal" if self._sort.get_selected() == 1 else "heard"
        admin = self._state is not None and self._state.is_admin
        for n in sort_neighbours(self._neighbours, by):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.add_css_class("admin-repeater-row")
            name = _cell(n.get("name") or n["pubkey"], expand=True)
            name.set_tooltip_text(n["pubkey"])
            if not n.get("name"):
                name.add_css_class("panel-muted")
            row.append(name)
            row.append(_cell(f"{n.get('snr', 0):+.1f} dB"))
            row.append(_cell(f"{format_duration(n.get('secs_ago', 0))} ago", "panel-muted"))
            if admin:
                remove = Gtk.Button.new_from_icon_name("user-trash-symbolic")
                remove.add_css_class("flat")
                remove.set_tooltip_text("Remove from the repeater's neighbour list")
                remove.connect("clicked", lambda _b, key=n["pubkey"]: self._remove(key))
                row.append(remove)
            self._list.append(row)

    def _remove(self, key: str) -> None:
        def done(result: dict) -> None:
            if result.get("success") and rs.set_succeeded(result.get("response_text")):
                self._neighbours = [n for n in self._neighbours if n["pubkey"] != key]
                self._note.set_text("Removed; it comes back when the repeater hears it again.")
                self._render()
            else:
                self._note.set_text(result.get("response_text") or "No reply")

        self._start(
            "Removing...",
            lambda p: self._service.send_repeater_command(p, f"neighbor.remove {key}"),
            done,
        )


# -- Access list ---------------------------------------------------------------


class AccessTab(_RemoteTab):
    """Who may log in (read-only; keys are 6-byte prefixes)."""

    needs_admin = True
    what = "access lists"

    def __init__(self, service: MeshcoreService, jobs: RepeaterJobs) -> None:
        super().__init__(service, jobs)
        self._refresh_btn = _button("Refresh", self.load)
        self._bar.append(self._refresh_btn)
        self._grid = _grid()
        self.append(self._grid)
        hint = _muted("Change permissions from the console: setperm <pubkey> <0-3>.")
        self.append(hint)

    def _actions(self) -> list[Gtk.Widget]:
        return [self._refresh_btn]

    def _clear(self) -> None:
        clear_children(self._grid)

    def load(self) -> bool:
        return self._start(
            "Fetching the access list...", self._service.get_repeater_access_list, self._show
        )

    def _show(self, entries: list[dict] | None) -> None:
        if entries is None:
            self._note.set_text("No reply.")
            return
        self._loaded = True
        self._clear()
        me = (self._service.get_self_public_key() or "")[:12]
        for i, e in enumerate(entries):
            who = e.get("name") or e["key"]
            if me and e["key"] == me:
                who += " (you)"
            name = _cell(who, expand=True)
            name.set_tooltip_text(e["key"])
            self._grid.attach(name, 0, i, 1, 1)
            self._grid.attach(_cell(e["role"], "panel-muted"), 1, i, 1, 1)
        self._note.set_text(f"{len(entries)} entr{'ies' if len(entries) != 1 else 'y'}")


# -- Settings ------------------------------------------------------------------


class _Field:
    """One setting's row: the editor widget, and the value last read from the repeater."""

    def __init__(self, setting: rs.RepeaterSetting) -> None:
        self.setting = setting
        self.loaded: str | None = None
        self.label = _cell(setting.label)
        self.label.set_tooltip_text(setting.help or setting.key)
        self.result = _muted("")
        self.widget = self._make_widget()
        self.label.set_mnemonic_widget(self.widget)

    def _make_widget(self) -> Gtk.Widget:
        s = self.setting
        if s.kind == "onoff":
            switch = Gtk.Switch()
            switch.set_halign(Gtk.Align.START)
            return switch
        if s.kind == "choice":
            return Gtk.DropDown.new_from_strings(list(s.choices))
        if s.key == "owner.info":
            view = Gtk.TextView()
            view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            view.set_size_request(10, 54)
            view.add_css_class("cli-entry")
            return view
        entry = Gtk.PasswordEntry() if s.kind == "secret" else Gtk.Entry()
        if s.kind == "secret":
            entry.set_show_peek_icon(True)
            entry.set_property("placeholder-text", "unchanged")
        else:
            entry.set_width_chars(8 if s.kind in ("int", "float") else 16)
            if s.unit:
                entry.set_placeholder_text(s.unit)
        entry.set_hexpand(True)
        return entry

    def show(self, value: str) -> None:
        self.loaded = value
        w, s = self.widget, self.setting
        if isinstance(w, Gtk.Switch):
            w.set_active(value == "on")
        elif isinstance(w, Gtk.DropDown):
            w.set_selected(
                s.choices.index(value) if value in s.choices else Gtk.INVALID_LIST_POSITION
            )
        elif isinstance(w, Gtk.TextView):
            w.get_buffer().set_text(rs.display_value(s, value))
        else:
            w.set_text(value)

    def text(self) -> str:
        w, s = self.widget, self.setting
        if isinstance(w, Gtk.Switch):
            return "on" if w.get_active() else "off"
        if isinstance(w, Gtk.DropDown):
            i = w.get_selected()
            return s.choices[i] if i < len(s.choices) else ""
        if isinstance(w, Gtk.TextView):
            buf = w.get_buffer()
            return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
        return w.get_text()

    def change(self) -> str | None:
        """The value to send, None if unchanged; ValueError if invalid."""
        return rs.changed_value(self.setting, self.loaded, self.text())

    def set_result(self, text: str, error: bool = False) -> None:
        self.result.set_text(text)
        if error:
            self.result.add_css_class("cli-error")
        else:
            self.result.remove_css_class("cli-error")


class _Group(Gtk.Box):
    """A settings group: loaded and applied on its own (each key is a round trip)."""

    def __init__(self, tab: SettingsTab, title: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._tab = tab
        self.title = title
        self.fields = [_Field(s) for s in rs.SETTINGS if s.group == title]
        self.loaded = False

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        heading = Gtk.Label(label=title)
        heading.add_css_class("message-detail-header")
        heading.set_halign(Gtk.Align.START)
        heading.set_hexpand(True)
        head.append(heading)
        self.load_btn = _button("Load", lambda: tab.load_group(self))
        self.apply_btn = _button("Apply", lambda: tab.apply_group(self), "suggested-action")
        self.apply_btn.set_sensitive(False)
        head.append(self.load_btn)
        head.append(self.apply_btn)
        self.append(head)
        self.extra = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.extra.set_visible(False)
        self.append(self.extra)

        self.note = _muted("")
        self.append(self.note)
        self.grid = _grid()
        self.grid.set_visible(False)
        for i, f in enumerate(self.fields):
            self.grid.attach(f.label, 0, 2 * i, 1, 1)
            self.grid.attach(f.widget, 1, 2 * i, 1, 1)
            self.grid.attach(f.result, 1, 2 * i + 1, 1, 1)
        self.append(self.grid)

    def reset(self) -> None:
        self.loaded = False
        self.apply_btn.set_sensitive(False)
        self.grid.set_visible(False)
        self.note.set_text("")
        for f in self.fields:
            f.loaded = None
            f.set_result("")
            for w in (f.label, f.widget, f.result):
                w.set_visible(True)


class SettingsTab(_RemoteTab):
    """Grouped settings forms; Apply sends only what changed, key by key."""

    needs_admin = True
    what = "settings"
    auto_load = False  # ~2-3 s per key: the user loads the groups they want

    def __init__(self, service: MeshcoreService, jobs: RepeaterJobs) -> None:
        super().__init__(service, jobs)
        self._note.set_text("")
        scroll = Gtk.ScrolledWindow.new()
        scroll.set_vexpand(True)
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_margin_end(8)
        self._groups = [_Group(self, title) for title in rs.GROUPS]
        for group in self._groups:
            box.append(group)
        box.append(self._build_admin_password())
        scroll.set_child(box)
        self.append(scroll)

        identity = self._groups[0]
        self._gps_btn = _button("Use my position", self._use_my_position)
        self._gps_btn.set_tooltip_text("Fill latitude/longitude from this device's GPS")
        identity.extra.append(self._gps_btn)
        self._advert_check = Gtk.CheckButton.new_with_label("Advert after apply")
        self._advert_check.set_tooltip_text(
            "Flood an advert so others see the new name or position"
        )
        self._advert_check.set_active(True)
        identity.extra.append(self._advert_check)
        identity.extra.set_visible(True)

    def _build_admin_password(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        heading = Gtk.Label(label="Admin password")
        heading.add_css_class("message-detail-header")
        heading.set_halign(Gtk.Align.START)
        heading.set_hexpand(True)
        head.append(heading)
        self._pw_btn = _button("Change", self._change_admin_password, "destructive-action")
        head.append(self._pw_btn)
        box.append(head)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._pw1, self._pw2 = Gtk.PasswordEntry(), Gtk.PasswordEntry()
        for entry, hint in ((self._pw1, "new password"), (self._pw2, "again")):
            entry.set_property("placeholder-text", hint)
            entry.set_hexpand(True)
            row.append(entry)
        box.append(row)
        self._pw_note = _muted("A typo here locks you out until someone resets it on site.")
        box.append(self._pw_note)
        return box

    def _actions(self) -> list[Gtk.Widget]:
        widgets: list[Gtk.Widget] = [self._pw_btn, self._gps_btn]
        for g in self._groups:
            widgets.append(g.load_btn)
            if g.loaded:
                widgets.append(g.apply_btn)
        return widgets

    def _clear(self) -> None:
        for g in self._groups:
            g.reset()
        self._pw1.set_text("")
        self._pw2.set_text("")

    def load(self) -> bool:
        return False  # per group, from its Load button

    # -- load / apply

    def load_group(self, group: _Group) -> None:
        keys = [f.setting.key for f in group.fields if f.setting.kind != "secret"]

        def done(result: rs.ReadResult) -> None:
            group.grid.set_visible(True)
            for f in group.fields:
                f.set_result("")
                key = f.setting.key
                if key in result.unsupported:
                    for w in (f.label, f.widget, f.result):
                        w.set_visible(False)
                elif key in result.values:
                    f.show(result.values[key])
            group.loaded = result.failed is None
            group.apply_btn.set_sensitive(group.loaded)
            if result.failed:
                group.note.set_text(f"No usable reply for {result.failed}; stopped. Load again.")
            else:
                group.note.set_text("")
            self._note.set_text("")

        self._start(
            f"Reading {group.title.lower()}...",
            lambda peer: rs.read_settings(
                lambda c: self._service.send_repeater_command(peer, c), keys
            ),
            done,
        )

    def apply_group(self, group: _Group) -> None:
        changes: list[tuple[str, str]] = []
        bad = False
        for f in group.fields:
            if not f.widget.get_visible():
                continue
            try:
                value = f.change()
            except ValueError as exc:
                f.set_result(str(exc), error=True)
                bad = True
                continue
            f.set_result("")
            if value is not None:
                changes.append((rs.set_command(f.setting, value), f.setting.key))
        if bad:
            group.note.set_text("Fix the marked values first.")
            return
        if not changes:
            group.note.set_text("Nothing changed.")
            return
        if group.title == rs.IDENTITY and self._advert_check.get_active():
            changes.append(("advert", "advert"))
        dangerous = [k for _c, k in changes if k in rs.BY_KEY and rs.BY_KEY[k].dangerous]
        if dangerous:
            self._confirm(
                "Change the radio parameters?",
                "They take effect when the repeater reboots. If they don't match "
                "this device's, you lose contact with the repeater until they are "
                "fixed on site.",
                "Change",
                lambda: self._send_changes(group, changes),
            )
        else:
            self._send_changes(group, changes)

    def _send_changes(self, group: _Group, changes: list[tuple[str, str]]) -> None:
        by_key = {f.setting.key: f for f in group.fields}
        sent = {k: c.split(" ", 2)[2] for c, k in changes if c.startswith("set ")}

        def done(results: list[rs.ApplyResult]) -> None:
            note = ""
            for r in results:
                field = by_key.get(r.key)
                if field is None:  # the advert
                    note = r.reply
                elif not r.ok:
                    field.set_result(r.reply, error=True)
                else:
                    field.set_result("Saved" if r.reply == "OK" else r.reply)
                    if field.setting.kind == "secret":
                        field.widget.set_text("")
                    else:
                        field.loaded = sent[r.key]
            if len(results) < len(changes):
                note = "The repeater stopped answering; the rest was not sent."
            group.note.set_text(note)
            self._note.set_text("")

        self._start(
            "Applying...",
            lambda peer: rs.apply_settings(
                lambda c: self._service.send_repeater_command(peer, c), changes
            ),
            done,
        )

    def _use_my_position(self) -> None:
        position = self._service.get_device_location()
        identity = self._groups[0]
        if position is None:
            identity.note.set_text(
                "This device has no position (no GPS fix, none set in Settings)."
            )
            return
        fields = {f.setting.key: f for f in identity.fields}
        fields["lat"].widget.set_text(f"{position[0]:.6f}")
        fields["lon"].widget.set_text(f"{position[1]:.6f}")
        identity.note.set_text("Position filled in; Apply sends it.")

    def _change_admin_password(self) -> None:
        pw1, pw2 = self._pw1.get_text(), self._pw2.get_text()
        if pw1 != pw2:
            self._pw_note.set_text("The two entries differ.")
            return
        try:
            command = rs.admin_password_command(pw1)
        except ValueError as exc:
            self._pw_note.set_text(f"Password: {exc}")
            return
        self._confirm(
            "Change the admin password?",
            "Your current session stays logged in. Log in with the new password next time.",
            "Change",
            lambda: self._send_password(command),
        )

    def _send_password(self, command: str) -> None:
        peer = self._peer_name

        def done(result: dict) -> None:
            text = result.get("response_text") if result.get("success") else None
            # firmware echoes the new password ("password now: ..."); don't show it
            if text and text.startswith("password now"):
                self._pw1.set_text("")
                self._pw2.set_text("")
                note = "Changed."
                if peer and self._service.get_saved_repeater_password(peer):
                    self._service.delete_saved_repeater_password(peer)
                    note += " The saved (old) password was removed."
                self._pw_note.set_text(note)
            else:
                self._pw_note.set_text(
                    "No confirmation from the repeater; check before logging out."
                )
            self._note.set_text("")

        self._start(
            "Changing the admin password...",
            lambda p: self._service.send_repeater_command(p, command),
            done,
        )

    def _confirm(self, heading: str, body: str, verb: str, then: Callable[[], None]) -> None:
        dialog = build_alert_dialog(self.get_root(), heading, body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("go", verb)
        dialog.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: then() if response == "go" else None)
        present_dialog(dialog, self.get_root())
