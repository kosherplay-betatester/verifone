import socket
from PyQt5.QtCore import QThread, pyqtSignal
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from xml_builder import VALID_TYPES

class SockThread(QThread):
    """Send XML over TCP, gather reply."""
    result = pyqtSignal(str, str)  # sent_xml, recv_xml

    def __init__(self, ip: str, port: int, msg: str):
        super().__init__()
        self.ip, self.port, self.msg = ip, port, msg

    def run(self):
        sent, recv = self.msg, ""
        try:
            with socket.create_connection((self.ip, self.port), timeout=7) as s:
                s.sendall(sent.encode())
                parts = []
                while True:
                    try:
                        buf = s.recv(4096)
                    except socket.timeout:
                        break
                    if not buf:
                        break
                    parts.append(buf)
                recv = b"".join(parts).decode("utf-8", "replace")
        except Exception as e:
            recv = f"Error: {e}"
        self.result.emit(sent, recv)


class WebhookServer(QThread):
    """Simple GET /pay?amount=&type= webhook."""
    receivedPayment = pyqtSignal(float, str)

    def __init__(self, host="localhost", port=8080, parent=None):
        super().__init__(parent)
        self.host, self.port = host, port

    def run(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                p = urlparse(self.path)
                if p.path != "/pay":
                    return self._reply(404, b"Not Found")
                q = parse_qs(p.query)
                if "amount" not in q:
                    return self._reply(400, b"'amount' param required")
                try:
                    amt = float(q["amount"][0]); assert amt > 0
                except:
                    return self._reply(400, b"amount must be positive")
                tcode = q.get("type", ["01"])[0]
                if tcode not in VALID_TYPES:
                    return self._reply(400, b"invalid type")
                self._reply(200, b"OK")
                outer.receivedPayment.emit(amt, tcode)

            def log_message(self, *args):
                return

            def _reply(self, code, body: bytes):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = HTTPServer((self.host, self.port), H)
        srv.allow_reuse_address = True
        try:
            srv.serve_forever()
        finally:
            srv.server_close()
