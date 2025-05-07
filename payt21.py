#!/usr/bin/env python3
# Verifone P400 connector – Quick‑Sale flow + webhook trigger
# 2025‑05‑07  v1.33
#
# • v1.32‑C‑>v1.33: added WebhookServer (localhost:8080/pay?amount=xx.yy)
#   that triggers Quick‑Sale automatically.

import sys, socket, os, re, random, html
from datetime import datetime, timezone
from base64   import b64decode, b64encode
from typing   import Dict, Optional, List, Tuple

from PyQt5.QtCore    import Qt, QThread, pyqtSignal, pyqtSlot, QDate
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QCheckBox,
    QGridLayout, QVBoxLayout, QHBoxLayout, QSplitter, QMessageBox,
    QDialogButtonBox, QAction
)
from PyQt5.QtGui     import QTextCursor, QTextCharFormat, QColor
from Crypto.Cipher   import DES3
from Crypto.Hash     import SHA256

# ─────────── std‑lib for webhook server ────────────
from http.server     import HTTPServer, BaseHTTPRequestHandler
from urllib.parse    import urlparse, parse_qs


# ───────────────────────── helpers ─────────────────────────────────────────
def rand_session() -> str:
    return ''.join(random.choice('0123456789') for _ in range(16))


def des3_decrypt(ktk: str, mac_b64: str) -> str:
    if len(ktk) != 16:
        raise ValueError("KTK must be exactly 16 ASCII characters.")
    data  = b64decode(mac_b64)
    key24 = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    return DES3.new(key24, DES3.MODE_ECB).decrypt(data).rstrip(b'\0').decode()


def calc_mac(xml_wo: str, mac_key: str) -> str:
    """SHA‑256 MAC + Base64."""
    return b64encode(SHA256.new((xml_wo + mac_key).encode()).digest()).decode()


def to_minor(val: float) -> str:
    """₪10.00 → '1000' (agorot)."""
    return str(int(round(val * 100)))


# ───────────────────── threaded TCP sender ────────────────────────────────
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
                pieces: List[bytes] = []
                while True:
                    try:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        pieces.append(chunk)
                    except socket.timeout:
                        break
                received = b''.join(pieces).decode('utf‑8', 'replace')
        except Exception as e:
            received = f"Error: {e}"
        self.result.emit(sent, received)


# ───────────────────── webhook HTTP server ────────────────────────────────
class WebhookServer(QThread):
    """Listen on http://localhost:8080/pay?amount=123.45 and emit the amount."""
    receivedAmount = pyqtSignal(float)

    def __init__(self, host: str = 'localhost', port: int = 8080, parent=None):
        super().__init__(parent)
        self.host, self.port = host, port

    def run(self):
        parent = self  # closure reference for handler

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                p = urlparse(self.path)
                if p.path != "/pay":
                    self._send(404, b"Not Found"); return
                q = parse_qs(p.query)
                val = q.get("amount", [])
                if not val:
                    self._send(400, b"Error: amount param required"); return
                try:
                    amt = float(val[0])
                    if amt <= 0:
                        raise ValueError
                except ValueError:
                    self._send(400, b"Error: amount must be positive number"); return

                # ok
                self._send(200, b"OK")
                parent.receivedAmount.emit(amt)

            def log_message(self, *_):  # silence default logging
                return

            def _send(self, code, body: bytes):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = HTTPServer((self.host, self.port), Handler)
        server.allow_reuse_address = True
        try:
            server.serve_forever()
        finally:
            server.server_close()


# ───────────────────── credit‑term dialog (unchanged) ─────────────────────
class CreditTermDlg(QDialog):
    _map = {
        '01 Regular Term'      : '1',
        '02 Special Term (+30)': '2',
        '03 Immediate / Debit' : '3',
        '06 Credit Instalment' : '6',
        '08 Instalment'        : '8',
    }

    def __init__(self, flags: Dict[str, bool],
                 min_pay: int, max_pay: int, total: float, parent=None):
        super().__init__(parent)
        self.total = total
        self.setWindowTitle("Credit / Installment details")
        g = QGridLayout(self); r = 0

        g.addWidget(QLabel("Supported terms:"), r, 0, 1, 2); r += 1
        self.cmb = QComboBox()
        for lbl, code in self._map.items():
            field = {'1':'regular','2':'special','3':'immediate',
                     '6':'credit','8':'installments'}[code]
            self.cmb.addItem(lbl, code)
            self.cmb.model().item(self.cmb.count()-1).setEnabled(flags.get(field, False))
        g.addWidget(self.cmb, r, 0, 1, 2); r += 1

        self.lbl_pay, self.spin_pay = QLabel("Payments:"), QSpinBox()
        self.spin_pay.setRange(min_pay, max_pay); self.spin_pay.setValue(min_pay)
        g.addWidget(self.lbl_pay, r, 0); g.addWidget(self.spin_pay, r, 1); r += 1

        self.lbl_first = QLabel("First payment (₪):")
        self.spin_first = QDoubleSpinBox(decimals=2, maximum=total, value=total/self.spin_pay.value())
        self.lbl_next  = QLabel("Next payments (₪):")
        self.spin_next = QDoubleSpinBox(decimals=2, maximum=total, value=self.spin_first.value())
        self.spin_next.setReadOnly(True)
        g.addWidget(self.lbl_first, r, 0); g.addWidget(self.spin_first, r, 1); r += 1
        g.addWidget(self.lbl_next,  r, 0); g.addWidget(self.spin_next,  r, 1); r += 1

        self.cmb.currentIndexChanged.connect(self._toggle)
        self.spin_pay.valueChanged.connect(self._recalc)
        self.spin_first.valueChanged.connect(self._recalc)
        self._toggle()

        box = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box, r, 0, 1, 2)

    def _toggle(self, *_):
        inst = self.cmb.currentData() in ('6','8')
        for w in (self.lbl_pay, self.spin_pay,
                  self.lbl_first, self.spin_first,
                  self.lbl_next, self.spin_next):
            w.setVisible(inst)
        self._recalc()

    def _recalc(self, *_):
        if not self.spin_first.isVisible(): return
        p = self.spin_pay.value(); first = self.spin_first.value()
        nxt = max(0.0, self.total - first) / max(1, p-1)
        self.spin_next.blockSignals(True)
        self.spin_next.setValue(round(nxt,2))
        self.spin_next.blockSignals(False)

    def data(self) -> Tuple[str,int,float,float]:
        code = self.cmb.currentData()
        if code in ('6','8'):
            return code, self.spin_pay.value(), self.spin_first.value(), self.spin_next.value()
        return code, 0, 0.0, 0.0


# ───────────────────── dialogs (StartTransaction, Discover) unchanged ────
# ... (identical to v1.32‑C, omitted for brevity) ...


# ────────────────────── Main window ───────────────────────────────────────
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Connector – Quick Sale + Webhook")
        self.resize(1020, 820)

        # state vars
        self.session = self.ktk = self.mac_key = ''
        self.threads: List[QThread] = []
        self.qs_active = False
        self.qs_defaults: Dict[str,str] = {}
        self.qs_start_body = ''

        # -------------------- UI (same as before) -------------------------
        central = QWidget(); lay = QVBoxLayout(central)

        cr = QHBoxLayout()
        self.ip = QLineEdit("192.168.1.202"); self.port = QLineEdit("5015")
        cr.addWidget(QLabel("IP:")); cr.addWidget(self.ip)
        cr.addWidget(QLabel("Port:")); cr.addWidget(self.port); cr.addStretch()
        lay.addLayout(cr)

        rr = QHBoxLayout()
        self.chain,self.store = QLineEdit("39999"),QLineEdit("0123")
        self.lane,self.alt = QLineEdit("074"),QLineEdit("012345678")
        self.pos_type = QComboBox(); self.pos_type.addItems(["ATTENDED","UNATTENDED"])
        for lbl,w in (("Chain",self.chain),("Store",self.store),
                      ("Lane",self.lane),("Alt‑ID",self.alt)):
            rr.addWidget(QLabel(lbl)); rr.addWidget(w)
        rr.addWidget(QLabel("POS Type")); rr.addWidget(self.pos_type)
        self.reg_btn = QPushButton("Register",clicked=self.cmd_register); rr.addWidget(self.reg_btn)
        lay.addLayout(rr)

        kr = QHBoxLayout()
        kr.addWidget(QLabel("KTK (16)"))
        self.ktk_edit = QLineEdit(); self.ktk_edit.setEnabled(False); kr.addWidget(self.ktk_edit)
        self.key_btn  = QPushButton("Exchange Keys",clicked=self.cmd_keys); self.key_btn.setEnabled(False)
        kr.addWidget(self.key_btn); kr.addStretch(); lay.addLayout(kr)

        mr = QHBoxLayout()
        mr.addWidget(QLabel("MAC Key")); self.mac_view = QLineEdit(); self.mac_view.setReadOnly(True)
        mr.addWidget(self.mac_view); mr.addStretch(); lay.addLayout(mr)

        br = QHBoxLayout()
        self.start_btn = QPushButton("Start Tran…",clicked=self.do_start); self.start_btn.setEnabled(False)
        self.disc_btn  = QPushButton("Discover…", clicked=self.do_discover); self.disc_btn.setEnabled(False)
        br.addWidget(self.start_btn); br.addWidget(self.disc_btn)
        br.addWidget(QLabel("Amount ₪"))
        self.qs_amt = QDoubleSpinBox(decimals=2,maximum=999999,value=10.00); br.addWidget(self.qs_amt)
        self.quick_btn = QPushButton("Quick Sale",clicked=lambda: self.quick_sale(self.qs_amt.value()))
        self.quick_btn.setEnabled(False); br.addWidget(self.quick_btn)
        br.addWidget(QLabel("Admin cmd")); self.cmd_in = QLineEdit()
        self.cmd_send = QPushButton("Send",clicked=self.admin_cmd)
        br.addWidget(self.cmd_in); br.addWidget(self.cmd_send); br.addStretch()
        lay.addLayout(br)

        self.status_btn = QPushButton("Status",clicked=self.cmd_status); self.status_btn.setEnabled(False)
        lay.addWidget(self.status_btn)

        self.sent,self.recv = QTextEdit(readOnly=True),QTextEdit(readOnly=True)
        split = QSplitter(Qt.Horizontal); split.addWidget(self.sent); split.addWidget(self.recv); lay.addWidget(split)

        srow = QHBoxLayout()
        srow.addWidget(QLabel("Search logs:")); self.search_edit = QLineEdit()
        self.search_btn = QPushButton("Find",clicked=self.search_logs)
        self.search_edit.returnPressed.connect(self.search_logs)
        srow.addWidget(self.search_edit); srow.addWidget(self.search_btn); srow.addStretch(); lay.addLayout(srow)
        act = QAction(self); act.setShortcut("Ctrl+F")
        act.triggered.connect(lambda: self.search_edit.setFocus(Qt.ShortcutFocusReason)); self.addAction(act)
        lay.addWidget(QPushButton("Clear logs",clicked=lambda:(self.sent.clear(),self.recv.clear())))
        self.setCentralWidget(central)

        self._load_mac()

        # -------------------- start webhook server -----------------------
        self.webhook = WebhookServer(parent=self)
        self.webhook.receivedAmount.connect(self._from_webhook)
        self.webhook.start()

    # ───────── persistence ────────────────────────────────────────────────
    def _load_mac(self):
        if os.path.exists("mac.txt"):
            self.mac_key = open("mac.txt").read().strip()
            if self.mac_key:
                self.mac_view.setText(self.mac_key)
                for b in (self.status_btn,self.start_btn,self.disc_btn,self.quick_btn):
                    b.setEnabled(True)

    def _save_mac(self): open("mac.txt","w").write(self.mac_key)

    # ───────── XML helpers / _send identical to v1.32‑C ───────────────────
    def _env(self, fg:str, cmd:str, body:str="") -> str:
        ts = datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        hdr=(f"<TRANSACTION><FUNCTION_GROUP>{fg}</FUNCTION_GROUP><COMMAND>{cmd}</COMMAND>"
             f"<SESSION_ID>{self.session}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
             f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>")
        xml_wo=hdr+body+"<MAC></MAC></TRANSACTION>"
        mac=calc_mac(xml_wo,self.mac_key) if self.mac_key else ''
        return hdr+body+f"<MAC>{mac}</MAC></TRANSACTION>"

    def _send(self, xml:str):
        try: port=int(self.port.text())
        except ValueError:
            QMessageBox.critical(self,"Port","Invalid port"); return
        t=SockThread(self.ip.text().strip(),port,xml)
        t.result.connect(self._handle); t.finished.connect(lambda: self.threads.remove(t))
        self.threads.append(t); t.start()

    # ───────── body builders & credit‑term logic identical to v1.32‑C ────
    # ... (same _disc_body, _auth_body code as before) ...

    def _disc_body(self, d:Dict[str,Optional[str]])->str:
        x=(f"<TRANSACTION_DETAILS><RESTRICT_TOKEN>{d['restrict_token']}</RESTRICT_TOKEN>"
           f"<MANUAL>{int(d['manual'])}</MANUAL><CTLS>{int(d['ctls'])}</CTLS>"
           f"<ALLOW_CANCEL>{int(d['allow_cancel'])}</ALLOW_CANCEL>"
           f"<UNATTENDED>{int(d['unattended'])}</UNATTENDED>"
           f"<TRAN_TYPE>{d['tran_type']}</TRAN_TYPE><MTI>100</MTI><ENTRY_MODE>04</ENTRY_MODE>"
           f"<TRANSACTION_AMOUNT>{d['amount']}</TRANSACTION_AMOUNT>"
           f"<ORIGINAL_CURRENCY>{d['currency']}</ORIGINAL_CURRENCY>")
        if d['manual'] and d['manual_reason']: x+=f"<MANUAL_REASON>{d['manual_reason']}</MANUAL_REASON>"
        if d['cash'] is not None: x+=f"<CASH_AMOUNT>{d['cash']}</CASH_AMOUNT>"
        if d['fx']: x+=(f"<CONVERTED_AMOUNT>{d['fx_amt']}</CONVERTED_AMOUNT>"
                        f"<CONVERTED_CURRENCY>{d['fx_to']}</CONVERTED_CURRENCY>")
        if d['use_token'] and d['token_val']: x+=f"<CARD_TOKEN>{d['token_val']}</CARD_TOKEN>"
        if d['service_type']: x+=f"<SERVICE_TYPE>{d['service_type']}</SERVICE_TYPE>"
        x+=f"<OPERATION>{d['operation']}</OPERATION></TRANSACTION_DETAILS>"
        return f"<TIMEOUT>{d['timeout']}</TIMEOUT>"+x

    def _auth_body(self, defaults:Dict[str,str], ct:str, pay:int, first:float, nxt:float)->str:
        x=self._disc_body(defaults)
        inject=f"<CREDIT_TERMS>{ct}</CREDIT_TERMS>"
        if pay: inject+=f"<PAYMENTS_NUMBER>{pay:02d}</PAYMENTS_NUMBER>"
        if first: inject+=f"<FIRST_PAYMENT_AMOUNT>{to_minor(first)}</FIRST_PAYMENT_AMOUNT>"
        if nxt:   inject+=f"<NEXT_PAYMENT_AMOUNT>{to_minor(nxt)}</NEXT_PAYMENT_AMOUNT>"
        return x.replace('</TRANSACTION_DETAILS>',inject+'</TRANSACTION_DETAILS>')

    # ───────── command slots ──────────────────────────────────────────────
    def cmd_register(self):
        self.session=rand_session()
        body=(f"<CHAIN>{self.chain.text()}</CHAIN><STORE>{self.store.text()}</STORE>"
              f"<LANE>{self.lane.text()}</LANE><TERMINAL_ID>{self.alt.text()}</TERMINAL_ID>"
              f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>")
        self._send(self._env("ADMIN","REGISTER",body))

    def cmd_keys(self):
        self.ktk=self.ktk_edit.text().strip()
        self._send(self._env("ADMIN","EXCHANGE_KEYS",f"<KTK>{self.ktk}</KTK>"))

    def cmd_status(self): self._send(self._env("ADMIN","STATUS"))

    def do_start(self):
        dlg=StartTransactionDlg(self)
        if dlg.exec_()!=QDialog.Accepted: return
        body=''.join(f"<{k}>{v}</{k}>" for k,v in dlg.data().items())
        self._send(self._env("SESSION","START_TRAN",body))

    def do_discover(self):
        dlg=DiscoverDlg(self)
        if dlg.exec_()!=QDialog.Accepted: return
        self._send(self._env("PAYMENT","DISCOVERY",self._disc_body(dlg.data())))

    # ───────── Quick‑Sale entry point (GUI & webhook) ─────────────────────
    def quick_sale(self, amount:float):
        if self.qs_active:
            QMessageBox.warning(self,"Quick Sale","Already running."); return
        self.qs_amt.setValue(amount)
        amt_minor=to_minor(amount)
        self.qs_defaults={'timeout':'60','restrict_token':'0','manual':False,'manual_reason':'',
                          'tran_type':'01','amount':amt_minor,'currency':'376','cash':None,
                          'fx':False,'fx_to':'','fx_amt':None,'use_token':False,'token_val':'',
                          'service_type':'','ctls':True,'allow_cancel':True,'unattended':False,
                          'operation':'04'}
        self.qs_start_body=(f"<INVOICE>100000</INVOICE>"
                            f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>")
        self.qs_active=True
        self._send(self._env("SESSION","START_TRAN",self.qs_start_body))

    @pyqtSlot(float)
    def _from_webhook(self, amt:float):
        self.quick_sale(amt)

    def admin_cmd(self):
        cmd=self.cmd_in.text().strip().upper()
        if cmd: self._send(self._env("ADMIN",cmd))

    # ───────── search ─────────────────────────────────────────────────────
    def search_logs(self):
        term=self.search_edit.text()
        for pane in (self.sent,self.recv):
            pane.setExtraSelections([])
            if not term: continue
            sels,doc=[],pane.document(); cur=QTextCursor(doc)
            while True:
                cur=doc.find(term,cur); 
                if cur.isNull(): break
                sel=QTextEdit.ExtraSelection(); sel.cursor=cur
                fmt=QTextCharFormat(); fmt.setBackground(QColor("#ffff66"))
                sel.format=fmt; sels.append(sel)
            pane.setExtraSelections(sels)

    # ───────── response handler (same FSM as v1.32‑C) ─────────────────────
    def _handle(self,sent:str,recv:str):
        self.sent.append(sent); self.recv.append(recv)

        # --- quick‑sale FSM ---
        if self.qs_active:
            if '<COMMAND>START_TRAN' in sent:
                if '<RESULT_CODE>0<' in recv:
                    self._send(self._env("PAYMENT","DISCOVERY",
                                         self._disc_body(self.qs_defaults)))
                else: self.qs_active=False
                return

            if '<COMMAND>DISCOVERY' in sent:
                if '<RESULT_CODE>0<' not in recv: self.qs_active=False; return
                def flag(n): return bool(re.search(fr'<TERMS_{n.upper()}>1</TERMS_{n.upper()}>',recv,re.I))
                flags={f:flag(f) for f in ('regular','special','immediate','credit','installments')}
                credit_ok=flags['credit'] or flags['installments']
                if credit_ok:
                    def itag(tag,d): m=re.search(fr'<{tag}>(\d+)</{tag}>',recv); return int(m.group(1)) if m else d
                    min_pay=max(2,itag('CREDIT_MIN_PAYMENTS',2))
                    max_pay=max(min_pay,itag('CREDIT_MAX_PAYMENTS',36))
                    dlg=CreditTermDlg(flags,min_pay,max_pay,self.qs_amt.value(),self)
                    if dlg.exec_()!=QDialog.Accepted:
                        QMessageBox.information(self,"Quick Sale","Cancelled."); self.qs_active=False; return
                    ct,pay,first,nxt=dlg.data()
                else: ct,pay,first,nxt='1',0,0.0,0.0
                self._send(self._env("PAYMENT","AUTHORIZE",
                                     self._auth_body(self.qs_defaults,ct,pay,first,nxt))); return

            if '<COMMAND>AUTHORIZE' in sent:
                if '<RESULT_CODE>0<' in recv:
                    if m:=re.search(r'<RECEIPT_ARR>(.*?)</RECEIPT_ARR>',recv,re.S):
                        QMessageBox.information(self,"Receipt",html.unescape(m.group(1).strip()))
                self._send(self._env("SESSION","FINISH_TRAN")); return

            if '<COMMAND>FINISH_TRAN' in sent:
                self.qs_active=False; return

        # --- registration / MAC ---
        if '<COMMAND>REGISTER' in sent and '<EVENT>COMPLETED' in recv:
            self.ktk_edit.setEnabled(True); self.key_btn.setEnabled(True)

        if '<COMMAND>EXCHANGE_KEYS' in sent and '<MAC_KEY>' in recv:
            if (m:=re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>',recv)):
                try:
                    self.mac_key=des3_decrypt(self.ktk,m.group(1))
                    self.mac_view.setText(self.mac_key); self._save_mac()
                    for b in (self.status_btn,self.start_btn,self.disc_btn,self.quick_btn):
                        b.setEnabled(True)
                except Exception as e:
                    QMessageBox.critical(self,"Decrypt error",str(e))

    # ───────── cleanup ───────────────────────────────────────────────────
    def closeEvent(self,ev):
        self.webhook.terminate()   # stops HTTP server thread
        for t in self.threads: t.quit(); t.wait()
        ev.accept()


# ───────────────────── run ────────────────────────────────────────────────
if __name__=="__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling,True)
    app=QApplication(sys.argv)
    Main().show()
    sys.exit(app.exec_())
