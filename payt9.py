#!/usr/bin/env python3
#  Verifone P400 Connector – 2025‑05‑06 remaster
#  Compatible with API‑Spec 3.7.6 (P400 / MX‑series)

import sys, socket, os, re, random
from datetime import datetime, timezone
from base64 import b64decode, b64encode
from typing import Dict, Optional

from PyQt5.QtCore   import Qt, QThread, pyqtSignal, QDate
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLabel, QLineEdit,
    QComboBox, QPushButton, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit,
    QCheckBox, QGridLayout, QVBoxLayout, QHBoxLayout, QSplitter, QMessageBox,
    QDialogButtonBox
)
from Crypto.Cipher import DES3
from Crypto.Hash   import SHA256


# ───────────────────────── helpers ─────────────────────────
def rand_session() -> str:
    return ''.join(random.choice('0123456789') for _ in range(16))


def des3_decrypt(ktk: str, mac_key_b64: str) -> str:
    enc = b64decode(mac_key_b64)
    if len(ktk) != 16:
        raise ValueError("KTK must be exactly 16 ASCII chars")
    key24 = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    return DES3.new(key24, DES3.MODE_ECB).decrypt(enc).rstrip(b'\0').decode('utf‑8', 'ignore')


def calc_mac(xml_wo_mac: str, mac_key: str) -> str:
    h = SHA256.new((xml_wo_mac + mac_key).encode())
    return b64encode(h.digest()).decode()


def money_to_minor(val: float) -> str:
    """10.50 → 1050  (two decimals * 100)"""
    return str(int(round(val * 100)))


# ───────────────────────── socket thread ───────────────────
class SockThread(QThread):
    result = pyqtSignal(str, str)  # sent, recv

    def __init__(self, ip: str, port: int, msg: str):
        super().__init__()
        self.ip, self.port, self.msg = ip, port, msg

    def run(self):
        sent, recv = self.msg, ''
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(7)
                s.connect((self.ip, self.port))
                s.sendall(sent.encode())
                chunks = []
                while True:
                    try:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        chunks.append(chunk)
                    except socket.timeout:
                        break
                recv = b''.join(chunks).decode('utf‑8', 'replace')
        except Exception as e:
            recv = f"Error: {e}"
        self.result.emit(sent, recv)


# ───────────────────────── dialogs ─────────────────────────
class StartTransactionDlg(QDialog):
    """Invoice / cashier / shift – optional for SESSION‑>START_TRAN."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start Transaction")
        g = QGridLayout(self)

        self.inv  = QLineEdit()
        self.cash = QLineEdit()
        self.shift = QSpinBox(); self.shift.setRange(0, 9999); self.shift.setValue(1)
        self.date = QDateEdit(); self.date.setCalendarPopup(True); self.date.setDate(QDate.currentDate())
        self.posip = QLineEdit(); self.posport = QLineEdit()

        for r, (lbl, w) in enumerate((
            ("Merchant invoice number", self.inv),
            ("Cashier ID",              self.cash),
            ("Shift ID",                self.shift),
            ("Business Date",           self.date),
            ("POS IP",                  self.posip),
            ("POS Port",                self.posport),
        )):
            g.addWidget(QLabel(lbl), r, 0); g.addWidget(w, r, 1)

        g.addWidget(QLabel("* All fields optional"), 6, 0, 1, 2)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Start")
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        g.addWidget(btns, 7, 0, 1, 2)

    def data(self) -> Dict[str, str]:
        return {k: v for k, v in {
            'INVOICE':      self.inv.text().strip(),
            'CASHIER_ID':   self.cash.text().strip(),
            'SHIFT_ID':     str(self.shift.value()) if self.shift.value() else '',
            'BUSINESSDATE': self.date.date().toString("yyyyMMdd"),
            'POS_IP':       self.posip.text().strip(),
            'POS_PORT':     self.posport.text().strip()
        }.items() if v}


class DiscoverDlg(QDialog):
    """Full PAYMENT‑>DISCOVERY form."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Discover Credit Card")
        g = QGridLayout(self); r = 0

        # Timeout
        g.addWidget(QLabel("Time‑out (sec)"), r, 0)
        self.timeout = QSpinBox(minimum=5, maximum=999, value=60)
        g.addWidget(self.timeout, r, 1); r += 1

        # Manual flag
        self.manual = QCheckBox("Manual card entry"); g.addWidget(self.manual, r, 0)
        self.manual_reason = QComboBox(); self.manual_reason.addItems(["SIG", "CNP"]); self.manual_reason.setEnabled(False)
        g.addWidget(self.manual_reason, r, 1); r += 1
        self.manual.toggled.connect(self.manual_reason.setEnabled)

        # Transaction type
        g.addWidget(QLabel("Transaction type"), r, 0)
        self.tran_type = QComboBox()
        self.tran_type.addItems([
            "01 Regular Charge", "02 Unloading", "03 Forced",
            "06 Cashback", "30 Balance", "53 Refund", "55 Loading"
        ])
        g.addWidget(self.tran_type, r, 1); r += 1

        # Amount + currency
        g.addWidget(QLabel("Bill amount"), r, 0)
        self.amount = QDoubleSpinBox(decimals=2, maximum=999999, value=10.00)
        g.addWidget(self.amount, r, 1)
        g.addWidget(QLabel("Currency"), r, 2)
        self.currency = QComboBox(); self.currency.addItems(["376 NIS", "840 USD", "978 EUR"])
        g.addWidget(self.currency, r, 3); r += 1

        # Cash back (optional)
        self.cash_chk = QCheckBox("Cash amount?"); g.addWidget(self.cash_chk, r, 0)
        self.cash_amt = QDoubleSpinBox(decimals=2, maximum=999999); self.cash_amt.setEnabled(False)
        g.addWidget(self.cash_amt, r, 1); r += 1
        self.cash_chk.toggled.connect(self.cash_amt.setEnabled)

        # FX conversion (optional)
        self.fx_chk = QCheckBox("Convert currency?"); g.addWidget(self.fx_chk, r, 0)
        self.fx_to  = QComboBox(); self.fx_to.addItems(["376 NIS", "840 USD", "978 EUR"]); self.fx_to.setEnabled(False)
        self.fx_amt = QDoubleSpinBox(decimals=2, maximum=999999); self.fx_amt.setEnabled(False)
        g.addWidget(QLabel("Convert to"), r, 2); g.addWidget(self.fx_to, r, 3); r += 1
        g.addWidget(QLabel("Converted amount"), r, 2); g.addWidget(self.fx_amt, r, 3); r += 1
        self.fx_chk.toggled.connect(lambda b: [w.setEnabled(b) for w in (self.fx_to, self.fx_amt)])

        # Token
        g.addWidget(QLabel("Generate card token"), r, 0)
        self.gen_token = QComboBox(); self.gen_token.addItems(["0 None", "1 All cards", "2 Shufersal only"])
        g.addWidget(self.gen_token, r, 1); r += 1

        self.use_token_chk = QCheckBox("Use existing token"); g.addWidget(self.use_token_chk, r, 0)
        self.use_token_val = QLineEdit(); self.use_token_val.setEnabled(False); g.addWidget(self.use_token_val, r, 1); r += 1
        self.use_token_chk.toggled.connect(self.use_token_val.setEnabled)

        # Service type (rarely used)
        g.addWidget(QLabel("Service Type"), r, 0)
        self.service_type = QComboBox(); self.service_type.addItems(["", "1", "2", "3"])
        g.addWidget(self.service_type, r, 1); r += 1

        # Flags
        self.ctls_chk   = QCheckBox("Enable CTLS");        self.ctls_chk.setChecked(True)
        self.cancel_chk = QCheckBox("Allow cancel");       self.cancel_chk.setChecked(True)
        self.unatt_chk  = QCheckBox("Unattended POS")      # unchecked by default
        g.addWidget(self.ctls_chk,   r, 0)
        g.addWidget(self.cancel_chk, r, 1)
        g.addWidget(self.unatt_chk,  r, 2); r += 1

        # Operation
        g.addWidget(QLabel("Operation"), r, 0)
        self.operation = QComboBox(); self.operation.addItems([
            "03 Inquiry", "04 Execute transaction", "05 Authorize only", "06 Capture only"
        ])
        g.addWidget(self.operation, r, 1); r += 1

        # Buttons
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("Discover")
        btns.accepted.connect(self.accept); btns.rejected.connect(self.reject)
        g.addWidget(btns, r, 0, 1, 4)

    # ─── return cleaned data
    def data(self) -> Dict[str, Optional[str]]:
        return {
            'timeout':       str(self.timeout.value()),
            'manual':        self.manual.isChecked(),
            'manual_reason': self.manual_reason.currentText() if self.manual.isChecked() else '',
            'tran_type':     self.tran_type.currentText().split()[0],
            'amount':        money_to_minor(self.amount.value()),
            'currency':      self.currency.currentText().split()[0],
            'cash':          money_to_minor(self.cash_amt.value()) if self.cash_chk.isChecked() else None,
            'fx':            self.fx_chk.isChecked(),
            'fx_to':         self.fx_to.currentText().split()[0] if self.fx_chk.isChecked() else '',
            'fx_amt':        money_to_minor(self.fx_amt.value())  if self.fx_chk.isChecked() else None,
            'restrict_token': self.gen_token.currentText().split()[0],
            'use_token':     self.use_token_chk.isChecked(),
            'token_val':     self.use_token_val.text().strip(),
            'service_type':  self.service_type.currentText(),
            'ctls':          self.ctls_chk.isChecked(),
            'allow_cancel':  self.cancel_chk.isChecked(),
            'unattended':    self.unatt_chk.isChecked(),
            'operation':     self.operation.currentText().split()[0]
        }


# ───────────────────────── main window ─────────────────────
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Connector – remastered")
        self.resize(1000, 820)

        # ─── state
        self.session = ''
        self.ktk: str = ''
        self.mac_key: str = ''
        self.threads = []

        # ─── UI
        central = QWidget(); lay = QVBoxLayout(central)

        # connection row
        conn = QHBoxLayout()
        self.ip = QLineEdit("192.168.1.202"); self.port = QLineEdit("5015")
        conn.addWidget(QLabel("IP:")); conn.addWidget(self.ip)
        conn.addWidget(QLabel("Port:")); conn.addWidget(self.port); conn.addStretch()
        lay.addLayout(conn)

        # register row
        reg = QHBoxLayout()
        self.chain = QLineEdit("39999"); self.store = QLineEdit("0123"); self.lane = QLineEdit("074"); self.alt = QLineEdit("012345678")
        self.pos_type = QComboBox(); self.pos_type.addItems(["ATTENDED", "UNATTENDED"])
        for lbl, w in (("Chain", self.chain), ("Store", self.store), ("Lane", self.lane), ("Alt‑ID", self.alt)):
            reg.addWidget(QLabel(lbl)); reg.addWidget(w)
        reg.addWidget(QLabel("POS Type")); reg.addWidget(self.pos_type)
        self.reg_btn = QPushButton("Register"); self.reg_btn.clicked.connect(self.cmd_register)
        reg.addWidget(self.reg_btn)
        lay.addLayout(reg)

        # key row
        key = QHBoxLayout(); key.addWidget(QLabel("KTK (16)"))
        self.ktk_edit = QLineEdit(); self.ktk_edit.setEnabled(False); key.addWidget(self.ktk_edit)
        self.key_btn = QPushButton("Exchange Keys"); self.key_btn.setEnabled(False); self.key_btn.clicked.connect(self.cmd_keys)
        key.addWidget(self.key_btn); key.addStretch()
        lay.addLayout(key)

        # mac row
        mac_row = QHBoxLayout(); mac_row.addWidget(QLabel("MAC Key"))
        self.mac_show = QLineEdit(); self.mac_show.setReadOnly(True); mac_row.addWidget(self.mac_show); mac_row.addStretch()
        lay.addLayout(mac_row)

        # action row
        act = QHBoxLayout()
        self.start_btn = QPushButton("Start Tran …"); self.start_btn.setEnabled(False); self.start_btn.clicked.connect(self.do_start)
        self.disc_btn  = QPushButton("Discover …");   self.disc_btn.setEnabled(False);  self.disc_btn.clicked.connect(self.do_discover)
        act.addWidget(self.start_btn); act.addWidget(self.disc_btn)
        act.addWidget(QLabel("Command")); self.cmd_edit = QLineEdit()
        self.cmd_send = QPushButton("Send"); self.cmd_send.clicked.connect(self.custom_cmd)
        act.addWidget(self.cmd_edit); act.addWidget(self.cmd_send)
        self.help_btn = QPushButton("Help"); self.help_btn.clicked.connect(self.show_help); act.addWidget(self.help_btn); act.addStretch()
        lay.addLayout(act)

        self.status_btn = QPushButton("Status"); self.status_btn.setEnabled(False); self.status_btn.clicked.connect(self.cmd_status)
        lay.addWidget(self.status_btn)

        # logs
        self.sent = QTextEdit(); self.sent.setReadOnly(True)
        self.recv = QTextEdit(); self.recv.setReadOnly(True)
        split = QSplitter(Qt.Horizontal); split.addWidget(self.sent); split.addWidget(self.recv)
        lay.addWidget(split)

        clear = QPushButton("Clear logs"); clear.clicked.connect(lambda: (self.sent.clear(), self.recv.clear()))
        lay.addWidget(clear)

        self.setCentralWidget(central)
        self._load_mac()

    # ─── utilities
    def _load_mac(self):
        if os.path.exists("mac.txt"):
            self.mac_key = open("mac.txt").read().strip()
            if self.mac_key:
                self.mac_show.setText(self.mac_key)
                for b in (self.status_btn, self.start_btn, self.disc_btn):
                    b.setEnabled(True)

    def _save_mac(self):
        open("mac.txt", "w").write(self.mac_key)

    def _send(self, xml: str):
        try:
            port = int(self.port.text())
        except ValueError:
            QMessageBox.critical(self, "Port", "Enter valid port")
            return
        t = SockThread(self.ip.text().strip(), port, xml)
        t.result.connect(self._handle); t.finished.connect(lambda: self.threads.remove(t))
        self.threads.append(t); t.start()

    def _envelope(self, func: str, cmd: str, body: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        hdr = (f"<TRANSACTION><FUNCTION_GROUP>{func}</FUNCTION_GROUP><COMMAND>{cmd}</COMMAND>"
               f"<SESSION_ID>{self.session}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
               f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>")
        xml_wo_mac = hdr + body + "<MAC></MAC></TRANSACTION>"
        mac = calc_mac(xml_wo_mac, self.mac_key) if self.mac_key else ''
        return hdr + body + f"<MAC>{mac}</MAC></TRANSACTION>"

    # ─── API calls
    def cmd_register(self):
        self.session = rand_session()
        body = (f"<CHAIN>{self.chain.text()}</CHAIN><STORE>{self.store.text()}</STORE>"
                f"<LANE>{self.lane.text()}</LANE><TERMINAL_ID>{self.alt.text()}</TERMINAL_ID>"
                f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>")
        self._send(self._envelope("ADMIN", "REGISTER", body))

    def cmd_keys(self):
        self.ktk = self.ktk_edit.text().strip()
        self._send(self._envelope("ADMIN", "EXCHANGE_KEYS", f"<KTK>{self.ktk}</KTK>"))

    def cmd_status(self):
        self._send(self._envelope("ADMIN", "STATUS", ""))

    # start tran
    def do_start(self):
        dlg = StartTransactionDlg(self);  # modal
        if dlg.exec_() != QDialog.Accepted:
            return
        body = ''.join(f"<{k}>{v}</{k}>" for k, v in dlg.data().items())
        self._send(self._envelope("SESSION", "START_TRAN", body))

    # discover
    def do_discover(self):
        dlg = DiscoverDlg(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        d = dlg.data()
        if int(d['amount']) == 0:
            QMessageBox.warning(self, "Amount", "Bill amount cannot be 0")
            return

        tdet = (f"<TRANSACTION_DETAILS>"
                f"<RESTRICT_TOKEN>{d['restrict_token']}</RESTRICT_TOKEN>"
                f"<MANUAL>{int(d['manual'])}</MANUAL>"
                f"<CTLS>{int(d['ctls'])}</CTLS>"
                f"<ALLOW_CANCEL>{int(d['allow_cancel'])}</ALLOW_CANCEL>"
                f"<UNATTENDED>{int(d['unattended'])}</UNATTENDED>"
                f"<TRAN_TYPE>{d['tran_type']}</TRAN_TYPE>"
                f"<MTI>100</MTI><ENTRY_MODE>04</ENTRY_MODE>"
                f"<TRANSACTION_AMOUNT>{d['amount']}</TRANSACTION_AMOUNT>"
                f"<ORIGINAL_CURRENCY>{d['currency']}</ORIGINAL_CURRENCY>"
               )
        if d['manual'] and d['manual_reason']:
            tdet += f"<MANUAL_REASON>{d['manual_reason']}</MANUAL_REASON>"
        if d['cash'] is not None:
            tdet += f"<CASH_AMOUNT>{d['cash']}</CASH_AMOUNT>"
        if d['fx']:
            tdet += f"<CONVERTED_AMOUNT>{d['fx_amt']}</CONVERTED_AMOUNT><CONVERTED_CURRENCY>{d['fx_to']}</CONVERTED_CURRENCY>"
        if d['use_token'] and d['token_val']:
            tdet += f"<CARD_TOKEN>{d['token_val']}</CARD_TOKEN>"
        if d['service_type']:
            tdet += f"<SERVICE_TYPE>{d['service_type']}</SERVICE_TYPE>"
        tdet += f"<OPERATION>{d['operation']}</OPERATION></TRANSACTION_DETAILS>"

        body = f"<TIMEOUT>{d['timeout']}</TIMEOUT>" + tdet
        self._send(self._envelope("PAYMENT", "DISCOVERY", body))

    # custom
    def custom_cmd(self):
        cmd = self.cmd_edit.text().strip().upper()
        if not cmd:
            return
        self._send(self._envelope("ADMIN", cmd, ""))

    # result handler
    def _handle(self, sent: str, recv: str):
        self.sent.append(sent); self.recv.append(recv)
        if '<COMMAND>REGISTER' in sent and '<EVENT>COMPLETED' in recv:
            self.ktk_edit.setEnabled(True); self.key_btn.setEnabled(True)
        if '<COMMAND>EXCHANGE_KEYS' in sent and '<MAC_KEY>' in recv:
            m = re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>', recv)
            if m:
                try:
                    self.mac_key = des3_decrypt(self.ktk, m.group(1))
                    self.mac_show.setText(self.mac_key); self._save_mac()
                    for b in (self.status_btn, self.start_btn, self.disc_btn):
                        b.setEnabled(True)
                except Exception as e:
                    QMessageBox.critical(self, "Decrypt", str(e))

    def show_help(self):
        cmds = (
            "ADMIN : REGISTER, EXCHANGE_KEYS, STATUS, PING, OPEN_LANE, CLOSE_LANE, EOD …\n"
            "SESSION : START_TRAN, FINISH_TRAN …\n"
            "PAYMENT : DISCOVERY (this GUI), AUTHORIZE, CAPTURE …"
        )
        QMessageBox.information(self, "Command cheat‑sheet", cmds)

    def closeEvent(self, ev):
        for t in list(self.threads):
            t.quit(); t.wait()
        ev.accept()


# ───────────────────────── entrypoint ──────────────────────
if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    Main().show()
    sys.exit(app.exec_())
