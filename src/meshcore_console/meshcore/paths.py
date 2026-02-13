from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "meshcore-uconsole"


def xdg_config_home() -> Path:
    """Return XDG_CONFIG_HOME or default ~/.config"""
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def xdg_data_home() -> Path:
    """Return XDG_DATA_HOME or default ~/.local/share"""
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def xdg_state_home() -> Path:
    """Return XDG_STATE_HOME or default ~/.local/state"""
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))


def config_dir() -> Path:
    """Return the app config directory (XDG_CONFIG_HOME/meshcore-uconsole)"""
    return xdg_config_home() / APP_NAME


def data_dir() -> Path:
    """Return the app data directory (XDG_DATA_HOME/meshcore-uconsole)"""
    return xdg_data_home() / APP_NAME


def state_dir() -> Path:
    """Return the app state directory (XDG_STATE_HOME/meshcore-uconsole)"""
    return xdg_state_home() / APP_NAME


def db_path() -> Path:
    """Return the path to the SQLite database."""
    return state_dir() / "meshcore.db"


def identity_key_path() -> Path:
    """Return the path to the persisted identity seed (identity.key)"""
    return data_dir() / "identity.key"
