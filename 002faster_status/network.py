#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# network.py  –  FULL FILE  (v1.61 • fast Ping/Status + full receipts)

"""
שינויי v1.61
============
1. **קטגוריות טיימ-אאוט מדויקות**  
   • QUICK_CMDS   = {"PING", "STATUS"} – מחכים  *רק* ל-<EVENT>COMPLETED>  
     (chunk_timeout = 0.4 s, max_wait = 4 s).  
   • USER_CMDS    = {"DISCOVERY", "CARD_DATA", "SIGNATURE_CAPTURE"} – מחכים  
     ל-<EVENT>COMPLETED> (3 s × עד 65 s).  
   • RECEIPT_CMDS = {"AUTHORIZE", "FINISH_TRAN", "REFUND"} – מחכים  
     ל-</TRANSACTION> (1.2 s × עד 15 s).  
   • כל פקודה אחרת – ‎</TRANSACTION>,  chunk = 1 s, max = 10 s.

2. Ping / Status חוזרים שוב תוך ~150–300 ms, בעוד AUTHORIZE מקבל
   את כל קובץ הקבלה לפני שסוגר את הסוקט.
"""

import re, socket, queue
from http.server  import HTTPServer, BaseHTTPRequestHandler
from threading    import Thread, Event
from urllib.parse import urlparse, parse_qs

from PyQt5.QtCore import QThread, pyqtSignal
from logger import log_traffic


# ----- /pay -----
VALID_TYPES = {'01', '02', '03', '06', '30', '53', '55'}


# ════════════════════════════════════════════════════════
#                      SockThread
# ════════════════════════════════════════════════════════
class SockThread(QThread):
    """אחראי על שיחה בודדת מול ה-P400 (TCP + XML)."""

    result = pyqtSignal(str, str)           # (sent_xml, received_text)

    QUICK_CMDS    = {"PING", "STATUS"}
    USER_CMDS     = {"DISCOVERY", "CARD_DATA", "SIGNATURE_CAPTURE"}
    RECEIPT_CMDS  = {"AUTHORIZE", "FINISH_TRAN", "REFUND"}

    # ---------- helpers ----------
    def _is_done(self, buf: bytes, cmd: str) -> bool:
        if cmd in self.QUICK_CMDS | self.USER_CMDS:
            return b"<EVENT>COMPLETED" in buf
        return b"</TRANSACTION>" in buf

    def _timers(self, cmd: str):
        """Return (chunk_timeout, max_wait) per command category."""
        if cmd in self.QUICK_CMDS:    return 0.4, 4.0
        if cmd in self.USER_CMDS:     return 3.0, 65.0
        if cmd in self.RECEIPT_CMDS:  return 1.2, 15.0
        return 1.0, 10.0              # default

    # ---------- ctor ----------
    def __init__(self, ip: str, port: int, msg: str):
        super().__init__(None)
        self.ip, self.port, self.msg = ip, port, msg

    # ---------- thread ----------
    def run(self):
        sent = self.msg
        recv_buf = b""

        cmd_m = re.search(r"<COMMAND>([^<]+)</COMMAND>", sent)
        cmd   = cmd_m.group(1) if cmd_m else ""
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


# ════════════════════════════════════════════════════════
#                     WebhookServer
# ════════════════════════════════════════════════════════
class WebhookServer(Thread):
    """/pay   /receipt  HTTP mini-server (runs as daemon thread)."""

    def __init__(self, host: str, port: int, callback):
        super().__init__(daemon=True)
        self.host, self.port, self.callback = host, port, callback
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._signal = Event()
        self._last   = b"{}"

    def publish(self, data: bytes):
        data = data or b"{}"
        self._q.put(data); self._last = data; self._signal.set()

    # ----- handler -----
    def run(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            # ---- mini helpers ----
            def _plain(self, st, body):
                self.send_response(st)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers(); self.wfile.write(body)

            def _json(self, st, body):
                self.send_response(st)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers(); self.wfile.write(body)

            # ---- CORS ----
            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Max-Age", "86400")
                self.end_headers()

            # ---- GET ----
            def do_GET(self):
                try:
                    p = urlparse(self.path)

                    # /receipt
                    if p.path in ("/receipt", "/receipt.json"):
                        return self._json(200, outer._last)

                    # /pay
                    if p.path != "/pay":
                        return self._plain(404, b"Not Found")
                    q = parse_qs(p.query)

                    # amount
                    try:    amt = float(q["amount"][0]); assert amt > 0
                    except Exception:
                        return self._plain(400, b"amount must be positive")

                    # type
                    code = q.get("type", ["01"])[0]
                    if code not in VALID_TYPES:
                        return self._plain(400, b"invalid type")

                    wait = q.get("wait", ["0"])[0] == "1"
                    outer.callback(amt, code)          # trigger Quick-Sale

                    if not wait:
                        return self._plain(200, b"OK")

                    if not outer._signal.wait(timeout=65):
                        return self._plain(504, b"timeout")

                    try:    body = outer._q.get_nowait()
                    except queue.Empty:
                        return self._plain(504, b"timeout")

                    if outer._q.empty(): outer._signal.clear()
                    return self._json(200, body)

                except (ConnectionAbortedError, BrokenPipeError):
                    return  # client closed connection early

            def log_message(self, *args): pass  # silence default log

        HTTPServer((self.host, self.port), H).serve_forever()
