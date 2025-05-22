import socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from urllib.parse import urlparse, parse_qs
from PyQt5.QtCore import QThread, pyqtSignal

# Allowed transaction type codes
VALID_TYPES = {'01', '02', '03', '06', '30', '53', '55'}

class SockThread(QThread):
    """
    QThread that opens a TCP socket to (ip, port), sends `msg` (XML),
    reads until EOF or timeout, and emits a (sent, recv) signal.
    """
    result = pyqtSignal(str, str)  # sent-xml, recv-xml

    def __init__(self, ip: str, port: int, msg: str):
        # No parent QObject
        super().__init__(None)
        self.ip = ip
        self.port = port
        self.msg = msg

    def run(self):
        sent, recv = self.msg, ''
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
                recv = b''.join(chunks).decode('utf-8', 'replace')
        except Exception as e:
            recv = f"Error: {e}"
        self.result.emit(sent, recv)

class WebhookServer(Thread):
    """
    Background HTTP server that listens on (host, port).
    - GET /pay?amount=xx&type=yy → triggers callback(amount, type)
    - GET /receipt or /receipt.json → returns the last receipt as JSON
    """
    def __init__(self, host: str, port: int, callback):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.callback = callback

    def run(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                p = urlparse(self.path)

                # Serve last receipt
                if p.path in ('/receipt', '/receipt.json'):
                    try:
                        with open("receipt.json", "rb") as f:
                            body = f.read()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.end_headers()
                        self.wfile.write(body)
                    except FileNotFoundError:
                        self.send_response(404)
                        self.send_header("Content-Type", "text/plain")
                        self.end_headers()
                        self.wfile.write(b"receipt.json not found")
                    return

                # Original /pay endpoint
                if p.path != '/pay':
                    return self._respond(404, b"Not Found")

                q = parse_qs(p.query)
                try:
                    amt = float(q['amount'][0])
                    assert amt > 0
                except Exception:
                    return self._respond(400, b"amount must be positive")

                code = q.get('type', ['01'])[0]
                if code not in VALID_TYPES:
                    return self._respond(400, b"invalid type")

                # Respond OK and trigger main app
                self._respond(200, b"OK")
                outer.callback(amt, code)

            def log_message(self, *args):
                # Suppress default HTTP server logging
                pass

            def _respond(self, status: int, body: bytes):
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer((self.host, self.port), Handler)
        try:
            server.serve_forever()
        finally:
            server.server_close()