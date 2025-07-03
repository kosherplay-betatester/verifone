#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# config.py  –  FULL FILE  (v1.3 • SHVA_TERM_ID support)

"""
Global configuration helpers
============================

• SETTINGS_PATH – JSON file persisted beside the executable
• MAC_PATH      – Plain-text file that stores the decrypted MAC key

Public helpers
--------------
    load_settings()          -> dict   (auto-creates file on first run)
    save_settings(data:dict) -> None   (overwrite settings.txt atomically)
    load_mac()               -> str    (read MAC key or "")
    save_mac(key:str)        -> None
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Final

# ───────────────────────────────────────────────────────────
# Paths
# ───────────────────────────────────────────────────────────
SETTINGS_PATH: Final[str] = "settings.txt"
MAC_PATH:      Final[str] = "mac.txt"

# ───────────────────────────────────────────────────────────
# Default structure – extend as the app evolves
# ───────────────────────────────────────────────────────────
DEFAULT_SETTINGS: Final[dict] = {
    # Device connection
    "ip":            "",       # P400 IP address
    "port":          "",       # P400 TCP port
    "training":      False,    # Training mode flag

    # Registration defaults
    "chain":         "00000",
    "store":         "0000",
    "lane":          "000",
    "alt":           "000000000",
    "pos_type":      "ATTENDED",

    # Quick-Sale preset
    "quick_amount":  0.00,

    # Key transport
    "ktk":           "",       # 16-char key-transport key

    # Webhook listener
    "webhook_host":  "localhost",
    "webhook_port":  8080,

    # NEW (v1.3) – SHVA terminal identifier for validation
    "shva_term_id":  ""
}

# ───────────────────────────────────────────────────────────
# Settings helpers
# ───────────────────────────────────────────────────────────
def _atomic_write(path: str, data: str) -> None:
    """
    Write *data* to *path* atomically to avoid corruption on power loss.
    """
    dname  = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dname, prefix=".tmp_cfg_", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(data)
        os.replace(tmp, path)              # atomic on POSIX + Windows
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def load_settings() -> dict:
    """
    Return the merged settings dict, making sure **all** keys from
    `DEFAULT_SETTINGS` are present.  If *settings.txt* is missing,
    it is created with defaults.
    """
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fp:
            data = json.load(fp)
    except FileNotFoundError:
        data = DEFAULT_SETTINGS.copy()
        _atomic_write(SETTINGS_PATH, json.dumps(data, indent=4, ensure_ascii=False))
        return data
    except Exception:                       # malformed JSON → reset
        data = {}

    # Merge any new keys introduced in newer versions
    changed = False
    for key, val in DEFAULT_SETTINGS.items():
        if key not in data:
            data[key] = val
            changed = True
    if changed:
        _atomic_write(SETTINGS_PATH, json.dumps(data, indent=4, ensure_ascii=False))
    return data


def save_settings(data: dict) -> None:
    """
    Overwrite *settings.txt* with *data* (caller guarantees completeness).
    """
    _atomic_write(SETTINGS_PATH, json.dumps(data, indent=4, ensure_ascii=False))


# ───────────────────────────────────────────────────────────
# MAC-key helpers
# ───────────────────────────────────────────────────────────
def load_mac() -> str:
    """Return the decrypted MAC key string, or an empty string if absent."""
    try:
        return open(MAC_PATH, encoding="utf-8").read().strip()
    except FileNotFoundError:
        return ""


def save_mac(mac_key: str) -> None:
    """Persist *mac_key* to disk (overwrites any previous key)."""
    with open(MAC_PATH, "w", encoding="utf-8") as fp:
        fp.write(mac_key)
