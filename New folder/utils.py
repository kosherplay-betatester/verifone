#!/usr/bin/env python3
"""
utils.py – shared helpers
=========================

• rand_session – generate random 16-digit SESSION_ID
• to_minor     – convert a major-units amount to a *12-digit*, zero-padded
                 string of minor units (₪ → אגורות)
• des3_decrypt – decrypt DES3-encrypted MAC_KEY
"""

from __future__ import annotations

import random
from base64 import b64decode
from Crypto.Cipher import DES3


# ───────────────────────────────────────────────────────────
# Public helpers
# ───────────────────────────────────────────────────────────

def rand_session() -> str:
    """Return a random 16-digit numeric string (SESSION_ID)."""
    return "".join(random.choice("0123456789") for _ in range(16))


def to_minor(value) -> str:
    """
    Convert *value* to a **12-digit** zero-padded string of minor units.

    *Accepted input*  →  *Output*           (examples)
    ------------------------------------------------------------------
    123.45            →  "000000012345"
    1000  (int agorot)→  "000000010000"
    "000000012345"    →  "000000012345"  (already padded)

    The rest of the codebase always calls `to_minor()` before sending the
    amount to the device, so the P400 now receives exactly 12 digits.
    """
    # Already a correctly-formatted string?
    if isinstance(value, str) and len(value) == 12 and value.isdigit():
        return value

    # Float in shekels  →  int agorot
    if isinstance(value, float):
        value = int(round(value * 100))

    return f"{int(value):012d}"


def des3_decrypt(ktk: str, mac_b64: str) -> str:
    """
    Decrypt the DES3-encrypted MAC key returned from the device.

    Parameters
    ----------
    ktk      : 16-character key-transport key (ASCII)
    mac_b64  : base-64 encrypted MAC_KEY from device

    Returns
    -------
    str – plaintext MAC key
    """
    if len(ktk) != 16:
        raise ValueError("KTK must be 16 ASCII chars")

    # Expand 16-byte KTK to 24 bytes with parity bits
    key24 = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    cipher = DES3.new(key24, DES3.MODE_ECB)

    data = cipher.decrypt(b64decode(mac_b64))
    return data.rstrip(b"\0").decode()
