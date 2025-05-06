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


# ---------------------------------------------------------------------------
#  Utility helpers
# ---------------------------------------------------------------------------

def generate_session_id() -> str:
    """Return a random 16‑digit numeric session‑id string."""
    return ''.join(random.choice('0123456789') for _ in range(16))


def des3_decrypt(ktk: str, mac_key_b64: str) -> str:
    """Decrypt MAC_KEY (base64) with the 16‑byte ASCII KTK."""
    encrypted = b64decode(mac_key_b64)
    key_bytes = ktk.encode('utf‑8')
    if len(key_bytes) != 16:
        raise ValueError(f"KTK must be 16 bytes, got {len(key_bytes)}")
    key24 = DES3.adjust_key_parity(key_bytes + key_bytes[:8])
    cipher = DES3.new(key24, DES3.MODE_ECB)
    return cipher.decrypt(encrypted).rstrip(b"\0").decode('utf‑8', 'ignore')


def calc_mac(xml_without_mac: str, mac_key: str) -> str:
    """SHA‑256(xml + mac_key) → base64, per spec."""
    h = SHA256.new()
    h.update((xml_without_mac + mac_key).encode('utf‑8'))
    return b64encode(h.digest()).decode('utf‑8')


# ---------------------------------------------------------------------------
#  Worker thread for socket I/O (non‑blocking UI)
# ---------------------------------------------------------------------------

class CommandThread(QThread):
    result = pyqtSignal(str, str)          # sent, received

    def __init__(self, ip: str, port: int, message: str):
        super().__init__()
        self.ip, self.port, self.message = ip, port, message

    def run(self):
        sent = self.message
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                s.connect((self.ip, self.port))
                s.sendall(sent.encode('utf‑8'))
                chunks = []
                while True:
                    try:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    except socket.timeout:
                        break
                received = b''.join(chunks).decode('utf‑8', 'replace')
        except Exception as e:
            received = f"Error: {e}"
        self.result.emit(sent, received)


# ---------------------------------------------------------------------------
#  Dialog used for Start Transaction
# ---------------------------------------------------------------------------

class StartTransactionDialog(QDialog):
    """Modal form that mirrors the screenshot layout."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start Transaction")

        # -- form controls ---------------------------------------------------
        self.inv_input = QLineEdit()
        self.cashier_input = QLineEdit()
        self.shift_input = QSpinBox()
        self.shift_input.setRange(0, 9999)
        self.shift_input.setValue(1)
        self.date_input = QDateEdit()
        self.date_input.setCalendarPopup(True)
        self.date_input.setDate(QDate.currentDate())

        self.pos_ip_input = QLineEdit()
        self.pos_port_input = QLineEdit()

        # -- layout (two column label / field) -------------------------------
        form = QVBoxLayout(self)
        for label, widget in (
            ("Merchant invoice number", self.inv_input),
            ("Cashier ID",               self.cashier_input),
            ("Shift ID",                 self.shift_input),
            ("Business Date",            self.date_input),
            ("Pos IP",                   self.pos_ip_input),
            ("Pos Port",                 self.pos_port_input),
        ):
            row = QHBoxLayout()
            row.addWidget(QLabel(label), 1)
            row.addWidget(widget, 3)
            form.addLayout(row)

        form.addSpacing(6)
        form.addWidget(QLabel("* All Fields Are Optional"))

        # buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Start Transaction")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addWidget(buttons)

    # convenience getters ----------------------------------------------------
    def data(self):
        """Return dict of the filled‑in optional fields (empty strings omitted)."""
        date = self.date_input.date().toString("MM/dd/yyyy")
        return {
            'INVOICE':       self.inv_input.text().strip(),
            'CASHIER_ID':    self.cashier_input.text().strip(),
            'SHIFT_ID':      str(self.shift_input.value()) if self.shift_input.value() else '',
            'BUSINESSDATE':  self.date_input.date().toString("yyyyMMdd"),
            'POS_IP':        self.pos_ip_input.text().strip(),
            'POS_PORT':      self.pos_port_input.text().strip(),
        }


# ---------------------------------------------------------------------------
#  Main application window
# ---------------------------------------------------------------------------

class VerifoneApp(QMainWindow):

    # ------------------------------------------------------------------ init
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Connector")
        self.resize(900, 750)

        # session state
        self.session_id = ''
        self.ktk = ''
        self.mac_key = ''
        self.threads = []

        # --------------------------------------------------- connection line
        conn = QHBoxLayout()
        conn.addWidget(QLabel("IP:"))
        self.ip_input = QLineEdit("192.168.1.202")
        conn.addWidget(self.ip_input)
        conn.addWidget(QLabel("Port:"))
        self.port_input = QLineEdit("5015")
        conn.addWidget(self.port_input)
        conn.addStretch()

        # ------------------------------------------- register POS line
        reg = QHBoxLayout()
        self.chain_input = QLineEdit("39999")
        self.store_input = QLineEdit("0123")
        self.lane_input = QLineEdit("074")
        self.alt_input = QLineEdit("012345678")
        self.pos_type = QComboBox()
        self.pos_type.addItems(["ATTENDED", "UNATTENDED"])
        for lbl, w in (("Chain ID:", self.chain_input),
                       ("Store ID:", self.store_input),
                       ("Lane ID:",  self.lane_input),
                       ("Alt Term ID:", self.alt_input),
                       ("POS Type:", self.pos_type)):
            reg.addWidget(QLabel(lbl))
            reg.addWidget(w)
        self.register_btn = QPushButton("Register Terminal")
        self.register_btn.clicked.connect(self.register_terminal)
        reg.addWidget(self.register_btn)

        # -------------------------------------------- key‑exchange line
        key = QHBoxLayout()
        key.addWidget(QLabel("KTK (16):"))
        self.ktk_input = QLineEdit()
        self.ktk_input.setEnabled(False)
        key.addWidget(self.ktk_input)
        self.exchange_btn = QPushButton("Exchange Keys")
        self.exchange_btn.setEnabled(False)
        self.exchange_btn.clicked.connect(self.exchange_keys)
        key.addWidget(self.exchange_btn)
        key.addStretch()

        # -------------------------------------------- MAC display
        mac_row = QHBoxLayout()
        mac_row.addWidget(QLabel("MAC Key:"))
        self.mac_display = QLineEdit()
        self.mac_display.setReadOnly(True)
        mac_row.addWidget(self.mac_display)
        mac_row.addStretch()

        # -------------------------------------------- Start‑Tx + command row
        cmd = QHBoxLayout()
        self.start_btn = QPushButton("Start Transaction …")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.open_start_dialog)
        cmd.addWidget(self.start_btn)

        cmd.addWidget(QLabel("Command:"))
        self.cmd_input = QLineEdit()
        cmd.addWidget(self.cmd_input)
        self.send_cmd_btn = QPushButton("Send Command")
        self.send_cmd_btn.clicked.connect(self.send_custom_command)
        cmd.addWidget(self.send_cmd_btn)
        self.help_btn = QPushButton("Help")
        self.help_btn.clicked.connect(self.show_help)
        cmd.addWidget(self.help_btn)
        cmd.addStretch()

        # -------------------------------------------- status button
        self.status_btn = QPushButton("Get Status")
        self.status_btn.setEnabled(False)
        self.status_btn.clicked.connect(self.get_status)

        # -------------------------------------------- logs
        self.sent_log = QTextEdit();  self.sent_log.setReadOnly(True)
        self.recv_log = QTextEdit();  self.recv_log.setReadOnly(True)
        logs = QSplitter(Qt.Horizontal)
        logs.addWidget(self.sent_log); logs.addWidget(self.recv_log)

        # -------------------------------------------- clear‑log button
        clear_btn = QPushButton("Clear Logs")
        clear_btn.clicked.connect(lambda: (self.sent_log.clear(), self.recv_log.clear()))

        # ------------------------------------------------ main layout
        mainw = QWidget();  lay = QVBoxLayout(mainw)
        lay.addLayout(conn); lay.addLayout(reg); lay.addLayout(key); lay.addLayout(mac_row)
        lay.addLayout(cmd);  lay.addWidget(self.status_btn); lay.addWidget(logs); lay.addWidget(clear_btn)
        self.setCentralWidget(mainw)

        self._load_mac()

    # ---------------------------------------------------------------- helpers
    def _load_mac(self):
        if os.path.exists("mac.txt"):
            try:
                with open("mac.txt") as f:
                    self.mac_key = f.read().strip()
                if self.mac_key:
                    self.mac_display.setText(self.mac_key)
                    self.status_btn.setEnabled(True)
                    self.start_btn.setEnabled(True)
            except OSError:
                pass

    def _save_mac(self):
        try:
            with open("mac.txt", "w") as f:
                f.write(self.mac_key)
        except OSError as e:
            QMessageBox.warning(self, "Warning", f"Could not save MAC key: {e}")

    # ------------------------------------------------------ socket wrapper
    def _send(self, xml: str):
        ip = self.ip_input.text().strip()
        try:
            port = int(self.port_input.text().strip())
        except ValueError:
            QMessageBox.critical(self, "Invalid Port", "Enter a valid port number.")
            return
        t = CommandThread(ip, port, xml)
        t.result.connect(self._handle_result)
        t.finished.connect(lambda: self.threads.remove(t))
        self.threads.append(t)
        t.start()

    # ------------------------------------------------------ UI callbacks
    def register_terminal(self):
        self.session_id = generate_session_id()
        xml = (
            f"<TRANSACTION><FUNCTION_GROUP>ADMIN</FUNCTION_GROUP><COMMAND>REGISTER</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
            f"<CHAIN>{self.chain_input.text()}</CHAIN><STORE>{self.store_input.text()}</STORE>"
            f"<LANE>{self.lane_input.text()}</LANE><TERMINAL_ID>{self.alt_input.text()}</TERMINAL_ID>"
            f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE></TRANSACTION>"
        )
        self._send(xml)

    def exchange_keys(self):
        self.ktk = self.ktk_input.text().strip()
        xml = (
            f"<TRANSACTION><FUNCTION_GROUP>ADMIN</FUNCTION_GROUP><COMMAND>EXCHANGE_KEYS</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
            f"<KTK>{self.ktk}</KTK></TRANSACTION>"
        )
        self._send(xml)

    def get_status(self):
        txn_time = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        base = (
            f"<TRANSACTION><FUNCTION_GROUP>ADMIN</FUNCTION_GROUP><COMMAND>STATUS</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
            f"<TRANSACTION_TIME>{txn_time}</TRANSACTION_TIME>"
        )
        xml_no_mac = base + "<MAC></MAC></TRANSACTION>"
        mac_val = calc_mac(xml_no_mac, self.mac_key)
        self._send(base + f"<MAC>{mac_val}</MAC></TRANSACTION>")

    # ----------------------- new : open dialog and send START_TRAN
    def open_start_dialog(self):
        if not self.mac_key:
            QMessageBox.warning(self, "Missing MAC", "Exchange keys first to obtain MAC key.")
            return
        dlg = StartTransactionDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        data = dlg.data()

        txn_time = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        base = (
            f"<TRANSACTION><FUNCTION_GROUP>SESSION</FUNCTION_GROUP><COMMAND>START_TRAN</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
            f"<TRANSACTION_TIME>{txn_time}</TRANSACTION_TIME>"
        )
        # append only the non‑empty optional tags
        for tag, val in data.items():
            if val:
                base += f"<{tag}>{val}</{tag}>"
        xml_no_mac = base + "<MAC></MAC></TRANSACTION>"
        mac_val = calc_mac(xml_no_mac, self.mac_key)
        self._send(base + f"<MAC>{mac_val}</MAC></TRANSACTION>")

    # ----------------------- custom command (unchanged)
    def send_custom_command(self):
        cmd = self.cmd_input.text().strip().upper()
        if not cmd:
            QMessageBox.warning(self, "Warning", "Enter a command.")
            return
        if cmd == 'PING':
            xml = (f"<TRANSACTION><FUNCTION_GROUP>ADMIN</FUNCTION_GROUP><COMMAND>PING</COMMAND>"
                   f"<SESSION_ID>{self.session_id}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE></TRANSACTION>")
        else:
            txn_time = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
            base = (f"<TRANSACTION><FUNCTION_GROUP>ADMIN</FUNCTION_GROUP><COMMAND>{cmd}</COMMAND>"
                    f"<SESSION_ID>{self.session_id}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
                    f"<TRANSACTION_TIME>{txn_time}</TRANSACTION_TIME>")
            xml_no_mac = base + "<MAC></MAC></TRANSACTION>"
            mac_val = calc_mac(xml_no_mac, self.mac_key)
            xml = base + f"<MAC>{mac_val}</MAC></TRANSACTION>"
        self._send(xml)

    def show_help(self):
        cmds = ('PING', 'REGISTER', 'EXCHANGE_KEYS', 'STATUS', 'OPEN_LANE', 'CLOSE_LANE',
                'EOD', 'START_TRAN', 'ADD_LINE_ITEM', 'FINISH_TRANSACTION')
        dlg = QDialog(self); dlg.setWindowTitle("Available Commands")
        lay = QVBoxLayout(dlg)
        txt = QTextEdit(); txt.setReadOnly(True)
        txt.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        txt.setPlainText('\n'.join(cmds))
        lay.addWidget(txt)
        lay.addWidget(QDialogButtonBox(QDialogButtonBox.Close, accepted=dlg.accept, rejected=dlg.reject))
        dlg.resize(300, 200); dlg.exec_()

    # ------------------------------------------- result handler
    def _handle_result(self, sent, received):
        self.sent_log.append(sent)
        self.recv_log.append(received)

        # update UI state on successful flows
        if '<COMMAND>REGISTER' in sent and '<EVENT>COMPLETED' in received:
            self.ktk_input.setEnabled(True); self.exchange_btn.setEnabled(True)
        if '<COMMAND>EXCHANGE_KEYS' in sent and '<MAC_KEY>' in received:
            if m := re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>', received):
                try:
                    self.mac_key = des3_decrypt(self.ktk, m.group(1))
                    self.mac_display.setText(self.mac_key)
                    self._save_mac()
                    for b in (self.status_btn, self.start_btn):
                        b.setEnabled(True)
                    self.get_status()
                except Exception as e:
                    QMessageBox.critical(self, "Decrypt Error", str(e))

    # ------------------------------------------- cleanup
    def closeEvent(self, e):
        for t in list(self.threads):
            t.quit(); t.wait()
        e.accept()


# ---------------------------------------------------------------------------
#  Entrypoint
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = VerifoneApp(); win.show()
    sys.exit(app.exec_())
