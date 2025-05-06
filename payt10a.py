#!/usr/bin/env python3
# Verifone P400 Connector – remaster + Quick Sale (2025‑05‑06)
import sys, socket, os, re, random
from datetime import datetime, timezone
from base64 import b64decode, b64encode
from typing import Dict, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QDate
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLabel, QLineEdit,
    QComboBox, QPushButton, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit,
    QCheckBox, QGridLayout, QVBoxLayout, QHBoxLayout, QSplitter, QMessageBox,
    QDialogButtonBox
)
from Crypto.Cipher import DES3
from Crypto.Hash   import SHA256


# ───────────── helpers ─────────────
def rand_session() -> str:  # 16‑digit numeric
    return ''.join(random.choice('0123456789') for _ in range(16))


def des3_decrypt(ktk: str, mac_key_b64: str) -> str:
    if len(ktk) != 16:
        raise ValueError("KTK must be 16 ASCII chars")
    enc = b64decode(mac_key_b64)
    key24 = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    return DES3.new(key24, DES3.MODE_ECB).decrypt(enc).rstrip(b'\0').decode('utf‑8', 'ignore')


def calc_mac(xml_wo_mac: str, mac_key: str) -> str:
    return b64encode(SHA256.new((xml_wo_mac + mac_key).encode()).digest()).decode()


def money_minor(val: float) -> str:  # 10.50 → 1050
    return str(int(round(val * 100)))


# ───────────── socket helper ─────────────
class SockThread(QThread):
    result = pyqtSignal(str, str)  # sent, received

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
                data = []
                while True:
                    try:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        data.append(chunk)
                    except socket.timeout:
                        break
                recv = b''.join(data).decode('utf‑8', 'replace')
        except Exception as e:
            recv = f"Error: {e}"
        self.result.emit(sent, recv)


# ───────────── dialogs (unchanged) ─────────────
class StartTransactionDlg(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start Transaction")
        g = QGridLayout(self)
        self.inv = QLineEdit("100000")
        self.cash = QLineEdit()
        self.shift = QSpinBox(); self.shift.setRange(0, 9999); self.shift.setValue(1)
        self.date = QDateEdit(); self.date.setCalendarPopup(True); self.date.setDate(QDate.currentDate())
        self.posip = QLineEdit(); self.posport = QLineEdit()
        for r, (lbl, w) in enumerate((
            ("Invoice", self.inv), ("Cashier ID", self.cash), ("Shift ID", self.shift),
            ("Business Date", self.date), ("POS IP", self.posip), ("POS Port", self.posport)
        )):
            g.addWidget(QLabel(lbl), r, 0); g.addWidget(w, r, 1)
        g.addWidget(QLabel("* optional"), 6, 0, 1, 2)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Start")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box, 7, 0, 1, 2)

    def data(self) -> Dict[str, str]:
        return {k: v for k, v in {
            'INVOICE': self.inv.text().strip(),
            'CASHIER_ID': self.cash.text().strip(),
            'SHIFT_ID': str(self.shift.value()) if self.shift.value() else '',
            'BUSINESSDATE': self.date.date().toString("yyyyMMdd"),
            'POS_IP': self.posip.text().strip(),
            'POS_PORT': self.posport.text().strip()
        }.items() if v}


class DiscoverDlg(QDialog):
    """unchanged – full UI for manual Discover (see previous code)"""
    # ...  keep your existing DiscoverDlg definition here ...


# ───────────── main window ─────────────
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Connector (remaster + Quick Sale)")
        self.resize(1000, 820)

        # state
        self.session = ''
        self.ktk = ''
        self.mac_key = ''
        self.threads = []

        # UI build
        central = QWidget(); lay = QVBoxLayout(central)

        # connection
        conn = QHBoxLayout()
        self.ip = QLineEdit("192.168.1.202"); self.port = QLineEdit("5015")
        conn.addWidget(QLabel("IP:")); conn.addWidget(self.ip)
        conn.addWidget(QLabel("Port:")); conn.addWidget(self.port); conn.addStretch()
        lay.addLayout(conn)

        # register
        reg = QHBoxLayout()
        self.chain = QLineEdit("39999"); self.store = QLineEdit("0123")
        self.lane = QLineEdit("074"); self.alt = QLineEdit("012345678")
        self.pos_type = QComboBox(); self.pos_type.addItems(["ATTENDED", "UNATTENDED"])
        for lbl, w in (("Chain", self.chain), ("Store", self.store), ("Lane", self.lane), ("Alt‑ID", self.alt)):
            reg.addWidget(QLabel(lbl)); reg.addWidget(w)
        reg.addWidget(QLabel("POS Type")); reg.addWidget(self.pos_type)
        self.reg_btn = QPushButton("Register"); self.reg_btn.clicked.connect(self.cmd_register)
        reg.addWidget(self.reg_btn); lay.addLayout(reg)

        # keys
        key = QHBoxLayout(); key.addWidget(QLabel("KTK (16)"))
        self.ktk_edit = QLineEdit(); self.ktk_edit.setEnabled(False); key.addWidget(self.ktk_edit)
        self.key_btn = QPushButton("Exchange Keys"); self.key_btn.setEnabled(False); self.key_btn.clicked.connect(self.cmd_keys)
        key.addWidget(self.key_btn); key.addStretch(); lay.addLayout(key)

        # mac
        mac_row = QHBoxLayout(); mac_row.addWidget(QLabel("MAC Key"))
        self.mac_show = QLineEdit(); self.mac_show.setReadOnly(True); mac_row.addWidget(self.mac_show); mac_row.addStretch()
        lay.addLayout(mac_row)

        # action buttons
        act = QHBoxLayout()
        self.start_btn = QPushButton("Start Tran …"); self.start_btn.setEnabled(False); self.start_btn.clicked.connect(self.do_start)
        self.disc_btn  = QPushButton("Discover …");   self.disc_btn.setEnabled(False); self.disc_btn.clicked.connect(self.do_discover)
        self.quick_btn = QPushButton("Quick Sale");   self.quick_btn.setEnabled(False); self.quick_btn.clicked.connect(self.quick_sale)
        for b in (self.start_btn, self.disc_btn, self.quick_btn): act.addWidget(b)
        act.addWidget(QLabel("Command")); self.cmd_edit = QLineEdit()
        self.cmd_send = QPushButton("Send"); self.cmd_send.clicked.connect(self.custom_cmd)
        act.addWidget(self.cmd_edit); act.addWidget(self.cmd_send); act.addStretch()
        lay.addLayout(act)

        self.status_btn = QPushButton("Status"); self.status_btn.setEnabled(False); self.status_btn.clicked.connect(self.cmd_status)
        lay.addWidget(self.status_btn)

        # logs
        self.sent = QTextEdit(); self.sent.setReadOnly(True)
        self.recv = QTextEdit(); self.recv.setReadOnly(True)
        splitter = QSplitter(Qt.Horizontal); splitter.addWidget(self.sent); splitter.addWidget(self.recv)
        lay.addWidget(splitter)
        lay.addWidget(QPushButton("Clear logs", clicked=lambda: (self.sent.clear(), self.recv.clear())))
        self.setCentralWidget(central)
        self._load_mac()

    # ─── persistence
    def _load_mac(self):
        if os.path.exists("mac.txt"):
            self.mac_key = open("mac.txt").read().strip()
            if self.mac_key:
                self.mac_show.setText(self.mac_key)
                for b in (self.status_btn, self.start_btn, self.disc_btn, self.quick_btn):
                    b.setEnabled(True)

    def _save_mac(self): open("mac.txt", "w").write(self.mac_key)

    # ─── network helpers
    def _send(self, xml: str):
        try: port = int(self.port.text())
        except ValueError:
            QMessageBox.critical(self, "Port", "Invalid port"); return
        t = SockThread(self.ip.text().strip(), port, xml)
        t.result.connect(self._handle); t.finished.connect(lambda: self.threads.remove(t))
        self.threads.append(t); t.start()

    def _env(self, func: str, cmd: str, body: str = "") -> str:
        ts = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        hdr = (f"<TRANSACTION><FUNCTION_GROUP>{func}</FUNCTION_GROUP><COMMAND>{cmd}</COMMAND>"
               f"<SESSION_ID>{self.session}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
               f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>")
        xml_wo_mac = hdr + body + "<MAC></MAC></TRANSACTION>"
        return hdr + body + f"<MAC>{calc_mac(xml_wo_mac, self.mac_key)}</MAC></TRANSACTION>"

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
        d = dlg.data()
        self._send(self._env("PAYMENT", "DISCOVERY", self._build_discovery_body(d)))

    # Quick Sale  (defaults)
    def quick_sale(self):
        start_body = f"<INVOICE>100000</INVOICE><POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>"
        disc_body  = self._build_discovery_body({
            'timeout':'60','restrict_token':'0','manual':False,'manual_reason':'',
            'tran_type':'01','amount':'1000','currency':'376','cash':None,
            'fx':False,'fx_to':'','fx_amt':None,'use_token':False,'token_val':'',
            'service_type':'','ctls':True,'allow_cancel':True,'unattended':False,
            'operation':'04'
        })
        self._send(self._env("SESSION", "START_TRAN", start_body))
        self._send(self._env("PAYMENT", "DISCOVERY", disc_body))

    # helper to construct TRANSACTION_DETAILS
    def _build_discovery_body(self, d: Dict[str, Optional[str]]) -> str:
        tdet = (f"<TRANSACTION_DETAILS>"
                f"<RESTRICT_TOKEN>{d['restrict_token']}</RESTRICT_TOKEN>"
                f"<MANUAL>{int(d['manual'])}</MANUAL>"
                f"<CTLS>{int(d['ctls'])}</CTLS>"
                f"<ALLOW_CANCEL>{int(d['allow_cancel'])}</ALLOW_CANCEL>"
                f"<UNATTENDED>{int(d['unattended'])}</UNATTENDED>"
                f"<TRAN_TYPE>{d['tran_type']}</TRAN_TYPE>"
                f"<MTI>100</MTI><ENTRY_MODE>04</ENTRY_MODE>"
                f"<TRANSACTION_AMOUNT>{d['amount']}</TRANSACTION_AMOUNT>"
                f"<ORIGINAL_CURRENCY>{d['currency']}</ORIGINAL_CURRENCY>")
        if d.get('manual') and d.get('manual_reason'):
            tdet += f"<MANUAL_REASON>{d['manual_reason']}</MANUAL_REASON>"
        if d.get('cash') is not None:
            tdet += f"<CASH_AMOUNT>{d['cash']}</CASH_AMOUNT>"
        if d.get('fx'):
            tdet += (f"<CONVERTED_AMOUNT>{d['fx_amt']}</CONVERTED_AMOUNT>"
                     f"<CONVERTED_CURRENCY>{d['fx_to']}</CONVERTED_CURRENCY>")
        if d.get('use_token') and d.get('token_val'):
            tdet += f"<CARD_TOKEN>{d['token_val']}</CARD_TOKEN>"
        if d.get('service_type'):
            tdet += f"<SERVICE_TYPE>{d['service_type']}</SERVICE_TYPE>"
        tdet += f"<OPERATION>{d['operation']}</OPERATION></TRANSACTION_DETAILS>"
        return f"<TIMEOUT>{d['timeout']}</TIMEOUT>" + tdet

    # custom admin
    def custom_cmd(self):
        cmd = self.cmd_edit.text().strip().upper()
        if cmd: self._send(self._env("ADMIN", cmd))

    # socket result
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
                    for b in (self.status_btn, self.start_btn, self.disc_btn, self.quick_btn):
                        b.setEnabled(True)
                except Exception as e:
                    QMessageBox.critical(self, "Decrypt", str(e))

    def closeEvent(self, e):
        for t in list(self.threads): t.quit(); t.wait()
        e.accept()


# ───── entrypoint ─────
if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    Main().show()
    sys.exit(app.exec_())
