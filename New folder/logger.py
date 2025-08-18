#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
logger.py  –  Dual-format network logger with 14-day rotation
=============================================================

v2.4 (per-response logging polish)
----------------------------------
• תומך כעת ב-log_traffic(sent=None) / sent=""
  - במצב “response-only” (מגיע מ-network.py אחרי פיצול
    <RESPONSE>-ים) לא מושמע בלוק SENT בפלט הקריא,
    ולכן ה-request לא משוכפל שוב ושוב.
  - ב-XML נשמר <sent> ריק כדי לשמור על מבנה קבוע.

v2.3 (hide ENTRY_MODE in readable log)
--------------------------------------
• Plain-text summary strips every <ENTRY_MODE>…</ENTRY_MODE> pair.
"""

from __future__ import annotations

import os
import re
import shutil
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

# Tags to suppress in plain-text summaries
_SUPPRESS_TAGS = ["ENTRY_MODE"]

# ───────────────────────────────────────────────────────────
#  File-management helpers
# ───────────────────────────────────────────────────────────
def _ensure_xml_root() -> None:
    """Create an opening <logs> tag if XML log is missing/empty."""
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
    """Rotate both logs if the oldest event is older than _ROTATE_AFTER."""
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
    raw = raw.strip()
    if not raw:
        return ""
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
        # fallback: insert newlines after '>'
        return re.sub(r">(?!\s)", ">\n", raw)


def _cdata(txt: str) -> str:
    """Wrap *txt* in a safe CDATA section."""
    return "<![CDATA[" + txt.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _hide_tags(xml_txt: str) -> str:
    """Remove every occurrence of each tag in _SUPPRESS_TAGS (inclusive)."""
    for tag in _SUPPRESS_TAGS:
        xml_txt = re.sub(
            rf"<{tag}>[^<]*</{tag}>", "", xml_txt, flags=re.I | re.S
        )
    return xml_txt


def _summarise(sent: str | None,
               recv: str,
               t_send: datetime,
               t_recv: datetime) -> str:
    """Build plain-text block shown in logs_readable.txt."""
    recv_clean = _hide_tags(recv)

    ts_s = t_send.strftime("%Y-%m-%d %H:%M:%S")
    ts_r = t_recv.strftime("%Y-%m-%d %H:%M:%S")

    # Try to determine COMMAND from sent; fall back to recv if missing.
    if sent:
        m_cmd = re.search(r"<COMMAND>([^<]+)</COMMAND>", sent)
        cmd_str = m_cmd.group(1) if m_cmd else "?"
    else:
        m_cmd = re.search(r"<COMMAND>([^<]+)</COMMAND>", recv_clean)
        cmd_str = m_cmd.group(1) if m_cmd else "?"

    m_rcd = re.search(r"<RESULT_CODE>([^<]+)<", recv_clean)
    rcd_str = f"  |  RESULT_CODE: {m_rcd.group(1)}" if m_rcd else ""

    lines: list[str] = [
        "-" * 72,
        f"{ts_s} START  | COMMAND: {cmd_str}{rcd_str}",
    ]

    if sent:
        lines += [
            f"SENT  @ {ts_s}",
            *("  " + ln for ln in _pretty_xml(sent).splitlines() if ln.strip()),
        ]

    lines += [
        f"RECV  @ {ts_r}",
        *("  " + ln for ln in _pretty_xml(recv_clean).splitlines() if ln.strip()),
        f"{ts_r} END",
        "-" * 72,
        "",
    ]
    return "\n".join(lines)

# ───────────────────────────────────────────────────────────
#  Public API
# ───────────────────────────────────────────────────────────
def log_traffic(sent: str | None,
                recv: str,
                start_dt: datetime,
                end_dt: datetime) -> None:
    """
    Append one request/response pair to both logs with full timestamps.

    Parameters
    ----------
    sent   : str | None
        The original request XML.  If *None* or empty, the human log
        will omit the SENT block (used for per-response logging).
    recv   : str
        The response (or partial response) XML text.
    start_dt / end_dt : datetime
        Wall-clock timestamps for this event.
    """
    _ensure_xml_root()
    _rotate_if_needed()

    iso_start = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
    iso_end   = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

    sent_txt = (sent or "").strip()
    recv_txt = recv.strip()

    # —— XML log ——————————————————————————————
    with open(_XML_FILE, "a", encoding="utf-8") as fp:
        fp.write(
            f'  <event time="{iso_start}" start="{iso_start}" end="{iso_end}">\n'
            f'    <sent_time>{iso_start}</sent_time>\n'
            f'    <sent>{_cdata(_pretty_xml(sent_txt))}</sent>\n'
            f'    <recv_time>{iso_end}</recv_time>\n'
            f'    <recv>{_cdata(_pretty_xml(recv_txt))}</recv>\n'
            f'  </event>\n'
        )

    # —— Plain-text log ————————————————————————
    with open(_HUM_FILE, "a", encoding="utf-8") as fp:
        fp.write(
            _summarise(sent_txt if sent else None,
                       recv_txt,
                       start_dt,
                       end_dt)
        )
