# network.py

"""
Network I/O threads:
- SockThread: handles one socket connection to the P400 device
- WebhookServer: lightweight HTTP server on localhost to receive /pay?amount=... calls
  and to serve all generated receipts at /receipts in JSON form.
"""

import socket
from http.server     import HTTPServer, BaseHTTPRequestHandler
from threading       import Thread
from urllib.parse    import urlparse, parse_qs
from PyQt5.QtCore    import QThread, pyqtSignal
import json  # Added for JSON receipts

# Allowed transaction type codes
VALID_TYPES = {'01','02','03','06','30','53','55'}


class SockThread(QThread):
    """
    QThread that opens a TCP socket to (ip,port), sends `msg` (XML),
    reads until EOF or timeout, and emits a (sent, recv) signal.
    """
    result = pyqtSignal(str, str)  # sent-xml, recv-xml

    def __init__(self, ip: str, port: int, msg: str):
        super().__init__()
        self.ip, self.port, self.msg = ip, port, msg

    def run(self):
        sent, recv = self.msg, ''
        try:
            with socket.create_connection((self.ip, self.port), timeout=7) as s:
                s.sendall(sent.encode())
                chunks = []
                while True:
                    try:
                        buf = s.recv(4096)
                    except socket.timeout:
                        break
                    if not buf:
                        break
                    chunks.append(buf)
                recv = b''.join(chunks).decode('utf-8', 'replace')
        except Exception as e:
            recv = f"Error: {e}"
        self.result.emit(sent, recv)


class WebhookServer(Thread):
    """
    Background HTTP server that listens on (host, port).
    - GET /pay?amount=xx&type=yy → invokes callback(amount, type)
    - GET /receipts            → returns JSON array of all saved receipts
    """
    def __init__(self, host: str, port: int, callback):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.callback = callback
        self.receipts = []  # Store receipt JSON objects

    def run(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                p = urlparse(self.path)

                # 1) Trigger payment
                if p.path == '/pay':
                    q = parse_qs(p.query)
                    try:
                        amt = float(q['amount'][0]); assert amt > 0
                    except Exception:
                        return self._respond(400, b"amount must be positive")
                    code = q.get('type', ['01'])[0]
                    if code not in VALID_TYPES:
                        return self._respond(400, b"invalid type")
                    self._respond(200, b"OK")
                    outer.callback(amt, code)
                    return

                # 2) Serve stored JSON receipts
                if p.path == '/receipts':
                    data = json.dumps(outer.receipts, ensure_ascii=False).encode('utf-8')
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

                # 404 for anything else
                return self._respond(404, b"Not Found")

            def _respond(self, status: int, body: bytes):
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                # Silence the default stderr logging
                pass

        server = HTTPServer((self.host, self.port), Handler)
        try:
            server.serve_forever()
        finally:
            server.server_close()
