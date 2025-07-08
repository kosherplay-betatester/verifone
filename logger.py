#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
logger.py  –  Dual-format network logger with automatic 14-day rotation
======================================================================

What this file does
-------------------
1. **Captures every request/response pair** exchanged with the Verifone
   P400 (the `SockThread` calls `log_traffic(sent, recv)`).

2. **Writes two parallel log files**:
   • `logs.txt`            – a machine-friendly XML tree  
   • `logs_readable.txt`   – a plain-text narrative for humans

3. **Rotates both files every 14 days**:
   • When the *oldest* `<event>` in `logs.txt` is at least 14 days old,
     both live logs are copied to:
        • `logs.bak`            – previous XML tree
        • `logs_readable.bak`   – previous narrative
   • Then the live logs are restarted from scratch.

4. **Pretty-prints** the device XML inside each log entry so that no line
   becomes absurdly long when you open the files in a text editor.

-----------------------------------------------------------------------
If you need to **test rotation quickly**, temporarily change
`_ROTATE_AFTER = timedelta(days=14)` to something like
`timedelta(seconds=30)`, restart the app, perform two requests 30 seconds
apart, and observe the `.bak` files appear.
-----------------------------------------------------------------------
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timedelta
from typing import Final

# Pretty-printing helpers
import xml.etree.ElementTree as ET
from xml.dom import minidom

# ───────────────────────────────────────────────────────────
#  File paths & constants
# ───────────────────────────────────────────────────────────
_XML_FILE:   Final[str] = "logs.txt"             # live XML log
_XML_BAK:    Final[str] = "logs.bak"             # previous XML log
_HUM_FILE:   Final[str] = "logs_readable.txt"    # live plain-text log
_HUM_BAK:    Final[str] = "logs_readable.bak"    # previous plain-text log

# Rotate the logs once the oldest event is this old:
_ROTATE_AFTER: Final[timedelta] = timedelta(days=14)

# ---------------------------------------------------------------------
#  Helper functions – XML file management
# ---------------------------------------------------------------------
def _ensure_xml_root() -> None:
    """
    Make sure `logs.txt` exists **and** begins with a `<logs>` root tag.
    Called on every write (cheap) so you can safely delete the file while
    the program is running; it will be recreated automatically.
    """
    if not os.path.exists(_XML_FILE) or os.path.getsize(_XML_FILE) == 0:
        with open(_XML_FILE, "w", encoding="utf-8") as f:
            f.write("<logs>\n")   # open root (we never close it – streaming log)

def _first_event_time() -> datetime | None:
    """
    Parse only enough of `logs.txt` to find the timestamp of the **very
    first** `<event time="…">`.  Returning `None` simply disables rotation
    if the tag is missing or malformed.
    """
    try:
        with open(_XML_FILE, encoding="utf-8") as f:
            for line in f:
                if "<event" in line and ' time="' in line:
                    # Grab the ISO-formatted timestamp
                    m = re.search(r'time="([^"]+)"', line)
                    if m:
                        return datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    except Exception:  # Catch I/O + parse errors – safest to skip rotation
        pass
    return None

def _rotate_if_needed() -> None:
    """
    • If the log’s first event is at least `_ROTATE_AFTER` ago:
        1. Copy live files → `.bak` (overwriting any previous backups)
        2. Recreate fresh, empty live logs (XML header & truncate readable log)
    • If the log is younger → do nothing.
    """
    if not os.path.exists(_XML_FILE):
        return    # nothing to rotate (fresh run)
    first = _first_event_time()
    if first is None:
        return    # malformed file – skip rotation
    if datetime.now() - first < _ROTATE_AFTER:
        return    # not old enough yet

    # 1) Copy live logs → backups
    shutil.copyfile(_XML_FILE, _XML_BAK)
    if os.path.exists(_HUM_FILE) and os.path.getsize(_HUM_FILE) > 0:
        shutil.copyfile(_HUM_FILE, _HUM_BAK)

    # 2) Restart live logs
    with open(_XML_FILE, "w", encoding="utf-8") as f:
        f.write("<logs>\n")                   # fresh XML root
    open(_HUM_FILE, "w", encoding="utf-8").close()  # truncate readable log

# ---------------------------------------------------------------------
#  Helper functions – XML presentation
# ---------------------------------------------------------------------
def _pretty_xml(raw: str) -> str:
    """
    Return a nicely indented version of the device XML:

    * **Normal case** – the string is a single well-formed element
      → use `xml.dom.minidom` to pretty-print with two-space indents.

    * **Special case** – the device concatenates multiple `<RESPONSE>`
      elements without a wrapper.  We detect that and temporarily wrap
      the string in `<root>…</root>` so that the XML parser accepts it,
      then discard the wrapper lines from the result.

    * **Failure** – if parsing blows up for any reason, fall back to
      simply inserting newlines between `><` so the text is still readable.
    """
    try:
        multi_root = raw.strip().startswith("<RESPONSE") and raw.count("<RESPONSE") > 1
        to_parse = f"<root>{raw}</root>" if multi_root else raw
        elem = ET.fromstring(to_parse)
        pretty_bytes = ET.tostring(elem, encoding="utf-8")
        pretty = minidom.parseString(pretty_bytes).toprettyxml(indent="  ")
        if multi_root:
            # Remove the synthetic <root> lines we added
            pretty = "\n".join(
                ln for ln in pretty.splitlines()
                if ln.strip() and not ln.strip().startswith("<root")
            )
        return pretty.strip()
    except Exception:
        # Very unlikely but safest: break long line at every ><
        return re.sub(r">(?!\s)", ">\n", raw)

def _cdata(block: str) -> str:
    """
    Safely wrap *block* in a CDATA section.  If the block contains the
    sequence `]]>` (rare), break it up to avoid premature termination.
    """
    return "<![CDATA[" + block.replace("]]>", "]]]]><![CDATA[>") + "]]>"

# ---------------------------------------------------------------------
#  Helper – produce the plain-text narrative entry
# ---------------------------------------------------------------------
def _summarise(sent: str, recv: str) -> str:
    """
    Build one human-readable block:

    ───────────────────────────────────────────────────────────────────
    YYYY-MM-DD HH:MM:SS  COMMAND: PING  |  RESULT_CODE: 0
    SENT:
      <COMMAND>PING</COMMAND>
      …
    RECV:
      <EVENT>COMPLETED</EVENT>
      …
    ───────────────────────────────────────────────────────────────────
    (blank line)
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Extract command & result code (if present) for quick scanning
    cmd_match = re.search(r"<COMMAND>([^<]+)</COMMAND>", sent)
    cmd = cmd_match.group(1) if cmd_match else "?"
    rcd_match = re.search(r"<RESULT_CODE>([^<]+)<", recv)
    rcd = rcd_match.group(1) if rcd_match else None

    header = f"{timestamp}  COMMAND: {cmd}"
    if rcd:
        header += f"  |  RESULT_CODE: {rcd}"

    lines: list[str] = [
        "-" * 80,
        header,
        "SENT:",
        *("  " + ln for ln in _pretty_xml(sent).splitlines()),
        "RECV:",
        *("  " + ln for ln in _pretty_xml(recv).splitlines()),
        "-" * 80,
        ""  # blank line separator
    ]
    return "\n".join(lines)

# ---------------------------------------------------------------------
#  Public API – This is what SockThread calls
# ---------------------------------------------------------------------
def log_traffic(sent: str, recv: str) -> None:
    """
    Main entry point: append one request/response pair
    to **both** live logs, performing rotation first if necessary.

    Parameters
    ----------
    sent : str
        The exact XML string that the application sent to the device.
    recv : str
        The exact XML string that was received as a response.
    """
    # 0) Make sure logs exist and rotate if they’re too old
    _ensure_xml_root()
    _rotate_if_needed()

    # ------------------------------------------------------------------
    # 1) Append to XML tree  (logs.txt)
    # ------------------------------------------------------------------
    ts_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with open(_XML_FILE, "a", encoding="utf-8") as fxml:
        fxml.write(f'  <event time="{ts_iso}">\n')
        fxml.write(f"    <sent>{_cdata(_pretty_xml(sent.strip()))}</sent>\n")
        fxml.write(f"    <recv>{_cdata(_pretty_xml(recv.strip()))}</recv>\n")
        fxml.write("  </event>\n")

    # ------------------------------------------------------------------
    # 2) Append to plain-text narrative  (logs_readable.txt)
    # ------------------------------------------------------------------
    with open(_HUM_FILE, "a", encoding="utf-8") as fhum:
        fhum.write(_summarise(sent, recv))
