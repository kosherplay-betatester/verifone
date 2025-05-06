import sys
import socket
import re
import random
import os
from datetime import datetime, timezone
from base64 import b64decode, b64encode

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QDialog, QDialogButtonBox, QHBoxLayout,
    QVBoxLayout, QSplitter, QMessageBox, QSpinBox, QDateEdit
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDate

from Crypto.Cipher import DES3
from Crypto.Hash import SHA256


def generate_session_id() -> str:
    return ''.join(random.choice('0123456789') for _ in range(16))


def des3_decrypt(ktk: str, mac_key_b64: str) -> str:
    encrypted = b64decode(mac_key_b64)
    key_bytes = ktk.encode('utf-8')
    if len(key_bytes) != 16:
        raise ValueError(f"KTK must be 16 bytes, got {len(key_bytes)}")
    key24 = DES3.adjust_key_parity(key_bytes + key_bytes[:8])
    cipher = DES3.new(key24, DES3.MODE_ECB)
    return cipher.decrypt(encrypted).rstrip(b"\x00").decode('utf-8', errors='ignore')


def calc_mac(xml_str: str, mac_key: str) -> str:
    h = SHA256.new()
    h.update((xml_str + mac_key).encode('utf-8'))
    return b64encode(h.digest()).decode('utf-8')


class CommandThread(QThread):
    result = pyqtSignal(str, str)
    def __init__(self, ip: str, port: int, message: str):
        super().__init__()
        self.ip, self.port, self.message = ip, port, message

    def run(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5)
                sock.connect((self.ip, self.port))
                sock.sendall(self.message.encode('utf-8'))
                chunks = []
                while True:
                    try:
                        c = sock.recv(4096)
                        if not c: break
                        chunks.append(c)
                    except socket.timeout:
                        break
            received = b''.join(chunks).decode('utf-8', errors='replace')
        except Exception as e:
            received = f"Error: {e}"
        self.result.emit(self.message, received)


class StartTransactionForm(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("StartTransactionForm")
        self.resize(400, 260)

        self.inv_edit    = QLineEdit("100002")
        self.cash_edit   = QLineEdit("102")
        self.shift_spin  = QSpinBox(); self.shift_spin.setRange(0,999); self.shift_spin.setValue(1)
        self.date_edit   = QDateEdit(QDate.currentDate()); self.date_edit.setCalendarPopup(True)
        self.ip_edit     = QLineEdit(parent.ip_input.text())
        self.port_edit   = QLineEdit(parent.port_input.text())

        layout = QVBoxLayout(self)
        for label, widget in [
            ("Merchant invoice number", self.inv_edit),
            ("Cashier ID",            self.cash_edit),
            ("Shift ID",              self.shift_spin),
            ("Business Date",         self.date_edit),
            ("Pos IP",                self.ip_edit),
            ("Pos Port",              self.port_edit),
        ]:
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            row.addWidget(widget)
            layout.addLayout(row)

        layout.addWidget(QLabel("* All Fields Are Optional"))

        buttons = QDialogButtonBox()
        start_btn = QPushButton("Start Transaction")
        cancel_btn = QPushButton("Cancel")
        buttons.addButton(start_btn, QDialogButtonBox.AcceptRole)
        buttons.addButton(cancel_btn, QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)

        start_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

    def get_values(self) -> dict:
        return {
            "invoice": self.inv_edit.text().strip(),
            "cashier": self.cash_edit.text().strip(),
            "shift":   str(self.shift_spin.value()),
            "date":    self.date_edit.date().toString("MM/dd/yyyy"),
            "ip":      self.ip_edit.text().strip(),
            "port":    self.port_edit.text().strip(),
        }


class VerifoneApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Connector")
        self.resize(800,700)

        self.session_id = ""
        self.ktk        = ""
        self.mac_key    = ""
        self.threads    = []

        # — Connection —
        conn = QHBoxLayout()
        conn.addWidget(QLabel("IP:"));    self.ip_input   = QLineEdit("192.168.1.202"); conn.addWidget(self.ip_input)
        conn.addWidget(QLabel("Port:"));  self.port_input = QLineEdit("5015");          conn.addWidget(self.port_input)
        conn.addStretch()

        # — Register POS —
        reg = QHBoxLayout()
        self.chain_input = QLineEdit("39999")
        self.store_input = QLineEdit("0123")
        self.lane_input  = QLineEdit("074")
        self.alt_input   = QLineEdit("012345678")
        self.pos_type    = QComboBox(); self.pos_type.addItems(["ATTENDED","UNATTENDED"])
        for lbl,w in [
            ("Chain ID:",self.chain_input),
            ("Store ID:",self.store_input),
            ("Lane ID:", self.lane_input),
            ("Alt Term ID:",self.alt_input),
            ("POS Type:", self.pos_type),
        ]:
            reg.addWidget(QLabel(lbl)); reg.addWidget(w)
        self.register_btn = QPushButton("Register Terminal")
        self.register_btn.clicked.connect(self.register_terminal)
        reg.addWidget(self.register_btn)

        # — Exchange Keys —
        keyex = QHBoxLayout()
        keyex.addWidget(QLabel("KTK (16):"))
        self.ktk_input   = QLineEdit(); self.ktk_input.setEnabled(False); keyex.addWidget(self.ktk_input)
        self.exchange_btn= QPushButton("Exchange Keys"); self.exchange_btn.setEnabled(False)
        self.exchange_btn.clicked.connect(self.exchange_keys); keyex.addWidget(self.exchange_btn)
        keyex.addStretch()

        # — MAC Display —
        macrow = QHBoxLayout()
        macrow.addWidget(QLabel("MAC Key:"))
        self.mac_display = QLineEdit(); self.mac_display.setReadOnly(True)
        macrow.addWidget(self.mac_display); macrow.addStretch()

        # — Custom Command —
        cmd = QHBoxLayout()
        cmd.addWidget(QLabel("Command:"))
        self.cmd_input = QLineEdit(); cmd.addWidget(self.cmd_input)
        send_btn = QPushButton("Send Command"); send_btn.clicked.connect(self.send_custom_command)
        help_btn = QPushButton("Help");         help_btn.clicked.connect(self.show_help)
        cmd.addWidget(send_btn); cmd.addWidget(help_btn); cmd.addStretch()

        # — Status & Discover —
        self.status_btn   = QPushButton("Get Status"); self.status_btn.setEnabled(False)
        self.status_btn.clicked.connect(self.get_status)

        self.discover_btn = QPushButton("Discover")
        self.discover_btn.setEnabled(False)
        self.discover_btn.clicked.connect(self.send_discover)

        # — Logs —
        self.sent_log = QTextEdit(); self.sent_log.setReadOnly(True)
        self.recv_log = QTextEdit(); self.recv_log.setReadOnly(True)
        logs_split = QSplitter(Qt.Horizontal)
        logs_split.addWidget(self.sent_log); logs_split.addWidget(self.recv_log)

        # — Bottom row —
        clear_btn    = QPushButton("Clear Logs"); clear_btn.clicked.connect(self.clear_logs)
        start_txn_btn= QPushButton("Start Transaction"); start_txn_btn.clicked.connect(self.open_start_transaction_form)
        bottom = QHBoxLayout()
        bottom.addWidget(clear_btn); bottom.addStretch()
        bottom.addWidget(start_txn_btn); bottom.addWidget(self.discover_btn)

        # — Assemble —
        central = QWidget(); v = QVBoxLayout(central)
        for l in (conn, reg, keyex, macrow, cmd):
            v.addLayout(l)
        v.addWidget(self.status_btn)
        v.addWidget(logs_split)
        v.addLayout(bottom)
        self.setCentralWidget(central)

        self._load_mac()

    def _load_mac(self):
        if os.path.exists('mac.txt'):
            try:
                key = open('mac.txt').read().strip()
                if key:
                    self.mac_key = key
                    self.mac_display.setText(key)
                    self.status_btn.setEnabled(True)
                    self.discover_btn.setEnabled(True)
            except IOError:
                pass

    def _save_mac(self):
        try:
            with open('mac.txt','w') as f:
                f.write(self.mac_key)
        except IOError as e:
            QMessageBox.warning(self,"Warning",f"Could not save MAC key: {e}")

    def register_terminal(self):
        self.session_id = generate_session_id()
        xml = (
            "<TRANSACTION>"
            "<FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
            "<COMMAND>REGISTER</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID>"
            "<TRAINING_MODE>0</TRAINING_MODE>"
            f"<CHAIN>{self.chain_input.text()}</CHAIN>"
            f"<STORE>{self.store_input.text()}</STORE>"
            f"<LANE>{self.lane_input.text()}</LANE>"
            f"<TERMINAL_ID>{self.alt_input.text()}</TERMINAL_ID>"
            f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>"
            "</TRANSACTION>"
        )
        self._send(xml)

    def exchange_keys(self):
        self.ktk = self.ktk_input.text().strip()
        xml = (
            "<TRANSACTION>"
            "<FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
            "<COMMAND>EXCHANGE_KEYS</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID>"
            "<TRAINING_MODE>0</TRAINING_MODE>"
            f"<KTK>{self.ktk}</KTK>"
            "</TRANSACTION>"
        )
        self._send(xml)

    def get_status(self):
        txn_time = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S")
        base = (
            "<TRANSACTION>"
            "<FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
            "<COMMAND>STATUS</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID>"
            "<TRAINING_MODE>0</TRAINING_MODE>"
            f"<TRANSACTION_TIME>{txn_time}</TRANSACTION_TIME>"
        )
        xml_wo_mac = base + "<MAC></MAC></TRANSACTION>"
        mac = calc_mac(xml_wo_mac, self.mac_key)
        xml = base + f"<MAC>{mac}</MAC></TRANSACTION>"
        self._send(xml)

    def send_custom_command(self):
        cmd = self.cmd_input.text().strip().upper()
        if not cmd:
            QMessageBox.warning(self, "Warning", "Enter a command.")
            return
        if cmd == 'PING':
            xml = (
                "<TRANSACTION>"
                "<FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
                "<COMMAND>PING</COMMAND>"
                f"<SESSION_ID>{self.session_id}</SESSION_ID>"
                "<TRAINING_MODE>0</TRAINING_MODE>"
                "</TRANSACTION>"
            )
        else:
            txn_time = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S")
            base = (
                "<TRANSACTION>"
                "<FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
                f"<COMMAND>{cmd}</COMMAND>"
                f"<SESSION_ID>{self.session_id}</SESSION_ID>"
                "<TRAINING_MODE>0</TRAINING_MODE>"
                f"<TRANSACTION_TIME>{txn_time}</TRANSACTION_TIME>"
            )
            xml_wo_mac = base + "<MAC></MAC></TRANSACTION>"
            mac = calc_mac(xml_wo_mac, self.mac_key)
            xml = base + f"<MAC>{mac}</MAC></TRANSACTION>"
        self._send(xml)

    def send_discover(self):
        """
        DISCOVERY as per PDF:
        <FUNCTION_GROUP>PAYMENT</FUNCTION_GROUP>
        <COMMAND>DISCOVERY</COMMAND>
        with TRANSACTION_TIME + MAC :contentReference[oaicite:4]{index=4}:contentReference[oaicite:5]{index=5}
        """
        txn_time = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S")
        base = (
            "<TRANSACTION>"
            "<FUNCTION_GROUP>PAYMENT</FUNCTION_GROUP>"
            "<COMMAND>DISCOVERY</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID>"
            "<TRAINING_MODE>0</TRAINING_MODE>"
            f"<TRANSACTION_TIME>{txn_time}</TRANSACTION_TIME>"
        )
        xml_wo_mac = base + "<MAC></MAC></TRANSACTION>"
        mac = calc_mac(xml_wo_mac, self.mac_key)
        xml = base + f"<MAC>{mac}</MAC></TRANSACTION>"
        self._send(xml)

    def open_start_transaction_form(self):
        dlg = StartTransactionForm(self)
        if dlg.exec_() != QDialog.Accepted:
            return

        vals = dlg.get_values()
        txn_time = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S")
        base = (
            "<TRANSACTION>"
            "<FUNCTION_GROUP>SESSION</FUNCTION_GROUP>"
            "<COMMAND>START_TRAN</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID>"
            "<TRAINING_MODE>0</TRAINING_MODE>"
            f"<TRANSACTION_TIME>{txn_time}</TRANSACTION_TIME>"
        )
        # optional fields
        if vals["invoice"]: base += f"<INVOICE>{vals['invoice']}</INVOICE>"
        if vals["cashier"]: base += f"<CASHIER_ID>{vals['cashier']}</CASHIER_ID>"
        if vals["shift"]:   base += f"<SHIFT_ID>{vals['shift']}</SHIFT_ID>"
        if vals["date"]:
            bdate = datetime.strptime(vals["date"], "%m/%d/%Y").strftime("%Y%m%d")
            base += f"<BUSINESSDATE>{bdate}</BUSINESSDATE>"
        if vals["ip"]:   base += f"<POS_IP>{vals['ip']}</POS_IP>"
        if vals["port"]: base += f"<POS_PORT>{vals['port']}</POS_PORT>"

        xml_wo_mac = base + "<MAC></MAC></TRANSACTION>"
        mac = calc_mac(xml_wo_mac, self.mac_key)
        xml = base + f"<MAC>{mac}</MAC></TRANSACTION>"

        ip   = vals["ip"]   or self.ip_input.text()
        port = vals["port"] or self.port_input.text()
        self._send(xml, ip_override=ip, port_override=port)

    def _send(self, xml: str, ip_override=None, port_override=None):
        ip   = (ip_override   or self.ip_input.text()).strip()
        port = (port_override or self.port_input.text()).strip()
        try:
            port = int(port)
        except ValueError:
            QMessageBox.critical(self, "Invalid Port", "Enter a valid port number.")
            return
        thread = CommandThread(ip, port, xml)
        thread.result.connect(self._handle_result)
        thread.finished.connect(lambda: self.threads.remove(thread))
        self.threads.append(thread)
        thread.start()

    def _handle_result(self, sent: str, received: str):
        self.sent_log.append(sent)
        self.recv_log.append(received)

        # After REGISTER → enable KTK entry & Exchange Keys
        if '<COMMAND>REGISTER' in sent and '<EVENT>COMPLETED' in received:
            self.ktk_input.setEnabled(True)
            self.exchange_btn.setEnabled(True)

        # After EXCHANGE_KEYS → decrypt MAC key, save, then enable Status & Discover
        if '<COMMAND>EXCHANGE_KEYS' in sent and '<MAC_KEY>' in received:
            m = re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>', received)
            if m:
                try:
                    self.mac_key = des3_decrypt(self.ktk, m.group(1))
                    self.mac_display.setText(self.mac_key)
                    self._save_mac()
                    self.status_btn.setEnabled(True)
                    self.discover_btn.setEnabled(True)
                    self.get_status()
                except Exception as e:
                    QMessageBox.critical(self, "Error", str(e))

    def show_help(self):
        cmds = [
            'PING','REGISTER','EXCHANGE_KEYS','STATUS',
            'OPEN_LANE','CLOSE_LANE','EOD',
            'START_TRANSACTION','PAYMENT_COMPLETED','FINISH_TRANSACTION',
            'DISCOVERY'
        ]
        dlg = QDialog(self); dlg.setWindowTitle("Available Commands")
        lay = QVBoxLayout(dlg)
        text = QTextEdit(); text.setReadOnly(True)
        text.setPlainText("\n".join(cmds)); lay.addWidget(text)
        btns = QDialogButtonBox(QDialogButtonBox.Close)
        btns.rejected.connect(dlg.reject); lay.addWidget(btns)
        dlg.resize(300,200); dlg.exec_()

    def clear_logs(self):
        self.sent_log.clear()
        self.recv_log.clear()

    def closeEvent(self, ev):
        for t in list(self.threads):
            t.quit(); t.wait()
        ev.accept()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = VerifoneApp()
    w.show()
    sys.exit(app.exec_())
