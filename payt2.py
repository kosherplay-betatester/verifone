import sys
import socket
import re
import random
from datetime import datetime, timezone
from base64 import b64decode, b64encode
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QHBoxLayout, QVBoxLayout, QSplitter, QMessageBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from Crypto.Cipher import DES3
from Crypto.Hash import SHA256


def generate_session_id() -> str:
    """Generate a random 16-digit numeric session ID."""
    return ''.join(random.choice('0123456789') for _ in range(16))


def des3_decrypt(ktk: str, mac_key_b64: str) -> str:
    """Decrypt a Base64-encoded MAC_KEY using 3DES-ECB with 16-byte ASCII KTK."""
    encrypted = b64decode(mac_key_b64)
    key_bytes = ktk.encode('utf-8')
    if len(key_bytes) != 16:
        raise ValueError(f"KTK must be 16 bytes, got {len(key_bytes)}")
    # Build 24-byte key: KTK (16 bytes) + first 8 bytes
    key24 = key_bytes + key_bytes[:8]
    key24 = DES3.adjust_key_parity(key24)
    cipher = DES3.new(key24, DES3.MODE_ECB)
    decrypted = cipher.decrypt(encrypted)
    return decrypted.rstrip(b"\x00").decode('utf-8', errors='ignore')


def calc_mac(xml_str: str, mac_key: str) -> str:
    """Compute SHA-256 MAC of xml_str+mac_key, output as Base64 string."""
    h = SHA256.new()
    # Append MAC key directly after xml (no extra whitespace)
    h.update((xml_str + mac_key).encode('utf-8'))
    # Return Base64-encoded raw 32-byte hash
    return b64encode(h.digest()).decode('utf-8')


class CommandThread(QThread):
    result = pyqtSignal(str, str)

    def __init__(self, ip: str, port: int, message: str):
        super().__init__()
        self.ip = ip
        self.port = port
        self.message = message

    def run(self):
        sent = self.message
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                sock.connect((self.ip, self.port))
                sock.sendall(sent.encode('utf-8'))
                chunks = []
                while True:
                    try:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    except socket.timeout:
                        break
                received = b''.join(chunks).decode('utf-8', errors='replace')
        except Exception as e:
            received = f"Error: {e}"
        self.result.emit(sent, received)


class VerifoneApp(QMainWindow):
    """GUI for Verifone P400: register, key exchange, and status."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Connector")
        self.resize(800, 600)

        # State
        self.session_id = ''
        self.ktk = ''
        self.mac_key = ''
        self.threads = []

        # Connection inputs
        conn_layout = QHBoxLayout()
        conn_layout.addWidget(QLabel("IP:"))
        self.ip_input = QLineEdit("192.168.1.202")
        conn_layout.addWidget(self.ip_input)
        conn_layout.addWidget(QLabel("Port:"))
        self.port_input = QLineEdit("5015")
        conn_layout.addWidget(self.port_input)
        conn_layout.addStretch()

        # Registration inputs
        self.chain_input = QLineEdit("39999")
        self.store_input = QLineEdit("0123")
        self.lane_input = QLineEdit("074")
        self.alt_input = QLineEdit("012345678")
        self.pos_type = QComboBox(); self.pos_type.addItems(["ATTENDED", "UNATTENDED"]);
        reg_layout = QHBoxLayout()
        for label, widget in [
            ("Chain ID:", self.chain_input),
            ("Store ID:", self.store_input),
            ("Lane ID:", self.lane_input),
            ("Alt Term ID:", self.alt_input),
            ("POS Type:", self.pos_type)
        ]:
            reg_layout.addWidget(QLabel(label))
            reg_layout.addWidget(widget)
        self.register_btn = QPushButton("Register Terminal")
        self.register_btn.clicked.connect(self.register_terminal)
        reg_layout.addWidget(self.register_btn)

        # Key exchange inputs
        ex_layout = QHBoxLayout()
        ex_layout.addWidget(QLabel("KTK (16 chars):"))
        self.ktk_input = QLineEdit(); self.ktk_input.setEnabled(False)
        ex_layout.addWidget(self.ktk_input)
        self.exchange_btn = QPushButton("Exchange Keys"); self.exchange_btn.setEnabled(False)
        self.exchange_btn.clicked.connect(self.exchange_keys)
        ex_layout.addWidget(self.exchange_btn)
        ex_layout.addStretch()

        # MAC Key display
        mac_layout = QHBoxLayout()
        mac_layout.addWidget(QLabel("Derived MAC Key:"))
        self.mac_display = QLineEdit(); self.mac_display.setReadOnly(True)
        mac_layout.addWidget(self.mac_display)
        mac_layout.addStretch()

        # Status button
        self.status_btn = QPushButton("Get Status"); self.status_btn.setEnabled(False)
        self.status_btn.clicked.connect(self.get_status)

        # Logs
        self.sent_log = QTextEdit(); self.sent_log.setReadOnly(True)
        self.recv_log = QTextEdit(); self.recv_log.setReadOnly(True)
        logs_split = QSplitter(Qt.Horizontal); logs_split.addWidget(self.sent_log); logs_split.addWidget(self.recv_log)

        clear_btn = QPushButton("Clear Logs"); clear_btn.clicked.connect(self.clear_logs)

        # Layout assembly
        main = QWidget(); v = QVBoxLayout(main)
        v.addLayout(conn_layout)
        v.addLayout(reg_layout)
        v.addLayout(ex_layout)
        v.addLayout(mac_layout)
        v.addWidget(self.status_btn)
        v.addWidget(logs_split)
        v.addWidget(clear_btn)
        self.setCentralWidget(main)

    def register_terminal(self):
        self.session_id = generate_session_id()
        xml = (
            f"<TRANSACTION>"
            f"<FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
            f"<COMMAND>REGISTER</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID>"
            f"<TRAINING_MODE>0</TRAINING_MODE>"
            f"<CHAIN>{self.chain_input.text()}</CHAIN>"
            f"<STORE>{self.store_input.text()}</STORE>"
            f"<LANE>{self.lane_input.text()}</LANE>"
            f"<TERMINAL_ID>{self.alt_input.text()}</TERMINAL_ID>"
            f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>"
            f"</TRANSACTION>"
        )
        self._send(xml)

    def exchange_keys(self):
        self.ktk = self.ktk_input.text().strip()
        xml = (
            f"<TRANSACTION>"
            f"<FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
            f"<COMMAND>EXCHANGE_KEYS</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID>"
            f"<TRAINING_MODE>0</TRAINING_MODE>"
            f"<KTK>{self.ktk}</KTK>"
            f"</TRANSACTION>"
        )
        self._send(xml)

    def get_status(self):
        txn_time = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        base = (
            f"<TRANSACTION>"
            f"<FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
            f"<COMMAND>STATUS</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID>"
            f"<TRAINING_MODE>0</TRAINING_MODE>"
            f"<TRANSACTION_TIME>{txn_time}</TRANSACTION_TIME>"
        )
        xml_no_mac = base + "<MAC></MAC></TRANSACTION>"
        mac_val = calc_mac(xml_no_mac, self.mac_key)
        xml = base + f"<MAC>{mac_val}</MAC></TRANSACTION>"
        self._send(xml)

    def _send(self, xml: str):
        ip = self.ip_input.text().strip()
        try:
            port = int(self.port_input.text().strip())
        except ValueError:
            QMessageBox.critical(self, "Invalid Port", "Enter a valid port number.")
            return
        thread = CommandThread(ip, port, xml)
        thread.result.connect(self._handle_result)
        thread.finished.connect(lambda t=thread: self.threads.remove(t))
        self.threads.append(thread)
        thread.start()

    def _handle_result(self, sent: str, received: str):
        self.sent_log.append(sent)
        self.recv_log.append(received)
        if '<COMMAND>REGISTER' in sent and '<EVENT>COMPLETED' in received:
            self.ktk_input.setEnabled(True); self.exchange_btn.setEnabled(True)
        if '<COMMAND>EXCHANGE_KEYS' in sent and '<MAC_KEY>' in received:
            match = re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>', received)
            if match:
                try:
                    self.mac_key = des3_decrypt(self.ktk, match.group(1))
                    self.mac_display.setText(self.mac_key)
                    self.status_btn.setEnabled(True)
                except Exception as e:
                    QMessageBox.critical(self, "Decryption Error", str(e))

    def clear_logs(self):
        self.sent_log.clear(); self.recv_log.clear()

    def closeEvent(self, event):
        for t in list(self.threads): t.quit(); t.wait()
        event.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = VerifoneApp()
    window.show()
    sys.exit(app.exec_())