# network.py
# -----------------------------------------------------------
# Networking helpers for Verifone-P400 application
# • SockThread  — opens a raw TCP socket, sends XML, returns reply
# • WebhookServer — HTTP listener that triggers Quick-Sale
#                   ונותן קבלה ייחודית לכל קריאה עם ‎wait=1‎
# -----------------------------------------------------------

import socket
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread, Event
from urllib.parse import urlparse, parse_qs

from PyQt5.QtCore import QThread, pyqtSignal

from logger import log_traffic   # ← our new module

# Allowed transaction-type codes
VALID_TYPES = {'01', '02', '03', '06', '30', '53', '55'}


# ────────────────────────────────────────────────────────────
#  SockThread  – one-off TCP exchange with the payment device
# ────────────────────────────────────────────────────────────
class SockThread(QThread):
    result = pyqtSignal(str, str)           # (sent, received)

    def __init__(self, ip: str, port: int, msg: str):
        super().__init__(None)
        self.ip = ip
        self.port = port
        self.msg = msg

    def run(self):
        sent = self.msg
        recv = ""
        try:
            with socket.create_connection((self.ip, self.port), timeout=7) as s:
                s.sendall(sent.encode())
                chunks = []
                while True:
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    chunks.append(chunk)
                recv = b"".join(chunks).decode("utf-8", "replace")
        except Exception as exc:
            recv = f"Error: {exc}"

        # Log every request/response
        log_traffic(sent, recv)

        # Emit for the rest of the app
        self.result.emit(sent, recv)


# ────────────────────────────────────────────────────────────
#  WebhookServer  – background HTTP server
# ────────────────────────────────────────────────────────────
class WebhookServer(Thread):
    def __init__(self, host: str, port: int, callback):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.callback = callback
        self._rec_queue: "queue.Queue[bytes]" = queue.Queue()
        self._signal = Event()
        self._last = b"{}"

    def publish(self, receipt_bytes: bytes):
        data = receipt_bytes or b"{}"
        self._rec_queue.put(data)
        self._last = data
        self._signal.set()

    def run(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _plain(self, status: int, body: bytes):
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, body: bytes):
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
                self.send_header("Access-Control-Max-Age", "86400")
                self.end_headers()

            def do_GET(self):
                try:
                    p = urlparse(self.path)

                    if p.path in ("/receipt", "/receipt.json"):
                        return self._json(200, outer._last)

                    if p.path != "/pay":
                        return self._plain(404, b"Not Found")
                    q = parse_qs(p.query)

                    try:
                        amt = float(q["amount"][0])
                        assert amt > 0
                    except Exception:
                        return self._plain(400, b"amount must be positive")

                    code = q.get("type", ["01"])[0]
                    if code not in VALID_TYPES:
                        return self._plain(400, b"invalid type")

                    wait = q.get("wait", ["0"])[0] == "1"
                    outer.callback(amt, code)

                    if not wait:
                        return self._plain(200, b"OK")

                    if not outer._signal.wait(timeout=65):
                        return self._plain(504, b"timeout")

                    try:
                        body = outer._rec_queue.get_nowait()
                    except queue.Empty:
                        return self._plain(504, b"timeout")

                    if outer._rec_queue.empty():
                        outer._signal.clear()

                    return self._json(200, body)

                except (ConnectionAbortedError, BrokenPipeError):
                    return

            def log_message(self, *args):
                pass

        HTTPServer((self.host, self.port), Handler).serve_forever()

# class WebhookServer(Thread):
#     """
#     Lightweight HTTP server (daemon thread) exposing:

#       • GET /pay?amount=XX&type=YY[&wait=1]
#           – Triggers `callback(amount, type)`.
#           – אם ‎wait=1‎ ה־thread נחסם עד שקבלה מתאימה מוכנה (עד ‎65s‎).

#       • GET /receipt   or /receipt.json
#           – Returns the *last* receipt JSON (legacy endpoint).
#     """
#     def __init__(self, host: str, port: int, callback):
#         super().__init__(daemon=True)
#         self.host = host
#         self.port = port
#         self.callback = callback

#         # תור קבלות ממתינות • Event לסנכרון
#         self._rec_queue: "queue.Queue[bytes]" = queue.Queue()
#         self._signal = Event()

#         # נשמר לצורך /receipt (אחרון בלבד)
#         self._last = b"{}"

#     # נקרא ממסך-הניהול בסיום עסקה
#     def publish(self, receipt_bytes: bytes):
#         """Push new receipt, wake *בדיוק* קריאה ממתינה אחת."""
#         data = receipt_bytes or b"{}"
#         self._rec_queue.put(data)
#         self._last = data                     # לעמוד /receipt הישן
#         self._signal.set()                    # העיר ממתינים

#     def run(self):
#         outer = self  # לשימוש בתוך המחלקה הפנימית

#         class Handler(BaseHTTPRequestHandler):
#             def _plain(self, status: int, body: bytes):
#                 self.send_response(status)
#                 self.send_header("Content-Type", "text/plain")
#                 self.send_header("Content-Length", str(len(body)))
#                 self.send_header("Access-Control-Allow-Origin", "*")
#                 self.end_headers()
#                 self.wfile.write(body)

#             def _json(self, status: int, body: bytes):
#                 self.send_response(status)
#                 self.send_header("Content-Type", "application/json; charset=utf-8")
#                 self.send_header("Content-Length", str(len(body)))
#                 self.send_header("Access-Control-Allow-Origin", "*")
#                 self.end_headers()
#                 self.wfile.write(body)

#             def do_GET(self):
#                 try:
#                     p = urlparse(self.path)

#                     # /receipt → always last receipt
#                     if p.path in ("/receipt", "/receipt.json"):
#                         return self._json(200, outer._last)

#                     # /pay …
#                     if p.path != "/pay":
#                         return self._plain(404, b"Not Found")
#                     q = parse_qs(p.query)

#                     # amount
#                     try:
#                         amt = float(q["amount"][0])
#                         assert amt > 0
#                     except Exception:
#                         return self._plain(400, b"amount must be positive")

#                     # type
#                     code = q.get("type", ["01"])[0]
#                     if code not in VALID_TYPES:
#                         return self._plain(400, b"invalid type")

#                     wait = q.get("wait", ["0"])[0] == "1"

#                     # Trigger Quick-Sale in GUI
#                     outer.callback(amt, code)

#                     # Immediate return?
#                     if not wait:
#                         return self._plain(200, b"OK")

#                     # blocking until matching receipt ready
#                     if not outer._signal.wait(timeout=65):
#                         return self._plain(504, b"timeout")

#                     try:
#                         body = outer._rec_queue.get_nowait()
#                     except queue.Empty:
#                         return self._plain(504, b"timeout")

#                     # clear signal if אין עוד קבלות
#                     if outer._rec_queue.empty():
#                         outer._signal.clear()

#                     return self._json(200, body)

#                 except (ConnectionAbortedError, BrokenPipeError):
#                     # client closed the connection early, ignore
#                     return

#             def log_message(self, *args):
#                 pass  # suppress default console logging

#         HTTPServer((self.host, self.port), Handler).serve_forever()
