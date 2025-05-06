#!/usr/bin/env python3
# Verifone P400 Connector – complete build (2025‑05‑06)

import sys, socket, os, re, random
from datetime import datetime, timezone
from base64 import b64decode, b64encode
from typing  import Dict, Optional

from PyQt5.QtCore    import Qt, QThread, pyqtSignal, QDate
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


def des3_decrypt(ktk: str, mac_b64: str) -> str:
    if len(ktk) != 16:
        raise ValueError("KTK must be 16 ASCII characters")
    enc = b64decode(mac_b64)
    key24 = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    return DES3.new(key24, DES3.MODE_ECB).decrypt(enc).rstrip(b'\0').decode()


def calc_mac(xml_wo_mac: str, mac_key: str) -> str:
    h = SHA256.new((xml_wo_mac + mac_key).encode())
    return b64encode(h.digest()).decode()


def minor(val: float) -> str:
    """10.00 → 1000"""
    return str(int(round(val * 100)))


# ───────────────────────── threaded sender ─────────────────────────
class SockThread(QThread):
    result = pyqtSignal(str, str)            # sent, received

    def __init__(self, ip: str, port: int, msg: str):
        super().__init__()
        self.ip, self.port, self.msg = ip, port, msg

    def run(self):
        sent, recv = self.msg, ''
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(6)
                s.connect((self.ip, self.port))
                s.sendall(sent.encode())
                buff = []
                while True:
                    try:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        buff.append(chunk)
                    except socket.timeout:
                        break
                recv = b''.join(buff).decode('utf-8', 'replace')
        except Exception as e:
            recv = f"Error: {e}"
        self.result.emit(sent, recv)


# ───────────────────────── dialogs ─────────────────────────
class StartTransactionDlg(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start Transaction")
        g = QGridLayout(self)

        self.inv   = QLineEdit("100000")
        self.cash  = QLineEdit()
        self.shift = QSpinBox(); self.shift.setRange(0, 9999); self.shift.setValue(1)
        self.date  = QDateEdit(); self.date.setCalendarPopup(True); self.date.setDate(QDate.currentDate())
        self.posip = QLineEdit(); self.posport = QLineEdit()

        for r, (lbl, w) in enumerate((
            ("Invoice", self.inv), ("Cashier ID", self.cash), ("Shift ID", self.shift),
            ("Business Date", self.date), ("POS IP", self.posip), ("POS Port", self.posport)
        )):
            g.addWidget(QLabel(lbl), r, 0); g.addWidget(w, r, 1)

        g.addWidget(QLabel("* all optional"), 6, 0, 1, 2)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Start")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box, 7, 0, 1, 2)

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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Discover – Read Card")
        g = QGridLayout(self); r = 0

        g.addWidget(QLabel("Timeout (sec)"), r, 0)
        self.timeout = QSpinBox(minimum=5, maximum=999, value=60)
        g.addWidget(self.timeout, r, 1); r += 1

        self.manual = QCheckBox("Manual card entry")
        g.addWidget(self.manual, r, 0)
        self.man_reason = QComboBox(); self.man_reason.addItems(["SIG", "CNP"])
        self.man_reason.setEnabled(False)
        g.addWidget(self.man_reason, r, 1); r += 1
        self.manual.toggled.connect(self.man_reason.setEnabled)

        g.addWidget(QLabel("Transaction type"), r, 0)
        self.tran_type = QComboBox()
        self.tran_type.addItems(["01 Regular Charge", "02 Unloading", "03 Forced",
                                 "06 Cashback", "30 Balance", "53 Refund", "55 Loading"])
        g.addWidget(self.tran_type, r, 1); r += 1

        g.addWidget(QLabel("Bill amount"), r, 0)
        self.amount = QDoubleSpinBox(decimals=2, maximum=999999, value=10.00)
        g.addWidget(self.amount, r, 1)
        g.addWidget(QLabel("Currency"), r, 2)
        self.currency = QComboBox(); self.currency.addItems(["376 NIS", "840 USD", "978 EUR"])
        g.addWidget(self.currency, r, 3); r += 1

        self.cash_chk = QCheckBox("Cash amount?")
        g.addWidget(self.cash_chk, r, 0)
        self.cash_amt = QDoubleSpinBox(decimals=2, maximum=999999); self.cash_amt.setEnabled(False)
        g.addWidget(self.cash_amt, r, 1); r += 1
        self.cash_chk.toggled.connect(self.cash_amt.setEnabled)

        self.fx_chk = QCheckBox("Convert currency?")
        g.addWidget(self.fx_chk, r, 0)
        self.fx_to  = QComboBox(); self.fx_to.addItems(["376 NIS", "840 USD", "978 EUR"]); self.fx_to.setEnabled(False)
        self.fx_amt = QDoubleSpinBox(decimals=2, maximum=999999); self.fx_amt.setEnabled(False)
        g.addWidget(QLabel("Convert to"), r, 2); g.addWidget(self.fx_to, r, 3); r += 1
        g.addWidget(QLabel("Converted amount"), r, 2); g.addWidget(self.fx_amt, r, 3); r += 1
        self.fx_chk.toggled.connect(lambda b: [w.setEnabled(b) for w in (self.fx_to, self.fx_amt)])

        g.addWidget(QLabel("Generate card token"), r, 0)
        self.gen_token = QComboBox(); self.gen_token.addItems(["0 None", "1 All cards", "2 Shufersal only"])
        g.addWidget(self.gen_token, r, 1); r += 1

        self.use_tok = QCheckBox("Use existing token")
        g.addWidget(self.use_tok, r, 0)
        self.tok_val = QLineEdit(); self.tok_val.setEnabled(False); g.addWidget(self.tok_val, r, 1); r += 1
        self.use_tok.toggled.connect(self.tok_val.setEnabled)

        g.addWidget(QLabel("Service type"), r, 0)
        self.service = QComboBox(); self.service.addItems(["", "1", "2", "3"])
        g.addWidget(self.service, r, 1); r += 1

        self.ctls = QCheckBox("Enable CTLS"); self.ctls.setChecked(True)
        self.allow = QCheckBox("Allow cancel"); self.allow.setChecked(True)
        self.unatt = QCheckBox("Unattended POS")
        g.addWidget(self.ctls, r, 0); g.addWidget(self.allow, r, 1); g.addWidget(self.unatt, r, 2); r += 1

        g.addWidget(QLabel("Operation"), r, 0)
        self.op = QComboBox(); self.op.addItems(["03 Inquiry", "04 Execute transaction",
                                                 "05 Authorize only", "06 Capture only"])
        g.addWidget(self.op, r, 1); r += 1

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Discover")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box, r, 0, 1, 4)

    def data(self) -> Dict[str, Optional[str]]:
        return {
            'timeout': str(self.timeout.value()),
            'restrict_token': self.gen_token.currentText().split()[0],
            'manual': self.manual.isChecked(),
            'manual_reason': self.man_reason.currentText() if self.manual.isChecked() else '',
            'tran_type': self.tran_type.currentText().split()[0],
            'amount': minor(self.amount.value()),
            'currency': self.currency.currentText().split()[0],
            'cash': minor(self.cash_amt.value()) if self.cash_chk.isChecked() else None,
            'fx': self.fx_chk.isChecked(),
            'fx_to': self.fx_to.currentText().split()[0] if self.fx_chk.isChecked() else '',
            'fx_amt': minor(self.fx_amt.value()) if self.fx_chk.isChecked() else None,
            'use_token': self.use_tok.isChecked(),
            'token_val': self.tok_val.text().strip(),
            'service_type': self.service.currentText(),
            'ctls': self.ctls.isChecked(),
            'allow_cancel': self.allow.isChecked(),
            'unattended': self.unatt.isChecked(),
            'operation': self.op.currentText().split()[0]
        }


# ───────────────────────── main window ─────────────────────────
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Connector – full build")
        self.resize(1000, 820)

        # state
        self.session   = ''
        self.ktk       = ''
        self.mac_key   = ''
        self.threads   = []
        self.disc_wait = None          # queued DISCOVERY body after Quick‑Sale

        # ── UI skeleton
        central = QWidget(); lay = QVBoxLayout(central)

        # connection
        conn = QHBoxLayout()
        self.ip = QLineEdit("192.168.1.202"); self.port = QLineEdit("5015")
        conn.addWidget(QLabel("IP:")); conn.addWidget(self.ip)
        conn.addWidget(QLabel("Port:")); conn.addWidget(self.port); conn.addStretch(); lay.addLayout(conn)

        # register
        reg = QHBoxLayout()
        self.chain = QLineEdit("39999"); self.store = QLineEdit("0123")
        self.lane  = QLineEdit("074");   self.alt   = QLineEdit("012345678")
        self.pos_type = QComboBox(); self.pos_type.addItems(["ATTENDED", "UNATTENDED"])
        for lbl, w in (("Chain", self.chain), ("Store", self.store), ("Lane", self.lane), ("Alt‑ID", self.alt)):
            reg.addWidget(QLabel(lbl)); reg.addWidget(w)
        reg.addWidget(QLabel("POS Type")); reg.addWidget(self.pos_type)
        self.reg_btn = QPushButton("Register", clicked=self.cmd_register)
        reg.addWidget(self.reg_btn); lay.addLayout(reg)

        # keys
        key = QHBoxLayout(); key.addWidget(QLabel("KTK (16)"))
        self.ktk_edit = QLineEdit(); self.ktk_edit.setEnabled(False); key.addWidget(self.ktk_edit)
        self.key_btn = QPushButton("Exchange Keys", clicked=self.cmd_keys); self.key_btn.setEnabled(False)
        key.addWidget(self.key_btn); key.addStretch(); lay.addLayout(key)

        # mac
        mac = QHBoxLayout(); mac.addWidget(QLabel("MAC Key"))
        self.mac_view = QLineEdit(); self.mac_view.setReadOnly(True); mac.addWidget(self.mac_view); mac.addStretch(); lay.addLayout(mac)

        # action buttons
        act = QHBoxLayout()
        self.start_btn  = QPushButton("Start Tran …", clicked=self.do_start);  self.start_btn.setEnabled(False)
        self.disc_btn   = QPushButton("Discover …",  clicked=self.do_discover); self.disc_btn.setEnabled(False)
        self.quick_btn  = QPushButton("Quick Sale",  clicked=self.quick_sale);  self.quick_btn.setEnabled(False)
        for b in (self.start_btn, self.disc_btn, self.quick_btn): act.addWidget(b)
        act.addWidget(QLabel("Admin cmd")); self.cmd_inp = QLineEdit()
        self.cmd_send = QPushButton("Send", clicked=self.admin_cmd)
        act.addWidget(self.cmd_inp); act.addWidget(self.cmd_send); act.addStretch(); lay.addLayout(act)

        self.status_btn = QPushButton("Status", clicked=self.cmd_status); self.status_btn.setEnabled(False); lay.addWidget(self.status_btn)

        # logs
        self.sent = QTextEdit(); self.sent.setReadOnly(True)
        self.recv = QTextEdit(); self.recv.setReadOnly(True)
        spl = QSplitter(Qt.Horizontal); spl.addWidget(self.sent); spl.addWidget(self.recv); lay.addWidget(spl)

        lay.addWidget(QPushButton("Clear logs", clicked=lambda: (self.sent.clear(), self.recv.clear())))
        self.setCentralWidget(central)
        self._load_mac()

    # ─── persistence & env
    def _load_mac(self):
        if os.path.exists("mac.txt"):
            self.mac_key = open("mac.txt").read().strip()
            if self.mac_key:
                self.mac_view.setText(self.mac_key)
                for b in (self.status_btn, self.start_btn, self.disc_btn, self.quick_btn):
                    b.setEnabled(True)

    def _save_mac(self): open("mac.txt", "w").write(self.mac_key)

    def _send(self, xml: str):
        try: port = int(self.port.text())
        except ValueError: QMessageBox.critical(self, "Port", "Invalid port value"); return
        t = SockThread(self.ip.text().strip(), port, xml)
        t.result.connect(self._handle); t.finished.connect(lambda: self.threads.remove(t))
        self.threads.append(t); t.start()

    def _env(self, fg: str, cmd: str, body: str = "") -> str:
        ts = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        hdr = (f"<TRANSACTION><FUNCTION_GROUP>{fg}</FUNCTION_GROUP><COMMAND>{cmd}</COMMAND>"
               f"<SESSION_ID>{self.session}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
               f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>")
        xml_wo = hdr + body + "<MAC></MAC></TRANSACTION>"
        return hdr + body + f"<MAC>{calc_mac(xml_wo, self.mac_key)}</MAC></TRANSACTION>"

    # ─── API flows
    def cmd_register(self):
        self.session = rand_session()
        body = (f"<CHAIN>{self.chain.text()}</CHAIN><STORE>{self.store.text()}</STORE>"
                f"<LANE>{self.lane.text()}</LANE><TERMINAL_ID>{self.alt.text()}</TERMINAL_ID>"
                f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>")
        self._send(self._env("ADMIN", "REGISTER", body))

    def cmd_keys(self):
        self.ktk = self.ktk_edit.text().strip()
        self._send(self._env("ADMIN", "EXCHANGE_KEYS", f"<KTK>{self.ktk}</KTK>"))

    def cmd_status(self): self._send(self._env("ADMIN", "STATUS"))

    def do_start(self):
        dlg = StartTransactionDlg(self)
        if dlg.exec_() != QDialog.Accepted: return
        body = ''.join(f"<{k}>{v}</{k}>" for k, v in dlg.data().items())
        self._send(self._env("SESSION", "START_TRAN", body))

    def do_discover(self):
        dlg = DiscoverDlg(self)
        if dlg.exec_() != QDialog.Accepted: return
        self._send(self._env("PAYMENT", "DISCOVERY", self._disc_body(dlg.data())))

    # Quick‑Sale (sequential)
    def quick_sale(self):
        start_body = f"<INVOICE>100000</INVOICE><POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>"
        self.disc_wait = self._disc_body({
            'timeout':'60','restrict_token':'0','manual':False,'manual_reason':'',
            'tran_type':'01','amount':'1000','currency':'376','cash':None,
            'fx':False,'fx_to':'','fx_amt':None,'use_token':False,'token_val':'',
            'service_type':'','ctls':True,'allow_cancel':True,'unattended':False,
            'operation':'04'
        })
        self._send(self._env("SESSION", "START_TRAN", start_body))

    # build DISCOVERY body
    def _disc_body(self, d: Dict[str, Optional[str]]) -> str:
        t = (f"<TRANSACTION_DETAILS>"
             f"<RESTRICT_TOKEN>{d['restrict_token']}</RESTRICT_TOKEN>"
             f"<MANUAL>{int(d['manual'])}</MANUAL>"
             f"<CTLS>{int(d['ctls'])}</CTLS>"
             f"<ALLOW_CANCEL>{int(d['allow_cancel'])}</ALLOW_CANCEL>"
             f"<UNATTENDED>{int(d['unattended'])}</UNATTENDED>"
             f"<TRAN_TYPE>{d['tran_type']}</TRAN_TYPE>"
             f"<MTI>100</MTI><ENTRY_MODE>04</ENTRY_MODE>"
             f"<TRANSACTION_AMOUNT>{d['amount']}</TRANSACTION_AMOUNT>"
             f"<ORIGINAL_CURRENCY>{d['currency']}</ORIGINAL_CURRENCY>")
        if d['manual'] and d['manual_reason']: t += f"<MANUAL_REASON>{d['manual_reason']}</MANUAL_REASON>"
        if d['cash'] is not None: t += f"<CASH_AMOUNT>{d['cash']}</CASH_AMOUNT>"
        if d['fx']:
            t += f"<CONVERTED_AMOUNT>{d['fx_amt']}</CONVERTED_AMOUNT><CONVERTED_CURRENCY>{d['fx_to']}</CONVERTED_CURRENCY>"
        if d['use_token'] and d['token_val']: t += f"<CARD_TOKEN>{d['token_val']}</CARD_TOKEN>"
        if d['service_type']: t += f"<SERVICE_TYPE>{d['service_type']}</SERVICE_TYPE>"
        t += f"<OPERATION>{d['operation']}</OPERATION></TRANSACTION_DETAILS>"
        return f"<TIMEOUT>{d['timeout']}</TIMEOUT>" + t

    def admin_cmd(self):
        cmd = self.cmd_inp.text().strip().upper()
        if cmd: self._send(self._env("ADMIN", cmd))

    # results
    def _handle(self, sent: str, recv: str):
        self.sent.append(sent); self.recv.append(recv)

        # Sequential Quick‑Sale : send Discover when Start‑Tran completes
        if self.disc_wait and '<COMMAND>START_TRAN' in sent and '<EVENT>COMPLETED' in recv:
            self._send(self._env("PAYMENT", "DISCOVERY", self.disc_wait))
            self.disc_wait = None

        if '<COMMAND>REGISTER' in sent and '<EVENT>COMPLETED' in recv:
            self.ktk_edit.setEnabled(True); self.key_btn.setEnabled(True)
        if '<COMMAND>EXCHANGE_KEYS' in sent and '<MAC_KEY>' in recv:
            if m := re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>', recv):
                try:
                    self.mac_key = des3_decrypt(self.ktk, m.group(1))
                    self.mac_view.setText(self.mac_key); self._save_mac()
                    for b in (self.status_btn, self.start_btn, self.disc_btn, self.quick_btn):
                        b.setEnabled(True)
                except Exception as e:
                    QMessageBox.critical(self, "Decrypt error", str(e))

    def closeEvent(self, ev):
        for t in list(self.threads): t.quit(); t.wait()
        ev.accept()


# ───────────────────────── run ─────────────────────────
if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    Main().show()
    sys.exit(app.exec_())
