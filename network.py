#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# network.py  –  FULL FILE  (v1.63 • 12-digit amount support)

"""
Networking helpers for the Verifone-P400 desktop app
====================================================

* QUICK_CMDS   = {"PING", "STATUS", "START_TRAN"}
    – Stop reading on <EVENT>COMPLETED>
    – chunk_timeout = 0.4 s,  max_wait = 4 s

* USER_CMDS    = {"DISCOVERY", "CARD_DATA", "SIGNATURE_CAPTURE"}
    – Wait up to 65 s for user action (<EVENT>COMPLETED>)

* RECEIPT_CMDS = {"AUTHORIZE", "FINISH_TRAN", "REFUND"}
    – Wait for </TRANSACTION>  (full receipt), 15 s max

* Any other command
    – </TRANSACTION>, 10 s max
"""

from __future__ import annotations

import re
import socket
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Event
from urllib.parse import urlparse, parse_qs

from PyQt5.QtCore import QThread, pyqtSignal
from logger import log_traffic
from utils  import to_minor


# ── /pay restrictions ─────────────────────────────────────
VALID_TYPES = {"01", "02", "03", "06", "30", "53", "55"}


# ══════════════════════════════════════════════════════════
#                       SockThread
# ══════════════════════════════════════════════════════════
class SockThread(QThread):
    """Per-request TCP thread talking to the P400."""

    result = pyqtSignal(str, str)           # (sent_xml, received_text)

    QUICK_CMDS   = {"PING", "STATUS", "START_TRAN"}
    USER_CMDS    = {"DISCOVERY", "CARD_DATA", "SIGNATURE_CAPTURE"}
    RECEIPT_CMDS = {"AUTHORIZE", "FINISH_TRAN", "REFUND"}

    # ---------- internal helpers ----------
    def _is_done(self, buf: bytes, cmd: str) -> bool:
        if cmd in self.QUICK_CMDS or cmd in self.USER_CMDS:
            return b"<EVENT>COMPLETED" in buf
        return b"</TRANSACTION>" in buf

    def _timers(self, cmd: str):
        if cmd in self.QUICK_CMDS:   return 0.4, 4.0
        if cmd in self.USER_CMDS:    return 3.0, 65.0
        if cmd in self.RECEIPT_CMDS: return 1.2, 15.0
        return 1.0, 10.0

    # ---------- ctor ----------
    def __init__(self, ip: str, port: int, msg: str):
        super().__init__(None)
        self.ip, self.port, self.msg = ip, port, msg

    # ---------- thread body ----------
    def run(self):
        sent = self.msg
        recv_buf = b""

        m   = re.search(r"<COMMAND>([^<]+)</COMMAND>", sent)
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
                    recv_buf += chunk
                    if self._is_done(recv_buf, cmd):
                        break
        except Exception as exc:
            recv_buf = f"Error: {exc}".encode()

        recv_txt = recv_buf.decode("utf-8", "replace")
        log_traffic(sent, recv_txt)
        self.result.emit(sent, recv_txt)


# ══════════════════════════════════════════════════════════
#                     WebhookServer
# ══════════════════════════════════════════════════════════
class WebhookServer(Thread):
    """
    Tiny HTTP server exposing /pay and /receipt endpoints.

    New feature (v1.63):
        • /pay?amount=000000012345      – 12-digit minor units
        • /pay?amount=123.45            – regular decimal
    """

    def __init__(self, host: str, port: int, callback):
        super().__init__(daemon=True)
        self.host, self.port, self.callback = host, port, callback
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._signal = Event()
        self._last   = b"{}"

    # ---------- publish receipt ----------
    def publish(self, data: bytes):
        data = data or b"{}"
        self._q.put(data)
        self._last = data
        self._signal.set()

    # ---------- HTTP handler ----------
    def run(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            # helpers ----------------------------------------------------
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

            # CORS pre-flight -------------------------------------------
            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Max-Age", "86400")
                self.end_headers()

            # main GET ---------------------------------------------------
            def do_GET(self):
                try:
                    p = urlparse(self.path)

                    # /receipt.json
                    if p.path in ("/receipt", "/receipt.json"):
                        return self._json(200, outer._last)

                    # /pay ------------------------------------------------
                    if p.path != "/pay":
                        return self._plain(404, b"Not Found")

                    q = parse_qs(p.query)
                    raw_amt = q.get("amount", [""])[0]

                    # ----- amount parsing (12-digit *or* decimal) -------
                    if re.fullmatch(r"\d{12}", raw_amt):           # 000000012345
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

                    wait = q.get("wait", ["0"])[0] == "1"

                    # Hand off to GUI layer
                    outer.callback(amt, code)

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
                    return  # client disconnected; ignore

            # suppress default logging
            def log_message(self, *args):  # noqa: D401
                pass

        # Run HTTP server forever
        HTTPServer((self.host, self.port), H).serve_forever()
