#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
logger.py  –  Dual-format network logger with 14-day rotation
=============================================================

v2.2.1  (bug-fix)
-----------------
• Fixed stray quote in `_rotate_if_needed()` that caused a SyntaxError.
• Retains v2.2 behaviour: every <event> carries `time=`, `start=`, `end=`,
  and each block includes <sent_time>/<recv_time>.
"""

from __future__ import annotations

import os, re, shutil
from datetime import datetime, timedelta
from typing import Final
import xml.etree.ElementTree as ET
from xml.dom import minidom

# ───────────────────────────────────────────────────────────
#  Paths & rotation policy
# ───────────────────────────────────────────────────────────
_XML_FILE: Final[str] = "logs.txt"
_XML_BAK:  Final[str] = "logs.bak"
_HUM_FILE: Final[str] = "logs_readable.txt"
_HUM_BAK:  Final[str] = "logs_readable.bak"
_ROTATE_AFTER: Final[timedelta] = timedelta(days=14)

# ───────────────────────────────────────────────────────────
#  File-management helpers
# ───────────────────────────────────────────────────────────
def _ensure_xml_root() -> None:
    if not os.path.exists(_XML_FILE) or os.path.getsize(_XML_FILE) == 0:
        with open(_XML_FILE, "w", encoding="utf-8") as fp:
            fp.write("<logs>\n")

def _first_event_time() -> datetime | None:
    """Return timestamp of very first <event> (prefers time=, else start=)."""
    try:
        with open(_XML_FILE, encoding="utf-8") as fp:
            for ln in fp:
                if "<event" in ln and ' time="' in ln:
                    iso = re.search(r'time="([^"]+)"', ln).group(1)
                    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S")
                if "<event" in ln and ' start="' in ln:
                    iso = re.search(r'start="([^"]+)"', ln).group(1)
                    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S")
    except Exception:
        pass
    return None

def _rotate_if_needed() -> None:
    first = _first_event_time()
    if not first or datetime.now() - first < _ROTATE_AFTER:
        return
    shutil.copyfile(_XML_FILE, _XML_BAK)
    if os.path.exists(_HUM_FILE) and os.path.getsize(_HUM_FILE):
        shutil.copyfile(_HUM_FILE, _HUM_BAK)
    with open(_XML_FILE, "w", encoding="utf-8") as fp:
        fp.write("<logs>\n")
    open(_HUM_FILE, "w", encoding="utf-8").close()

# ───────────────────────────────────────────────────────────
#  Formatting helpers
# ───────────────────────────────────────────────────────────
def _pretty_xml(raw: str) -> str:
    """Indent XML; handles concatenated <RESPONSE> blocks gracefully."""
    try:
        mult = raw.lstrip().startswith("<RESPONSE") and raw.count("<RESPONSE") > 1
        wrapped = f"<root>{raw}</root>" if mult else raw
        pretty = minidom.parseString(
            ET.tostring(ET.fromstring(wrapped), encoding="utf-8")
        ).toprettyxml(indent="  ")
        if mult:
            pretty = "\n".join(
                ln for ln in pretty.splitlines()
                if ln.strip() and not ln.strip().startswith("<root")
            )
        return pretty.strip()
    except Exception:
        return re.sub(r">(?!\s)", ">\n", raw)

def _cdata(txt: str) -> str:
    return "<![CDATA[" + txt.replace("]]>", "]]]]><![CDATA[>") + "]]>"

def _summarise(sent: str, recv: str,
               t_send: datetime, t_recv: datetime) -> str:
    ts_s = t_send.strftime("%Y-%m-%d %H:%M:%S")
    ts_r = t_recv.strftime("%Y-%m-%d %H:%M:%S")
    cmd  = re.search(r"<COMMAND>([^<]+)</COMMAND>", sent)
    cmd_str = cmd.group(1) if cmd else "?"
    rcd  = re.search(r"<RESULT_CODE>([^<]+)<", recv)
    rcd_str = f"  |  RESULT_CODE: {rcd.group(1)}" if rcd else ""
    return "\n".join([
        "-" * 72,
        f"{ts_s} START  | COMMAND: {cmd_str}{rcd_str}",
        f"SENT  @ {ts_s}",
        *("  " + ln for ln in _pretty_xml(sent).splitlines()),
        f"RECV  @ {ts_r}",
        *("  " + ln for ln in _pretty_xml(recv).splitlines()),
        f"{ts_r} END",
        "-" * 72,
        ""
    ])

# ───────────────────────────────────────────────────────────
#  Public API
# ───────────────────────────────────────────────────────────
def log_traffic(sent: str, recv: str,
                start_dt: datetime, end_dt: datetime) -> None:
    """
    Append one request/response pair to both logs with full timestamps.
    """
    _ensure_xml_root()
    _rotate_if_needed()

    iso_start = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
    iso_end   = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

    # —— XML log ——————————————————————————————
    with open(_XML_FILE, "a", encoding="utf-8") as fp:
        fp.write(
            f'  <event time="{iso_start}" start="{iso_start}" end="{iso_end}">\n'
            f'    <sent_time>{iso_start}</sent_time>\n'
            f'    <sent>{_cdata(_pretty_xml(sent.strip()))}</sent>\n'
            f'    <recv_time>{iso_end}</recv_time>\n'
            f'    <recv>{_cdata(_pretty_xml(recv.strip()))}</recv>\n'
            f'  </event>\n'
        )

    # —— Plain-text log ————————————————————————
    with open(_HUM_FILE, "a", encoding="utf-8") as fp:
        fp.write(_summarise(sent, recv, start_dt, end_dt))
