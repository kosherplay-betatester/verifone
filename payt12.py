#!/usr/bin/env python3
# Verifone P400 connector – Quick‑Sale flow
# 2025‑05‑07  v1.32‑A  (+ credit‑term wizard, dynamic AUTHORIZE)

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
from PyQt5.QtGui     import QTextCursor, QTextCharFormat, QColor
from Crypto.Cipher   import DES3
from Crypto.Hash     import SHA256

# ───────────────────────── helpers ─────────────────────────────────────────
def rand_session() -> str:
    return ''.join(random.choice('0123456789') for _ in range(16))

def des3_decrypt(ktk: str, mac_b64: str) -> str:
    if len(ktk) != 16:
        raise ValueError("KTK must be exactly 16 ASCII characters.")
    data  = b64decode(mac_b64)
    key24 = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    return DES3.new(key24, DES3.MODE_ECB).decrypt(data).rstrip(b'\0').decode()

def calc_mac(xml_wo: str, mac_key: str) -> str:        # SHA‑256 + Base64
    return b64encode(SHA256.new((xml_wo + mac_key).encode()).digest()).decode()

def to_minor(val: float) -> str:                       # ₪10.00 → "1000"
    return str(int(round(val * 100)))

# ───────────────────── threaded TCP sender ────────────────────────────────
class SockThread(QThread):
    result = pyqtSignal(str, str)
    def __init__(self, ip: str, port: int, msg: str):
        super().__init__(); self.ip, self.port, self.msg = ip, port, msg
    def run(self):
        sent, received = self.msg, ''
        try:
            with socket.create_connection((self.ip, self.port), timeout=7) as s:
                s.sendall(sent.encode())
                chunks = []
                while True:
                    try:
                        chunk = s.recv(4096)
                        if not chunk: break
                        chunks.append(chunk)
                    except socket.timeout:
                        break
                received = b''.join(chunks).decode('utf‑8', 'replace')
        except Exception as e:
            received = f"Error: {e}"
        self.result.emit(sent, received)

# ───────────────── credit‑term dialog ─────────────────────────────────────
class CreditTermDlg(QDialog):
    """
    Shown after DISCOVERY only when the card supports credit / instalments.
    """
    term_map = {
        '01 Regular Term'      : '1',
        '02 Special Term (+30)': '2',
        '03 Immediate / Debit' : '3',
        '06 Credit Instalment' : '6',
        '08 Instalment'        : '8',
    }

    def __init__(self, terms_flags: Dict[str, bool],
                 min_pay: int, max_pay: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select credit term")
        g = QGridLayout(self); r = 0
        g.addWidget(QLabel("Card supports the following terms:"), r, 0, 1, 2); r += 1

        self.cmb = QComboBox()
        for label, code in self.term_map.items():
            field = {
                '1': 'regular', '2': 'special', '3': 'immediate',
                '6': 'credit',  '8': 'installments'
            }[code]
            enabled = terms_flags.get(field, False)
            self.cmb.addItem(label, code)
            idx = self.cmb.count() - 1
            self.cmb.model().item(idx).setEnabled(enabled)
        g.addWidget(self.cmb, r, 0, 1, 2); r += 1

        self.pay_label = QLabel("Payments:")
        self.pay_spin  = QSpinBox(minimum=min_pay, maximum=max_pay, value=min_pay)
        g.addWidget(self.pay_label, r, 0); g.addWidget(self.pay_spin, r, 1); r += 1

        self.cmb.currentIndexChanged.connect(self._toggle_payments)
        self._toggle_payments()                              # set initial visibility

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box, r, 0, 1, 2)

    # show payments only for Credit / Instalment
    def _toggle_payments(self, *_):
        code = self.cmb.currentData()
        show = code in ('6', '8')
        self.pay_label.setVisible(show); self.pay_spin.setVisible(show)

    def data(self) -> Tuple[str, int]:
        return self.cmb.currentData(), (self.pay_spin.value()
                                        if self.pay_spin.isVisible() else 0)

# ───────────────────── dialogs ────────────────────────────────────────────
class StartTransactionDlg(QDialog):
    # unchanged … (omitted for brevity – identical to previous version)

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
            ("Invoice", self.inv), ("Cashier ID", self.cash), ("Shift ID", self.shift),
            ("Business Date", self.date), ("POS IP", self.pos_ip), ("POS Port", self.pos_port)
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
    # unchanged … identical to v1.31‑C

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Discover – Read Card")
        g = QGridLayout(self); r = 0

        g.addWidget(QLabel("Timeout (s)"), r, 0)
        self.timeout = QSpinBox(minimum=5, maximum=999, value=60); g.addWidget(self.timeout, r, 1); r += 1

        self.manual = QCheckBox("Manual card entry"); g.addWidget(self.manual, r, 0)
        self.man_reason = QComboBox(); self.man_reason.addItems(["SIG", "CNP"]); self.man_reason.setEnabled(False)
        g.addWidget(self.man_reason, r, 1); r += 1
        self.manual.toggled.connect(self.man_reason.setEnabled)

        g.addWidget(QLabel("Transaction type"), r, 0)
        self.tran_type = QComboBox(); self.tran_type.addItems(
            ["01 Regular Charge", "02 Unloading", "03 Forced", "06 Cashback",
             "30 Balance", "53 Refund", "55 Loading"])
        g.addWidget(self.tran_type, r, 1); r += 1

        g.addWidget(QLabel("Bill amount (₪)"), r, 0)
        self.amount = QDoubleSpinBox(decimals=2, maximum=999999, value=10.00); g.addWidget(self.amount, r, 1)
        g.addWidget(QLabel("Currency"), r, 2)
        self.currency = QComboBox(); self.currency.addItems(["376 NIS", "840 USD", "978 EUR"])
        g.addWidget(self.currency, r, 3); r += 1

        self.cash_chk = QCheckBox("Cash amount?"); g.addWidget(self.cash_chk, r, 0)
        self.cash_amt = QDoubleSpinBox(decimals=2, maximum=999999); self.cash_amt.setEnabled(False)
        g.addWidget(self.cash_amt, r, 1); r += 1
        self.cash_chk.toggled.connect(self.cash_amt.setEnabled)

        self.fx_chk = QCheckBox("Convert currency?"); g.addWidget(self.fx_chk, r, 0)
        self.fx_to  = QComboBox(); self.fx_to.addItems(["376 NIS", "840 USD", "978 EUR"]); self.fx_to.setEnabled(False)
        self.fx_amt = QDoubleSpinBox(decimals=2, maximum=999999); self.fx_amt.setEnabled(False)
        g.addWidget(QLabel("Convert to"), r, 2); g.addWidget(self.fx_to, r, 3); r += 1
        g.addWidget(QLabel("Converted amount"), r, 2); g.addWidget(self.fx_amt, r, 3); r += 1
        self.fx_chk.toggled.connect(lambda b: [w.setEnabled(b) for w in (self.fx_to, self.fx_amt)])

        g.addWidget(QLabel("Generate card token"), r, 0)
        self.gen_token = QComboBox(); self.gen_token.addItems(["0 None", "1 All cards", "2 Shufersal only"])
        g.addWidget(self.gen_token, r, 1); r += 1

        self.use_tok = QCheckBox("Use existing token"); g.addWidget(self.use_tok, r, 0)
        self.tok_val = QLineEdit(); self.tok_val.setEnabled(False); g.addWidget(self.tok_val, r, 1); r += 1
        self.use_tok.toggled.connect(self.tok_val.setEnabled)

        g.addWidget(QLabel("Service type"), r, 0)
        self.service = QComboBox(); self.service.addItems(["", "1", "2", "3"])
        g.addWidget(self.service, r, 1); r += 1

        self.ctls  = QCheckBox("Enable CTLS"); self.ctls.setChecked(True)
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
            'amount': to_minor(self.amount.value()),
            'currency': self.currency.currentText().split()[0],
            'cash': to_minor(self.cash_amt.value()) if self.cash_chk.isChecked() else None,
            'fx': self.fx_chk.isChecked(),
            'fx_to': self.fx_to.currentText().split()[0] if self.fx_chk.isChecked() else '',
            'fx_amt': to_minor(self.fx_amt.value()) if self.fx_chk.isChecked() else None,
            'use_token': self.use_tok.isChecked(),
            'token_val': self.tok_val.text().strip(),
            'service_type': self.service.currentText(),
            'ctls': self.ctls.isChecked(),
            'allow_cancel': self.allow.isChecked(),
            'unattended': self.unatt.isChecked(),
            'operation': self.op.currentText().split()[0]
        }

# ──────────────────────  Main window  ──────────────────────────────────────
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Connector – Quick Sale")
        self.resize(1020, 820)

        # state
        self.session = self.ktk = self.mac_key = ''
        self.threads: List[QThread] = []
        self.qs_active = False          # True while a quick‑sale is in progress
        self.qs_wait_term = False       # waiting for term dialog
        self.qs_defaults: Dict[str, str] = {}
        self.qs_start_body = ''

        # UI – identical to v1.31‑C except for internal behaviour, not shown again
        # … (constructor layout identical – omitted to keep listing compact)

        central = QWidget(); lay = QVBoxLayout(central)

        # connection row
        cr = QHBoxLayout()
        self.ip   = QLineEdit("192.168.1.202"); self.port = QLineEdit("5015")
        cr.addWidget(QLabel("IP:"));   cr.addWidget(self.ip)
        cr.addWidget(QLabel("Port:")); cr.addWidget(self.port); cr.addStretch()
        lay.addLayout(cr)

        # register row
        rr = QHBoxLayout()
        self.chain = QLineEdit("39999"); self.store = QLineEdit("0123")
        self.lane  = QLineEdit("074");   self.alt  = QLineEdit("012345678")
        self.pos_type = QComboBox(); self.pos_type.addItems(["ATTENDED", "UNATTENDED"])
        for lbl, w in (("Chain", self.chain), ("Store", self.store),
                       ("Lane", self.lane), ("Alt‑ID", self.alt)):
            rr.addWidget(QLabel(lbl)); rr.addWidget(w)
        rr.addWidget(QLabel("POS Type")); rr.addWidget(self.pos_type)
        self.reg_btn = QPushButton("Register", clicked=self.cmd_register)
        rr.addWidget(self.reg_btn); lay.addLayout(rr)

        # key row
        kr = QHBoxLayout()
        kr.addWidget(QLabel("KTK (16)"))
        self.ktk_edit = QLineEdit(); self.ktk_edit.setEnabled(False); kr.addWidget(self.ktk_edit)
        self.key_btn  = QPushButton("Exchange Keys", clicked=self.cmd_keys); self.key_btn.setEnabled(False)
        kr.addWidget(self.key_btn); kr.addStretch(); lay.addLayout(kr)

        # MAC view
        mr = QHBoxLayout()
        mr.addWidget(QLabel("MAC Key"))
        self.mac_view = QLineEdit(); self.mac_view.setReadOnly(True)
        mr.addWidget(self.mac_view); mr.addStretch(); lay.addLayout(mr)

        # button row with Quick‑Sale amount
        br = QHBoxLayout()
        self.start_btn = QPushButton("Start Tran…", clicked=self.do_start);   self.start_btn.setEnabled(False)
        self.disc_btn  = QPushButton("Discover…",  clicked=self.do_discover); self.disc_btn.setEnabled(False)
        br.addWidget(self.start_btn); br.addWidget(self.disc_btn)

        br.addWidget(QLabel("Amount ₪"))
        self.qs_amt = QDoubleSpinBox(decimals=2, maximum=999999, value=10.00)
        br.addWidget(self.qs_amt)

        self.quick_btn = QPushButton("Quick Sale", clicked=self.quick_sale);  self.quick_btn.setEnabled(False)
        br.addWidget(self.quick_btn)

        br.addWidget(QLabel("Admin cmd"))
        self.cmd_in = QLineEdit(); self.cmd_send = QPushButton("Send", clicked=self.admin_cmd)
        br.addWidget(self.cmd_in); br.addWidget(self.cmd_send); br.addStretch()
        lay.addLayout(br)

        self.status_btn = QPushButton("Status", clicked=self.cmd_status); self.status_btn.setEnabled(False)
        lay.addWidget(self.status_btn)

        # logs
        self.sent = QTextEdit(readOnly=True); self.recv = QTextEdit(readOnly=True)
        split = QSplitter(Qt.Horizontal); split.addWidget(self.sent); split.addWidget(self.recv)
        lay.addWidget(split)

        # search bar
        srow = QHBoxLayout()
        srow.addWidget(QLabel("Search logs:"))
        self.search_edit = QLineEdit(); self.search_edit.returnPressed.connect(self.search_logs)
        self.search_btn  = QPushButton("Find", clicked=self.search_logs)
        srow.addWidget(self.search_edit); srow.addWidget(self.search_btn); srow.addStretch(); lay.addLayout(srow)

        act = QAction(self); act.setShortcut("Ctrl+F")
        act.triggered.connect(lambda: self.search_edit.setFocus(Qt.ShortcutFocusReason)); self.addAction(act)
        lay.addWidget(QPushButton("Clear logs", clicked=lambda: (self.sent.clear(), self.recv.clear())))

        self.setCentralWidget(central)
        self._load_mac()

    # ───────── persistence ────────────────────────────────────────────────
    def _load_mac(self):
        if os.path.exists("mac.txt"):
            self.mac_key = open("mac.txt").read().strip()
            if self.mac_key:
                self.mac_view.setText(self.mac_key)
                for b in (self.status_btn, self.start_btn, self.disc_btn, self.quick_btn):
                    b.setEnabled(True)
    def _save_mac(self): open("mac.txt", "w").write(self.mac_key)

    # ───────── XML helpers ────────────────────────────────────────────────
    def _env(self, fg: str, cmd: str, body: str = "") -> str:
        ts = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        hdr = (f"<TRANSACTION><FUNCTION_GROUP>{fg}</FUNCTION_GROUP><COMMAND>{cmd}</COMMAND>"
               f"<SESSION_ID>{self.session}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
               f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>")
        xml_wo = hdr + body + "<MAC></MAC></TRANSACTION>"
        mac = calc_mac(xml_wo, self.mac_key) if self.mac_key else ''
        return hdr + body + f"<MAC>{mac}</MAC></TRANSACTION>"

    def _send(self, xml: str):
        try:
            port = int(self.port.text())
        except ValueError:
            QMessageBox.critical(self, "Port", "Invalid port value."); return
        t = SockThread(self.ip.text().strip(), port, xml)
        t.result.connect(self._handle); t.finished.connect(lambda: self.threads.remove(t))
        self.threads.append(t); t.start()

    # body builders -------------------------------------------------------
    def _disc_body(self, d: Dict[str, Optional[str]]) -> str:
        """
        Build the <DISCOVERY> body – same logic as before, CREDIT_TERMS removed.
        """
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
        x += f"<OPERATION>{d['operation']}</OPERATION></TRANSACTION_DETAILS>"
        return f"<TIMEOUT>{d['timeout']}</TIMEOUT>" + x

    def _auth_body(self, defaults: Dict[str, str],
                   cred_term: str, payments: int) -> str:
        """
        Build AUTHORIZE body based on defaults + chosen credit term.
        """
        x = self._disc_body(defaults)          # reuse the same tags
        # inject CREDIT_TERMS + payments
        if '</TRANSACTION_DETAILS>' in x:
            x = x.replace('</TRANSACTION_DETAILS>',
                          f"<CREDIT_TERMS>{cred_term}</CREDIT_TERMS>"
                          + (f"<PAYMENTS_NUMBER>{payments:02d}</PAYMENTS_NUMBER>" if payments else '')
                          + "</TRANSACTION_DETAILS>")
        return x

    # ───────── button slots ────────────────────────────────────────────────
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

    # ───────────── quick‑sale orchestrator ────────────────────────────────
    def quick_sale(self):
        if self.qs_active:
            QMessageBox.warning(self, "Quick Sale", "Already running."); return

        amt_minor = to_minor(self.qs_amt.value())
        self.qs_defaults = {'timeout':'60','restrict_token':'0','manual':False,'manual_reason':'',
                            'tran_type':'01','amount':amt_minor,'currency':'376','cash':None,
                            'fx':False,'fx_to':'','fx_amt':None,'use_token':False,'token_val':'',
                            'service_type':'','ctls':True,'allow_cancel':True,'unattended':False,
                            'operation':'04'}
        self.qs_start_body = f"<INVOICE>100000</INVOICE><POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>"
        self.qs_active = True

        self._send(self._env("SESSION", "START_TRAN", self.qs_start_body))
        # DISCOVERY will be sent in _handle after START_TRAN succeeds

    def admin_cmd(self):
        cmd = self.cmd_in.text().strip().upper()
        if cmd: self._send(self._env("ADMIN", cmd))

    # ───────── search / highlight ──────────────────────────────────────────
    def search_logs(self):
        term = self.search_edit.text()
        for pane in (self.sent, self.recv):
            self._highlight(pane, term)

    def _highlight(self, pane: QTextEdit, word: str):
        pane.setExtraSelections([])
        if not word: return
        sels, doc = [], pane.document(); cur = QTextCursor(doc)
        while True:
            cur = doc.find(word, cur)
            if cur.isNull(): break
            sel = QTextEdit.ExtraSelection(); sel.cursor = cur
            fmt = QTextCharFormat(); fmt.setBackground(QColor("#ffff66"))
            sel.format = fmt; sels.append(sel)
        pane.setExtraSelections(sels)

    # ───────── response handler ────────────────────────────────────────────
    def _handle(self, sent_xml: str, recv_xml: str):
        self.sent.append(sent_xml); self.recv.append(recv_xml)

        # ───── orchestrate quick‑sale flow ────────────────────────────
        if self.qs_active:
            if '<COMMAND>START_TRAN' in sent_xml:
                ok = '<RESULT_CODE>0<' in recv_xml or '<EVENT>COMPLETED' in recv_xml
                if ok:
                    # step 2: DISCOVERY
                    self._send(self._env("PAYMENT", "DISCOVERY",
                                         self._disc_body(self.qs_defaults)))
                    return
                else:
                    self.qs_active = False
                    return

            if '<COMMAND>DISCOVERY' in sent_xml:
                # analyse DISCOVERY result
                if '<RESULT_CODE>0<' not in recv_xml:
                    self.qs_active = False
                    return

                # parse terms flags
                flags = {f: bool(re.search(fr'<TERMS_{f.upper()}>\s*1\s*</TERMS_{f.upper()}>',
                                           recv_xml, re.I))
                         for f in ('regular', 'special', 'immediate', 'credit', 'installments')}
                credit_ok = flags['credit'] or flags['installments']

                if credit_ok:
                    # min / max from device if present
                    def _int(tag, default):
                        m = re.search(fr'<{tag}>(\d+)</{tag}>', recv_xml)
                        return int(m.group(1)) if m else default
                    min_pay = max(2, _int('CREDIT_MIN_PAYMENTS', 2))
                    max_pay = max(min_pay, _int('CREDIT_MAX_PAYMENTS', 36))

                    dlg = CreditTermDlg(flags, min_pay, max_pay, self)
                    if dlg.exec_() != QDialog.Accepted:
                        QMessageBox.information(self, "Quick Sale", "Cancelled by user.")
                        self.qs_active = False
                        return
                    cred_term, payments = dlg.data()
                else:
                    cred_term, payments = '1', 0   # default regular

                # send AUTHORIZE
                auth_body = self._auth_body(self.qs_defaults, cred_term, payments)
                self._send(self._env("PAYMENT", "AUTHORIZE", auth_body))
                self.qs_wait_term = False
                return

            if '<COMMAND>AUTHORIZE' in sent_xml:
                # show receipt if approved
                if '<RESULT_CODE>0<' in recv_xml:
                    if m := re.search(r'<RECEIPT_ARR>(.*?)</RECEIPT_ARR>', recv_xml, re.S):
                        QMessageBox.information(self, "Receipt",
                                                html.unescape(m.group(1).strip()))
                # FINISH TRAN whether approved or not
                self._send(self._env("SESSION", "FINISH_TRAN"))
                return

            if '<COMMAND>FINISH_TRAN' in sent_xml:
                self.qs_active = False
                return

        # ───── generic flow / registration / MAC etc ──────────────────
        if '<COMMAND>REGISTER' in sent_xml and '<EVENT>COMPLETED' in recv_xml:
            self.ktk_edit.setEnabled(True); self.key_btn.setEnabled(True)

        if '<COMMAND>EXCHANGE_KEYS' in sent_xml and '<MAC_KEY>' in recv_xml:
            if (m := re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>', recv_xml)):
                try:
                    self.mac_key = des3_decrypt(self.ktk, m.group(1))
                    self.mac_view.setText(self.mac_key); self._save_mac()
                    for b in (self.status_btn, self.start_btn, self.disc_btn, self.quick_btn):
                        b.setEnabled(True)
                except Exception as e:
                    QMessageBox.critical(self, "Decrypt error", str(e))

    # ───────── thread cleanup ──────────────────────────────────────────────
    def closeEvent(self, ev):
        for t in self.threads: t.quit(); t.wait()
        ev.accept()

# ───────────────────── run ────────────────────────────────────────────────
if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    Main().show()
    sys.exit(app.exec_())
