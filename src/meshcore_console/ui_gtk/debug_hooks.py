"""Global GTK signal emission hooks for UI interaction logging.

Hooks into GObject signal emissions at the type level, so every widget
instance is covered automatically — no per-handler instrumentation needed.

All output goes through ``logging.debug``, so it always appears in the
rotating log file (which is pinned to DEBUG) and on stderr only when the
user selects DEBUG in the settings page.

Call :func:`install` once during app startup (idempotent).
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk

logger = logging.getLogger("meshcore_console.ui_gtk.interactions")

_installed = False


# ---------------------------------------------------------------------------
# Widget identification helpers
# ---------------------------------------------------------------------------


def _widget_label(widget: Gtk.Widget) -> str:
    """Try to extract a human-readable label from a widget."""
    # Direct label property (Button, ToggleButton, etc.)
    if hasattr(widget, "get_label"):
        text = widget.get_label()
        if text:
            return text

    # Child label (icon buttons with a Label child)
    if hasattr(widget, "get_child"):
        child = widget.get_child()
        if isinstance(child, Gtk.Label):
            return child.get_text() or ""
        # Box with mixed children (icon + label)
        if isinstance(child, Gtk.Box):
            c = child.get_first_child()
            while c is not None:
                if isinstance(c, Gtk.Label):
                    t = c.get_text()
                    if t:
                        return t
                c = c.get_next_sibling()

    return ""


def _describe(widget: Gtk.Widget) -> str:
    """Build a short, informative description of a widget."""
    parts: list[str] = [type(widget).__name__]

    label = _widget_label(widget)
    if label:
        parts.append(repr(label))

    tip = widget.get_tooltip_text()
    if tip and tip != label:
        parts.append(f"tip={tip!r}")

    css = [
        c
        for c in widget.get_css_classes()
        if c not in ("flat", "circular", "text-button", "image-button")
    ]
    if css:
        parts.append(f"[{','.join(css)}]")

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Emission hook callbacks
# ---------------------------------------------------------------------------


def _on_button_clicked(_ihint: object, param_values: list[object]) -> bool:
    widget = param_values[0]
    if isinstance(widget, Gtk.ToggleButton):
        return True  # logged by the toggled hook instead
    logger.debug("clicked: %s", _describe(widget))
    return True


def _on_toggle_toggled(_ihint: object, param_values: list[object]) -> bool:
    widget = param_values[0]
    logger.debug("toggled: %s active=%s", _describe(widget), widget.get_active())
    return True


def _on_row_selected(_ihint: object, param_values: list[object]) -> bool:
    listbox = param_values[0]
    row = param_values[1] if len(param_values) > 1 else None
    if row is None:
        logger.debug("row-selected: %s row=None", _describe(listbox))
    else:
        idx = row.get_index()
        # Try to pull a domain ID stashed on the row (channel_id, _peer, etc.)
        extra = ""
        for attr in ("channel_id", "peer"):
            val = getattr(row, attr, None) or getattr(row, f"_{attr}", None)
            if val is not None:
                name = getattr(val, "display_name", None) or val
                extra = f" {attr}={name}"
                break
        logger.debug("row-selected: %s row=%d%s", _describe(listbox), idx, extra)
    return True


def _on_entry_activate(_ihint: object, param_values: list[object]) -> bool:
    widget = param_values[0]
    logger.debug("activate: %s", _describe(widget))
    return True


def _on_dialog_response(_ihint: object, param_values: list[object]) -> bool:
    dialog = param_values[0]
    response = param_values[1] if len(param_values) > 1 else "?"
    title = ""
    if hasattr(dialog, "get_heading"):
        title = dialog.get_heading() or ""
    logger.debug("dialog-response: %s response=%s", title or _describe(dialog), response)
    return True


def _on_switch_state(_ihint: object, param_values: list[object]) -> bool:
    widget = param_values[0]
    state = param_values[1] if len(param_values) > 1 else "?"
    logger.debug("switch: %s state=%s", _describe(widget), state)
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install() -> None:
    """Register emission hooks for common interactive GTK signals.

    Safe to call multiple times — hooks are installed only once.
    """
    global _installed  # noqa: PLW0603
    if _installed:
        return

    hooks: list[tuple[type, str, object]] = [
        (Gtk.Button, "clicked", _on_button_clicked),
        (Gtk.ToggleButton, "toggled", _on_toggle_toggled),
        (Gtk.ListBox, "row-selected", _on_row_selected),
        (Gtk.Entry, "activate", _on_entry_activate),
        (Gtk.Switch, "state-set", _on_switch_state),
    ]

    # Adw.AlertDialog.response (Adw ≥ 1.2)
    if hasattr(Adw, "AlertDialog"):
        hooks.append((Adw.AlertDialog, "response", _on_dialog_response))

    for widget_type, signal_name, callback in hooks:
        sig_id = GObject.signal_lookup(signal_name, widget_type)
        if sig_id:
            GObject.signal_add_emission_hook(sig_id, 0, callback)
            logger.debug("emission hook: %s.%s", widget_type.__name__, signal_name)

    _installed = True
