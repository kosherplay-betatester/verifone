#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# network.py  –  FULL FILE  (v1.68 • live‑logging + guaranteed receipt publish)

"""
Networking helpers for the Verifone‑P400 desktop app
====================================================

v1.68 (2025‑07‑23)
------------------
1. **Per‑response logging** (v1.67):  כל  <RESPONSE> נרשם מיד עם קבלתו.  
2. **Webhook wait‑loop**:  ‎/pay?wait=1 ו‑/GET_TRAN_DETAILS?wait=1  
   ממתינים עד **3 דקות** (במקום 65 שניות) וממשיכים לבדוק
   עד שמתקבל receipt.json; כך לא מתקבל עוד “timeout” כאשר הקובץ
   נשמר אך הפאבליש הגיע מעט מאוחר יותר.

Timing rules (ל‑TCP) לא שונו.
"""

from __future__ import annotations

import re, socket, queue, time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Event
from urllib.parse import urlparse, parse_qs

from PyQt5.QtCore    import QThread, pyqtSignal
from logger          import log_traffic
from utils           import to_minor

# ───────────────────────────────────────────────────────────
#  /pay restrictions
# ───────────────────────────────────────────────────────────
VALID_TYPES = {"01", "02", "03", "06", "30", "53", "55"}


# ══════════════════════════════════════════════════════════
#                       SockThread
# ══════════════════════════════════════════════════════════
class SockThread(QThread):
    """
    One‑shot TCP thread that sends a single XML command, logs each RESPONSE
    the moment it closes, and emits full payload back to the GUI.
    """

    result = pyqtSignal(str, str)

    QUICK_CMDS   = {"PING", "STATUS", "START_TRAN", "FINISH_TRAN"}
    USER_CMDS    = {"DISCOVERY", "CARD_DATA", "SIGNATURE_CAPTURE"}
    RECEIPT_CMDS = {"AUTHORIZE", "REFUND"}

    # ---------- helpers ----------
    def _is_done(self, buf: bytes, cmd: str) -> bool:
        if cmd in self.QUICK_CMDS or cmd in self.USER_CMDS:
            return b"<EVENT>COMPLETED" in buf
        return b"</TRANSACTION>" in buf

    def _timers(self, cmd: str) -> tuple[float, float]:
        if cmd in self.QUICK_CMDS:   return 0.4, 4.0
        if cmd in self.USER_CMDS:    return 3.0, 65.0
        if cmd in self.RECEIPT_CMDS: return 1.2, 15.0
        return 1.0, 10.0

    # ---------- run ----------
    def __init__(self, ip: str, port: int, msg: str):
        super().__init__(None)
        self.ip, self.port, self.msg = ip, port, msg

    def run(self):
        sent        = self.msg
        recv_full   = b""
        recv_chunk  = b""
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

                    recv_full += chunk
                    recv_chunk += chunk

                    # live log each complete </RESPONSE>
                    while b"</RESPONSE>" in recv_chunk:
                        end = recv_chunk.index(b"</RESPONSE>") + len(b"</RESPONSE>")
                        block, recv_chunk = recv_chunk[:end], recv_chunk[end:]
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

        # leftover (e.g. full </TRANSACTION>)
        if recv_chunk.strip():
            log_traffic(
                sent,
                recv_chunk.decode("utf-8", "replace"),
                start_time,
                datetime.now()
            )

        self.result.emit(sent, recv_full.decode("utf-8", "replace"))


# ══════════════════════════════════════════════════════════
#                     WebhookServer
# ══════════════════════════════════════════════════════════
class WebhookServer(Thread):
    """
    Tiny HTTP server exposing /pay, /receipt and /GET_TRAN_DETAILS endpoints.
    """

    def __init__(self, host: str, port: int,
                 pay_callback, details_callback):
        super().__init__(daemon=True)
        self.host              = host
        self.port              = port
        self._pay_callback     = pay_callback
        self._details_callback = details_callback
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._signal           = Event()
        self._last             = b"{}"

    # ---------- publish ----------
    def publish(self, data: bytes):
        data = data or b"{}"
        self._q.put(data)
        self._last = data
        self._signal.set()

    # ---------- HTTP server ----------
    def run(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            # ----- helpers -----
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

            # ----- CORS -----
            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Max-Age", "86400")
                self.end_headers()

            # ----- utility: wait up to 180 s for publish -----
            def _wait_for_receipt(self, max_sec: int = 180):
                expiry = time.time() + max_sec
                while time.time() < expiry:
                    left = expiry - time.time()
                    if outer._signal.wait(timeout=min(2, left)):  # poll every ≤2 s
                        try:
                            body = outer._q.get_nowait()
                        except queue.Empty:
                            outer._signal.clear()
                            continue
                        if outer._q.empty():
                            outer._signal.clear()
                        return body
                return None  # timeout

            # ----- GET -----
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
                        body = self._wait_for_receipt()
                        if body is None:
                            return self._plain(504, b"timeout")
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

                    body = self._wait_for_receipt()
                    if body is None:
                        return self._plain(504, b"timeout")
                    return self._json(200, body)

                except (ConnectionAbortedError, BrokenPipeError):
                    return

            def log_message(self, *args):
                pass

        HTTPServer((self.host, self.port), H).serve_forever()
