#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# network.py  –  FULL FILE  (v1.67 • per‑response logging)

"""
Networking helpers for the Verifone‑P400 desktop app
====================================================

v1.67 (2025‑07‑22)
------------------
• **SockThread.run** נרשם כעת *כל* <RESPONSE> שמתקבל בלייב — מיד
  כאשר סוגר </RESPONSE> מגיע — במקום להמתין עד סוף החיבור.  
  כך שה‑logs.txt / logs_readable.txt מראים אירועים במועד המדויק של
  הקבלה.

Timing rules (לא שונו)
----------------------
| Group          | Commands                               | chunk_timeout | max_wait |
|----------------|----------------------------------------|---------------|----------|
| QUICK_CMDS     | PING STATUS START_TRAN FINISH_TRAN     | 0.4 s         | 4 s      |
| USER_CMDS      | DISCOVERY CARD_DATA SIGNATURE_CAPTURE  | 3 s           | 65 s     |
| RECEIPT_CMDS   | AUTHORIZE REFUND                       | 1.2 s         | 15 s     |
| others         | fallback                               | 1 s           | 10 s     |
"""

from __future__ import annotations

import re
import socket
import queue
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Event
from urllib.parse import urlparse, parse_qs

from PyQt5.QtCore    import QThread, pyqtSignal
from logger          import log_traffic
from utils           import to_minor

# ───────────────────────────────────────────────────────────
# /pay restrictions
# ───────────────────────────────────────────────────────────
VALID_TYPES = {"01", "02", "03", "06", "30", "53", "55"}


# ══════════════════════════════════════════════════════════
#                       SockThread
# ══════════════════════════════════════════════════════════
class SockThread(QThread):
    """
    One‑shot TCP thread that sends a single XML command to the P400 and
    logs every RESPONSE immediately upon arrival.
    """

    # (sent_xml, received_text) will be delivered back to the GUI
    result = pyqtSignal(str, str)

    QUICK_CMDS   = {"PING", "STATUS", "START_TRAN", "FINISH_TRAN"}
    USER_CMDS    = {"DISCOVERY", "CARD_DATA", "SIGNATURE_CAPTURE"}
    RECEIPT_CMDS = {"AUTHORIZE", "REFUND"}

    # ───────────────────────────────────────────────────────
    #  helpers
    # ───────────────────────────────────────────────────────
    def _is_done(self, buf: bytes, cmd: str) -> bool:
        if cmd in self.QUICK_CMDS or cmd in self.USER_CMDS:
            return b"<EVENT>COMPLETED" in buf
        return b"</TRANSACTION>" in buf

    def _timers(self, cmd: str) -> tuple[float, float]:
        if cmd in self.QUICK_CMDS:   return 0.4, 4.0
        if cmd in self.USER_CMDS:    return 3.0, 65.0
        if cmd in self.RECEIPT_CMDS: return 1.2, 15.0
        return 1.0, 10.0

    # ───────────────────────────────────────────────────────
    #  main
    # ───────────────────────────────────────────────────────
    def __init__(self, ip: str, port: int, msg: str):
        super().__init__(None)
        self.ip, self.port, self.msg = ip, port, msg

    def run(self):
        sent        = self.msg
        recv_full   = b""      # כל מה שנקבל – לשידור חזרה ל‑GUI
        recv_proc   = b""      # נתונים שטרם נרשמו
        start_time  = datetime.now()

        m = re.search(r"<COMMAND>([^<]+)</COMMAND>", sent)
        cmd = m.group(1) if m else ""
        chunk_to, max_wait = self._timers(cmd)

        try:
            with socket.create_connection((self.ip, self.port), timeout=3) as s:
                s.sendall(sent.encode())
                s.settimeout(chunk_to)

                waited = 0.0
                while waited < max_wait:
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        waited += chunk_to
                        continue
                    if not chunk:
                        break

                    # צוברים ומעבדים
                    recv_full += chunk
                    recv_proc += chunk

                    # רישום בלייב – כל RESPONSE שנשלם
                    while b"</RESPONSE>" in recv_proc:
                        idx   = recv_proc.index(b"</RESPONSE>") + len(b"</RESPONSE>")
                        block = recv_proc[:idx]
                        recv_proc = recv_proc[idx:]

                        log_traffic(
                            sent,
                            block.decode("utf-8", "replace"),
                            start_time,
                            datetime.now()
                        )

                    if self._is_done(recv_full, cmd):
                        break
        except Exception as exc:
            recv_full = f"Error: {exc}".encode()

        # שארית שאינה RESPONSE (למשל TRANSACTION שלם)
        if recv_proc.strip():
            log_traffic(
                sent,
                recv_proc.decode("utf-8", "replace"),
                start_time,
                datetime.now()
            )
            recv_full = recv_full  # כבר מכיל הכול

        self.result.emit(sent, recv_full.decode("utf-8", "replace"))


# ══════════════════════════════════════════════════════════
#                     WebhookServer
# ══════════════════════════════════════════════════════════
class WebhookServer(Thread):
    """
    Tiny HTTP server exposing /pay, /receipt and /GET_TRAN_DETAILS endpoints.

    Features
    --------
    • /pay?amount=…[&type=…][&wait=1]     – trigger Quick‑Sale
    • /receipt.json                        – returns last JSON payload
    • /GET_TRAN_DETAILS[?wait=1]           – trigger GET_TRAN_DETAILS
    """

    def __init__(self, host: str, port: int,
                 pay_callback, details_callback):
        super().__init__(daemon=True)
        self.host                = host
        self.port                = port
        self._pay_callback       = pay_callback
        self._details_callback   = details_callback
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._signal             = Event()
        self._last               = b"{}"

    # ---------- publish JSON back to client ----------------
    def publish(self, data: bytes):
        data = data or b"{}"
        self._q.put(data)
        self._last = data
        self._signal.set()

    # ---------- HTTP server loop ---------------------------
    def run(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            # --- helpers ---
            def _plain(self, st, body=b""):
                self.send_response(st)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _json(self, st, body=b"{}"):
                self.send_response(st)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            # --- CORS preflight ---
            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Max-Age", "86400")
                self.end_headers()

            # --- GET handler ---
            def do_GET(self):
                try:
                    p = urlparse(self.path)
                    q = parse_qs(p.query)
                    wait = q.get("wait", ["0"])[0] == "1"

                    # /receipt.json
                    if p.path in ("/receipt", "/receipt.json"):
                        return self._json(200, outer._last)

                    # /GET_TRAN_DETAILS
                    if p.path.upper() == "/GET_TRAN_DETAILS":
                        outer._details_callback()
                        if not wait:
                            return self._plain(200, b"OK")
                        if not outer._signal.wait(timeout=65):
                            return self._plain(504, b"timeout")
                        try:
                            body = outer._q.get_nowait()
                        except queue.Empty:
                            return self._plain(504, b"timeout")
                        if outer._q.empty():
                            outer._signal.clear()
                        return self._json(200, body)

                    # /pay
                    if p.path != "/pay":
                        return self._plain(404, b"Not Found")

                    raw_amt = q.get("amount", [""])[0]
                    if re.fullmatch(r"\d{12}", raw_amt):
                        amt = int(raw_amt) / 100
                    else:
                        try:
                            amt = float(raw_amt)
                            assert amt > 0
                        except Exception:
                            return self._plain(400, b"amount must be positive")

                    code = q.get("type", ["01"])[0]
                    if code not in VALID_TYPES:
                        return self._plain(400, b"invalid type")

                    outer._pay_callback(amt, code)
                    if not wait:
                        return self._plain(200, b"OK")

                    if not outer._signal.wait(timeout=65):
                        return self._plain(504, b"timeout")
                    try:
                        body = outer._q.get_nowait()
                    except queue.Empty:
                        return self._plain(504, b"timeout")
                    if outer._q.empty():
                        outer._signal.clear()
                    return self._json(200, body)

                except (ConnectionAbortedError, BrokenPipeError):
                    return

            def log_message(self, *args):
                pass

        HTTPServer((self.host, self.port), H).serve_forever()
