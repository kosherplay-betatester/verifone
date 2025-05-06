import sys
import socket
import random
from datetime import datetime, timezone
from base64 import b64encode, b64decode
from Crypto.Hash import SHA256
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit,
    QPushButton, QTextEdit, QHBoxLayout, QVBoxLayout, QMessageBox
)


def generate_session_id() -> str:
    return ''.join(random.choice('0123456789') for _ in range(16))


def calc_mac(xml_str: str, mac_key: str) -> str:
    h = SHA256.new()
    h.update((xml_str + mac_key).encode('utf-8'))
    return b64encode(h.digest()).decode('utf-8')


def send_transaction(ip: str, port: int, message: str, timeout=5) -> str:
    try:
        with socket.create_connection((ip, port), timeout=timeout) as sock:
            sock.sendall(message.encode('utf-8'))
            data = sock.recv(8192)
            return data.decode('utf-8', errors='replace')
    except Exception as e:
        return f"Error: {e}"


class OpenLaneApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Open Lane")
        self.resize(500, 400)

        # IP and Port inputs
        ip_label = QLabel("IP:")
        self.ip_input = QLineEdit("192.168.1.202")
        port_label = QLabel("Port:")
        self.port_input = QLineEdit("5015")

        connect_layout = QHBoxLayout()
        connect_layout.addWidget(ip_label)
        connect_layout.addWidget(self.ip_input)
        connect_layout.addWidget(port_label)
        connect_layout.addWidget(self.port_input)

        # Open Lane button
        self.open_lane_btn = QPushButton("Open Lane")
        self.open_lane_btn.clicked.connect(self.open_lane)

        # Response log
        self.log = QTextEdit()
        self.log.setReadOnly(True)

        # Main layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.addLayout(connect_layout)
        main_layout.addWidget(self.open_lane_btn)
        main_layout.addWidget(self.log)
        self.setCentralWidget(main_widget)

    def open_lane(self):
        ip = self.ip_input.text().strip()
        try:
            port = int(self.port_input.text().strip())
        except ValueError:
            QMessageBox.critical(self, "Invalid Port", "Please enter a valid port number.")
            return

        # Load MAC key
        try:
            with open('mac.txt', 'r') as f:
                mac_key = f.read().strip()
        except FileNotFoundError:
            QMessageBox.critical(self, "MAC Key Missing", "mac.txt not found. Perform key exchange first.")
            return

        # Build XML without MAC
        session_id = generate_session_id()
        txn_time = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        xml_base = (
            f"<TRANSACTION>"
            f"<FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
            f"<COMMAND>OPEN_LANE</COMMAND>"
            f"<SESSION_ID>{session_id}</SESSION_ID>"
            f"<TRAINING_MODE>0</TRAINING_MODE>"
            f"<TRANSACTION_TIME>{txn_time}</TRANSACTION_TIME>"
            f"<MAC></MAC>"
            f"</TRANSACTION>"
        )
        # Calculate MAC
        mac_val = calc_mac(xml_base, mac_key)
        xml = xml_base.replace('<MAC></MAC>', f'<MAC>{mac_val}</MAC>')

        # Send and log
        self.log.append(f"Sent:\n{xml}\n")
        response = send_transaction(ip, port, xml)
        self.log.append(f"Received:\n{response}\n")


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = OpenLaneApp()
    window.show()
    sys.exit(app.exec_())