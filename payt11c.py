#!/usr/bin/env python3
# Verifone P400 connector – Quick‑Sale flow
# 2025‑05‑07  v1.32‑Q  (adds Tx‑type / Credit‑term / Currency selectors)

import sys, socket, os, re, random, html
from datetime import datetime, timezone
from base64   import b64decode, b64encode
from typing   import Dict, Optional, List, Tuple

from PyQt5.QtCore    import Qt, QThread, pyqtSignal, QDate
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QCheckBox,
    QGridLayout, QVBoxLayout, QHBoxLayout, QSplitter, QMessageBox,
    QDialogButtonBox, QAction
)
from PyQt5.QtGui     import QTextCursor, QTextCharFormat, QColor, QTextDocument
from Crypto.Cipher   import DES3
from Crypto.Hash     import SHA256


# ───────────────────────── helpers ────────────────────────────────────────
def rand_session() -> str:
    return ''.join(random.choice('0123456789') for _ in range(16))


def des3_decrypt(ktk: str, mac_b64: str) -> str:
    if len(ktk) != 16:
        raise ValueError("KTK must be exactly 16 ASCII characters.")
    payload = b64decode(mac_b64)
    key24   = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    return DES3.new(key24, DES3.MODE_ECB).decrypt(payload).rstrip(b'\0').decode()


def calc_mac(xml_wo: str, mac_key: str) -> str:
    """Return Base‑64 SHA‑256 MAC."""
    return b64encode(SHA256.new((xml_wo + mac_key).encode()).digest()).decode()


def to_minor(val: float) -> str:        # e.g. ₪10.00 → "1000"
    return str(int(round(val * 100)))


# ───────────────────────── threaded socket ────────────────────────────────
class SockThread(QThread):
    result = pyqtSignal(str, str)

    def __init__(self, ip: str, port: int, msg: str):
        super().__init__()
        self.ip, self.port, self.msg = ip, port, msg

    def run(self):
        sent, received = self.msg, ''
        try:
            with socket.create_connection((self.ip, self.port), timeout=7) as s:
                s.sendall(sent.encode())
                chunks: List[bytes] = []
                while True:
                    try:
                        ch = s.recv(4096)
                        if not ch:
                            break
                        chunks.append(ch)
                    except socket.timeout:
                        break
                received = b''.join(chunks).decode('utf‑8', 'replace')
        except Exception as e:
            received = f"Error: {e}"
        self.result.emit(sent, received)


# ───────────────────────── dialogs (unchanged) ────────────────────────────
class StartTransactionDlg(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start Transaction")
        g = QGridLayout(self)

        self.inv   = QLineEdit("100000")
        self.cash  = QLineEdit()
        self.shift = QSpinBox(minimum=0, maximum=9999, value=1)
        self.date  = QDateEdit(calendarPopup=True); self.date.setDate(QDate.currentDate())
        self.pos_ip   = QLineEdit()
        self.pos_port = QLineEdit()

        for row, (lbl, w) in enumerate((
            ("Invoice",      self.inv),
            ("Cashier ID",   self.cash),
            ("Shift ID",     self.shift),
            ("Business Date", self.date),
            ("POS IP",      self.pos_ip),
            ("POS Port",    self.pos_port)
        )):
            g.addWidget(QLabel(lbl), row, 0); g.addWidget(w, row, 1)

        g.addWidget(QLabel("* all fields optional"), 6, 0, 1, 2)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Start")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box, 7, 0, 1, 2)

    def data(self) -> Dict[str, str]:
        return {k: v for k, v in {
            'INVOICE'     : self.inv.text().strip(),
            'CASHIER_ID'  : self.cash.text().strip(),
            'SHIFT_ID'    : str(self.shift.value()) if self.shift.value() else '',
            'BUSINESSDATE': self.date.date().toString("yyyyMMdd"),
            'POS_IP'      : self.pos_ip.text().strip(),
            'POS_PORT'    : self.pos_port.text().strip()
        }.items() if v}


class DiscoverDlg(QDialog):
    """Same as before – unchanged, leaving out for brevity (no functional change)."""
    # … code identical to previous version …


# ───────────────────────── main window ────────────────────────────────────
class Main(QMainWindow):
    TX_TYPES   = ["01 Regular Charge", "02 Unload Prepaid", "03 Force Charge",
                  "06 Charge with CashBack", "07 Cash Withdrawal",
                  "11 Standing Order", "30 Balance inquiry", "53 Refund", "55 Load Prepaid"]

    CREDIT_TERMS = ["01 Regular Term", "02 Special Term (Adif / +30)",
                    "03 Debit", "06 Credit Installment", "08 Installment"]

    CURRENCIES = ["376 NIS", "840 USD", "978 EUR"]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Connector – Quick Sale")
        self.resize(1040, 840)

        # internal state
        self.session = self.ktk = self.mac_key = ''
        self.threads: List[QThread] = []
        self.qs_queue: List[Tuple[str, str, str]] = []

        # ───── UI build ────────────────────────────────────────────────────
        central = QWidget(); lay = QVBoxLayout(central)

        # connection row
        cr = QHBoxLayout()
        self.ip   = QLineEdit("192.168.1.202")
        self.port = QLineEdit("5015")
        cr.addWidget(QLabel("IP:"));   cr.addWidget(self.ip)
        cr.addWidget(QLabel("Port:")); cr.addWidget(self.port); cr.addStretch()
        lay.addLayout(cr)

        # register row
        rr = QHBoxLayout()
        self.chain = QLineEdit("39999"); self.store = QLineEdit("0123")
        self.lane  = QLineEdit("074");   self.alt   = QLineEdit("012345678")
        self.pos_type = QComboBox(); self.pos_type.addItems(["ATTENDED", "UNATTENDED"])
        for lbl, w in (("Chain", self.chain), ("Store", self.store),
                       ("Lane",  self.lane),  ("Alt‑ID", self.alt)):
            rr.addWidget(QLabel(lbl)); rr.addWidget(w)
        rr.addWidget(QLabel("POS Type")); rr.addWidget(self.pos_type)
        self.reg_btn = QPushButton("Register", clicked=self.cmd_register)
        rr.addWidget(self.reg_btn)
        lay.addLayout(rr)

        # key row
        kr = QHBoxLayout()
        kr.addWidget(QLabel("KTK (16)"))
        self.ktk_edit = QLineEdit(); self.ktk_edit.setEnabled(False); kr.addWidget(self.ktk_edit)
        self.key_btn  = QPushButton("Exchange Keys", clicked=self.cmd_keys); self.key_btn.setEnabled(False)
        kr.addWidget(self.key_btn); kr.addStretch(); lay.addLayout(kr)

        # MAC view
        mr = QHBoxLayout()
        mr.addWidget(QLabel("MAC Key"))
        self.mac_view = QLineEdit(); self.mac_view.setReadOnly(True); mr.addWidget(self.mac_view)
        mr.addStretch(); lay.addLayout(mr)

        # Quick‑Sale row  (new widgets here!)
        br = QHBoxLayout()
        self.start_btn = QPushButton("Start Tran…", clicked=self.do_start); self.start_btn.setEnabled(False)
        self.disc_btn  = QPushButton("Discover…",  clicked=self.do_discover); self.disc_btn.setEnabled(False)
        br.addWidget(self.start_btn); br.addWidget(self.disc_btn)

        # amount
        br.addWidget(QLabel("Amount ₪"))
        self.qs_amt = QDoubleSpinBox(decimals=2, maximum=999999, value=10.00)
        br.addWidget(self.qs_amt)

        # NEW: Tx‑type, Credit‑term, Currency combos
        br.addWidget(QLabel("Tx‑Type"))
        self.qs_tx = QComboBox(); self.qs_tx.addItems(self.TX_TYPES)
        br.addWidget(self.qs_tx)

        br.addWidget(QLabel("Credit Term"))
        self.qs_credit = QComboBox(); self.qs_credit.addItems(self.CREDIT_TERMS)
        br.addWidget(self.qs_credit)

        br.addWidget(QLabel("Currency"))
        self.qs_curr = QComboBox(); self.qs_curr.addItems(self.CURRENCIES)
        br.addWidget(self.qs_curr)

        self.quick_btn = QPushButton("Quick Sale", clicked=self.quick_sale); self.quick_btn.setEnabled(False)
        br.addWidget(self.quick_btn); br.addStretch()
        lay.addLayout(br)

        # admin row
        ar = QHBoxLayout()
        ar.addWidget(QLabel("Admin cmd"))
        self.cmd_in = QLineEdit()
        self.cmd_send = QPushButton("Send", clicked=self.admin_cmd)
        ar.addWidget(self.cmd_in); ar.addWidget(self.cmd_send); ar.addStretch()
        lay.addLayout(ar)

        self.status_btn = QPushButton("Status", clicked=self.cmd_status); self.status_btn.setEnabled(False)
        lay.addWidget(self.status_btn)

        # logs
        self.sent = QTextEdit(readOnly=True); self.recv = QTextEdit(readOnly=True)
        sp = QSplitter(Qt.Horizontal); sp.addWidget(self.sent); sp.addWidget(self.recv)
        lay.addWidget(sp)

        # search
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Search logs:"))
        self.search_edit = QLineEdit(); self.search_edit.returnPressed.connect(self.search_logs)
        self.search_btn  = QPushButton("Find", clicked=self.search_logs)
        srow.addWidget(self.search_edit); srow.addWidget(self.search_btn); srow.addStretch(); lay.addLayout(srow)

        act = QAction(self); act.setShortcut("Ctrl+F")
        act.triggered.connect(lambda: self.search_edit.setFocus(Qt.ShortcutFocusReason))
        self.addAction(act)

        lay.addWidget(QPushButton("Clear logs",
                                  clicked=lambda: (self.sent.clear(), self.recv.clear())))
        self.setCentralWidget(central)

        self._load_mac()

    # ───── persistence ────────────────────────────────────────────────────
    def _load_mac(self):
        if os.path.exists("mac.txt"):
            self.mac_key = open("mac.txt").read().strip()
            if self.mac_key:
                self.mac_view.setText(self.mac_key)
                for b in (self.status_btn, self.start_btn, self.disc_btn, self.quick_btn):
                    b.setEnabled(True)

    def _save_mac(self): open("mac.txt", "w").write(self.mac_key)

    # ───── XML helpers ────────────────────────────────────────────────────
    def _env(self, fg: str, cmd: str, body: str = "") -> str:
        ts = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        hdr   = (f"<TRANSACTION><FUNCTION_GROUP>{fg}</FUNCTION_GROUP>"
                 f"<COMMAND>{cmd}</COMMAND><SESSION_ID>{self.session}</SESSION_ID>"
                 f"<TRAINING_MODE>0</TRAINING_MODE><TRANSACTION_TIME>{ts}</TRANSACTION_TIME>")
        xml_wo = hdr + body + "<MAC></MAC></TRANSACTION>"
        mac    = calc_mac(xml_wo, self.mac_key) if self.mac_key else ''
        return hdr + body + f"<MAC>{mac}</MAC></TRANSACTION>"

    def _send(self, xml: str):
        try:
            port = int(self.port.text())
        except ValueError:
            QMessageBox.critical(self, "Port", "Invalid port value."); return
        th = SockThread(self.ip.text().strip(), port, xml)
        th.result.connect(self._handle); th.finished.connect(lambda: self.threads.remove(th))
        self.threads.append(th); th.start()

    def _disc_body(self, d: Dict[str, Optional[str]]) -> str:
        x = (f"<TRANSACTION_DETAILS>"
             f"<RESTRICT_TOKEN>{d['restrict_token']}</RESTRICT_TOKEN>"
             f"<MANUAL>{int(d['manual'])}</MANUAL>"
             f"<CTLS>{int(d['ctls'])}</CTLS>"
             f"<ALLOW_CANCEL>{int(d['allow_cancel'])}</ALLOW_CANCEL>"
             f"<UNATTENDED>{int(d['unattended'])}</UNATTENDED>"
             f"<TRAN_TYPE>{d['tran_type']}</TRAN_TYPE>"
             f"<MTI>100</MTI><ENTRY_MODE>04</ENTRY_MODE>"
             f"<TRANSACTION_AMOUNT>{d['amount']}</TRANSACTION_AMOUNT>"
             f"<ORIGINAL_CURRENCY>{d['currency']}</ORIGINAL_CURRENCY>")
        if d['manual'] and d['manual_reason']:
            x += f"<MANUAL_REASON>{d['manual_reason']}</MANUAL_REASON>"
        if d['cash'] is not None:
            x += f"<CASH_AMOUNT>{d['cash']}</CASH_AMOUNT>"
        if d['fx']:
            x += (f"<CONVERTED_AMOUNT>{d['fx_amt']}</CONVERTED_AMOUNT>"
                  f"<CONVERTED_CURRENCY>{d['fx_to']}</CONVERTED_CURRENCY>")
        if d['use_token'] and d['token_val']:
            x += f"<CARD_TOKEN>{d['token_val']}</CARD_TOKEN>"
        if d['service_type']:
            x += f"<SERVICE_TYPE>{d['service_type']}</SERVICE_TYPE>"
        if d.get('credit_term'):
            x += f"<CREDIT_TERMS>{d['credit_term']}</CREDIT_TERMS>"
        x += f"<OPERATION>{d['operation']}</OPERATION></TRANSACTION_DETAILS>"
        return f"<TIMEOUT>{d['timeout']}</TIMEOUT>" + x

    # ───── button slots ───────────────────────────────────────────────────
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
        if dlg.exec_() != QDialog.Accepted:
            return
        body = ''.join(f"<{k}>{v}</{k}>" for k, v in dlg.data().items())
        self._send(self._env("SESSION", "START_TRAN", body))

    def do_discover(self):
        dlg = DiscoverDlg(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        self._send(self._env("PAYMENT", "DISCOVERY", self._disc_body(dlg.data())))

    # ───── QUICK SALE  ────────────────────────────────────────────────────
    def quick_sale(self):
        amt_minor   = to_minor(self.qs_amt.value())
        tran_type   = self.qs_tx.currentText().split()[0]
        credit_term = self.qs_credit.currentText().split()[0]
        currency    = self.qs_curr.currentText().split()[0]

        # Start‑Transaction body (very small subset)
        start_body = (f"<INVOICE>100000</INVOICE>"
                      f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>")

        # default discovery / authorize dict
        defaults: Dict[str, Optional[str]] = {
            'timeout': '60',
            'restrict_token': '0',
            'manual': False,
            'manual_reason': '',
            'tran_type': tran_type,
            'amount': amt_minor,
            'currency': currency,
            'cash': None,
            'fx': False,
            'fx_to': '',
            'fx_amt': None,
            'use_token': False,
            'token_val': '',
            'service_type': '',
            'ctls': True,
            'allow_cancel': True,
            'unattended': False,
            'operation': '04',
            'credit_term': credit_term,
        }

        disc_body = self._disc_body(defaults)
        auth_body = self._disc_body(defaults)  # same details + CREDIT_TERMS

        # queue the four‑step quick‑sale
        self.qs_queue = [
            ("SESSION", "START_TRAN",  start_body),
            ("PAYMENT", "DISCOVERY",   disc_body),
            ("PAYMENT", "AUTHORIZE",   auth_body),
            ("SESSION", "FINISH_TRAN", ""),
        ]
        fg, cmd, body = self.qs_queue.pop(0)
        self._send(self._env(fg, cmd, body))

    # admin quick commands
    def admin_cmd(self):
        cmd = self.cmd_in.text().strip().upper()
        if cmd:
            self._send(self._env("ADMIN", cmd))

    # ───── search / highlight ─────────────────────────────────────────────
    def search_logs(self):
        term = self.search_edit.text()
        for pane in (self.sent, self.recv):
            self._highlight(pane, term)

    @staticmethod
    def _highlight(pane: QTextEdit, word: str):
        pane.setExtraSelections([])
        if not word:
            return
        sels, doc = [], pane.document(); cur = QTextCursor(doc)
        while True:
            cur = doc.find(word, cur)
            if cur.isNull():
                break
            sel = QTextEdit.ExtraSelection(); sel.cursor = cur
            fmt = QTextCharFormat(); fmt.setBackground(QColor("#ffff66"))
            sel.format = fmt; sels.append(sel)
        pane.setExtraSelections(sels)

    # ───── response handler & queue engine ────────────────────────────────
    def _handle(self, sent_xml: str, recv_xml: str):
        self.sent.append(sent_xml)
        self.recv.append(recv_xml)

        # quick‑sale engine
        if self.qs_queue:
            ok = ('<RESULT_CODE>0<' in recv_xml) or ('<EVENT>COMPLETED' in recv_xml)
            if ok:
                if ('<COMMAND>AUTHORIZE' in sent_xml and
                        '<RESULT_CODE>0<' in recv_xml and
                        (m := re.search(r'<RECEIPT_ARR>(.*?)</RECEIPT_ARR>', recv_xml, re.S))):
                    QMessageBox.information(self, "Receipt",
                                            html.unescape(m.group(1).strip()))
                if self.qs_queue:
                    fg, cmd, body = self.qs_queue.pop(0)
                    self._send(self._env(fg, cmd, body))
            else:
                self.qs_queue.clear()     # break the chain on first error

        # enable key‑exchange after register
        if '<COMMAND>REGISTER' in sent_xml and '<EVENT>COMPLETED' in recv_xml:
            self.ktk_edit.setEnabled(True); self.key_btn.setEnabled(True)

        # pull MAC‑key after exchange_keys
        if '<COMMAND>EXCHANGE_KEYS' in sent_xml and '<MAC_KEY>' in recv_xml:
            if (m := re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>', recv_xml)):
                try:
                    self.mac_key = des3_decrypt(self.ktk, m.group(1))
                    self.mac_view.setText(self.mac_key); self._save_mac()
                    for b in (self.status_btn, self.start_btn,
                              self.disc_btn, self.quick_btn):
                        b.setEnabled(True)
                except Exception as e:
                    QMessageBox.critical(self, "Decrypt error", str(e))

    # ───── clean shutdown ────────────────────────────────────────────────
    def closeEvent(self, ev):
        for t in self.threads:
            t.quit(); t.wait()
        ev.accept()


# ───────────────────────── run ───────────────────────────────────────────
if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    Main().show()
    sys.exit(app.exec_())
