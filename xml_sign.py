#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# xml_sign.py  –  FULL FILE  (v1.4 • ENTRY_MODE removed from DISCOVERY stub)
"""
XML templating & signing helpers for the Verifone-P400 desktop app
==================================================================

Key helpers
-----------
strip_mac(raw)      – remove any existing <MAC>…</MAC> block  
sign_xml(raw, key)  – insert <MAC>, compute SHA-256 HMAC (xml+key), substitute  
template_xml(...)   – build stand-alone <TRANSACTION> bodies for the XML
                      sandbox (START_TRAN, DISCOVERY, AUTHORIZE) **without
                      an <ENTRY_MODE> element**  
env_xml(...)        – wrap internal ADMIN / SESSION commands with timestamp &
                      optional body, then sign

Behavioural change
------------------
* The DISCOVERY template no longer embeds:
      <ENTRY_MODE>04</ENTRY_MODE>
  This keeps sandbox examples consistent with the runtime FSM, which
  stopped sending that element in v4.2 of *pay_process.py*.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from base64 import b64encode
from Crypto.Hash import SHA256

from utils import rand_session


# ───────────────────────────────────────────────────────────
# MAC helpers
# ───────────────────────────────────────────────────────────
def calc_mac(xml: str, key: str) -> str:
    """Return a Base64-encoded SHA-256 HMAC of (xml + key)."""
    digest = SHA256.new((xml + key).encode()).digest()
    return b64encode(digest).decode()


def strip_mac(raw: str) -> str:
    """Remove any existing <MAC>…</MAC> block (case-insensitive, multiline)."""
    return re.sub(r'<MAC>.*?</MAC>', '', raw, flags=re.I | re.S)


def sign_xml(raw: str, mac_key: str) -> str:
    """
    Insert placeholder <MAC></MAC> immediately after </TRANSACTION_TIME>,
    compute the correct MAC, and substitute it.

    Raises
    ------
    ValueError – if </TRANSACTION_TIME> is missing.
    """
    xml_no = strip_mac(raw)
    m_end  = re.search(r'</TRANSACTION_TIME\s*>', xml_no, flags=re.I)
    if not m_end:
        raise ValueError("Missing </TRANSACTION_TIME> tag")
    pos    = m_end.end()

    # Insert placeholder
    xml_ph = xml_no[:pos] + "\n  <MAC></MAC>" + xml_no[pos:]
    mac    = calc_mac(xml_ph, mac_key)

    # Replace placeholder with real MAC
    return xml_ph.replace("<MAC></MAC>", f"<MAC>{mac}</MAC>", 1)


# ───────────────────────────────────────────────────────────
# XML builders
# ───────────────────────────────────────────────────────────
def template_xml(fg: str, cmd: str, session: str,
                 training: bool, mac_key: str) -> str:
    """
    Build a **stand-alone** <TRANSACTION> XML for the Free-Call sandbox
    (e.g. SESSION START_TRAN, PAYMENT DISCOVERY, etc.) and sign it.

    NOTE:  The DISCOVERY stub **no longer contains <ENTRY_MODE>**.
    """
    if not session:
        session = rand_session()

    ts = datetime.now().strftime("%m.%d.%Y %H:%M:%S")
    xml = (
        "<TRANSACTION>"
        f"<FUNCTION_GROUP>{fg}</FUNCTION_GROUP>"
        f"<COMMAND>{cmd}</COMMAND>"
        f"<SESSION_ID>{session}</SESSION_ID>"
        f"<TRAINING_MODE>{int(training)}</TRAINING_MODE>"
        f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>"
    )

    # —— Command-specific bodies ——————————————
    if cmd == "START_TRAN":
        xml += (
            "<INVOICE>100000</INVOICE>"
            "<CASHIER_ID>1</CASHIER_ID>"
            "<SHIFT_ID>1</SHIFT_ID>"
            f"<BUSINESSDATE>{datetime.now():%Y%m%d}</BUSINESSDATE>"
        )

    elif cmd == "DISCOVERY":
        xml += (
            "<TIMEOUT>60</TIMEOUT>"
            "<TRANSACTION_DETAILS>"
            "<RESTRICT_TOKEN>0</RESTRICT_TOKEN><MANUAL>0</MANUAL>"
            "<CTLS>1</CTLS><ALLOW_CANCEL>1</ALLOW_CANCEL><UNATTENDED>0</UNATTENDED>"
            "<TRAN_TYPE>01</TRAN_TYPE><MTI>100</MTI>"
            "<TRANSACTION_AMOUNT>000000001000</TRANSACTION_AMOUNT>"
            "<ORIGINAL_CURRENCY>376</ORIGINAL_CURRENCY>"
            "</TRANSACTION_DETAILS>"
        )

    elif cmd == "AUTHORIZE":
        xml += (
            "<TRANSACTION_DETAILS>"
            "<OPERATION>04</OPERATION><TRAN_TYPE>01</TRAN_TYPE>"
            "<BATCH_ID>0</BATCH_ID><MTI>100</MTI>"
            "<TRANSACTION_AMOUNT>000000001000</TRANSACTION_AMOUNT>"
            "<ORIGINAL_CURRENCY>376</ORIGINAL_CURRENCY>"
            "<CREDIT_TERMS>1</CREDIT_TERMS><RESTRICT_TOKEN>0</RESTRICT_TOKEN>"
            "</TRANSACTION_DETAILS>"
        )

    xml += "</TRANSACTION>"
    return sign_xml(xml, mac_key)


def env_xml(fg: str, cmd: str, session: str,
            training: bool, mac_key: str, body: str = "") -> str:
    """
    Wrap an internal command (ADMIN / GENERAL / SESSION / PAYMENT / REPORT)
    with timestamp and optional *body*, then sign.

    The caller supplies the command-specific body (already free of
    <ENTRY_MODE>) when necessary.
    """
    ts  = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
    hdr = (
        "<TRANSACTION>"
        f"<FUNCTION_GROUP>{fg}</FUNCTION_GROUP>"
        f"<COMMAND>{cmd}</COMMAND>"
        f"<SESSION_ID>{session}</SESSION_ID>"
        f"<TRAINING_MODE>{int(training)}</TRAINING_MODE>"
        f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>"
    )
    return sign_xml(hdr + body + "</TRANSACTION>", mac_key)
