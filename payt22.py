#!/usr/bin/env python3
# Verifone P400 connector – Quick‑Sale flow + webhook trigger
# 2025‑05‑07  v1.34
#
# > Webhook:  GET /pay?amount=<N>&type=<TT>
#   • amount – required positive number
#   • type   – optional two‑digit code {01,02,03,06,30,53,55}; defaults to 01

import sys, os, socket, html, random, re
from datetime   import datetime, timezone
from base64     import b64decode, b64encode
from typing     import Dict, List, Optional, Tuple

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

from http.server  import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ─────────────── utility helpers ───────────────
VALID_TYPES = {'01','02','03','06','30','53','55'}

def rand_session() -> str: return ''.join(random.choice('0123456789') for _ in range(16))
def to_minor(val: float) -> str: return str(int(round(val*100)))
def calc_mac(xml: str, key: str) -> str:
    return b64encode(SHA256.new((xml + key).encode()).digest()).decode()

def des3_decrypt(ktk: str, mac_b64: str) -> str:
    if len(ktk) != 16: raise ValueError("KTK must be 16 ASCII chars")
    key24 = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    return DES3.new(key24, DES3.MODE_ECB).decrypt(b64decode(mac_b64)).rstrip(b'\0').decode()

# ─────────────── TCP worker ───────────────
class SockThread(QThread):
    result = pyqtSignal(str, str)
    def __init__(self, ip: str, port: int, msg: str):
        super().__init__(); self.ip, self.port, self.msg = ip, port, msg
    def run(self):
        sent, received = self.msg, ''
        try:
            with socket.create_connection((self.ip, self.port), timeout=7) as s:
                s.sendall(sent.encode())
                data = []
                while True:
                    try: chunk = s.recv(4096)
                    except socket.timeout: break
                    if not chunk: break
                    data.append(chunk)
                received = b''.join(data).decode('utf-8','replace')
        except Exception as e:
            received = f"Error: {e}"
        self.result.emit(sent, received)

# ─────────────── webhook server thread ───────────────
class WebhookServer(QThread):
    receivedPayment = pyqtSignal(float, str)                # amount, tran_type
    def __init__(self, host='localhost', port=8080, parent=None):
        super().__init__(parent); self.host, self.port = host, port
    def run(self):
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                p = urlparse(self.path)
                if p.path != '/pay':
                    return self._reply(404, b"Not Found")
                q = parse_qs(p.query)
                # amount
                if 'amount' not in q:
                    return self._reply(400, b"amount param required")
                try:
                    amt = float(q['amount'][0])
                    if amt <= 0: raise ValueError
                except ValueError:
                    return self._reply(400, b"amount must be positive number")
                # type
                tcode = q.get('type', ['01'])[0]
                if tcode not in VALID_TYPES:
                    return self._reply(400, b"invalid type code")
                self._reply(200, b"OK")
                outer.receivedPayment.emit(amt, tcode)
            def log_message(self, *a): return          # silence
            def _reply(self, code, body):
                self.send_response(code)
                self.send_header("Content-Type","text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        srv = HTTPServer((self.host,self.port), Handler); srv.allow_reuse_address = True
        try: srv.serve_forever()
        finally: srv.server_close()

# ─────────────── credit‑term dialog (unchanged logic) ───────────────
class CreditTermDlg(QDialog):
    _map = {'01 Regular':'1','02 Special(+30)':'2','03 Immediate':'3',
            '06 Credit Instal':'6','08 Instalment':'8'}
    def __init__(self, flags:Dict[str,bool], mn:int, mx:int, total:float, parent=None):
        super().__init__(parent); self.total = total
        self.setWindowTitle("Credit / Installment")
        g=QGridLayout(self); r=0
        g.addWidget(QLabel("Supported terms:"),r,0,1,2); r+=1
        self.cmb=QComboBox()
        for lbl,code in self._map.items():
            field={'1':'regular','2':'special','3':'immediate','6':'credit','8':'installments'}[code]
            self.cmb.addItem(lbl,code)
            self.cmb.model().item(self.cmb.count()-1).setEnabled(flags.get(field,False))
        g.addWidget(self.cmb,r,0,1,2); r+=1
        self.pay_lbl, self.pay_spin = QLabel("Payments:"), QSpinBox(minimum=mn,maximum=mx,value=mn)
        g.addWidget(self.pay_lbl,r,0); g.addWidget(self.pay_spin,r,1); r+=1
        self.first_lbl, self.first_spin = QLabel("First ₪:"), QDoubleSpinBox(decimals=2,maximum=total,value=total/mn)
        self.next_lbl,  self.next_spin  = QLabel("Next ₪:" ), QDoubleSpinBox(decimals=2,maximum=total,value=self.first_spin.value()); self.next_spin.setReadOnly(True)
        g.addWidget(self.first_lbl,r,0); g.addWidget(self.first_spin,r,1); r+=1
        g.addWidget(self.next_lbl ,r,0); g.addWidget(self.next_spin ,r,1); r+=1
        self.cmb.currentIndexChanged.connect(self._toggle)
        self.pay_spin.valueChanged.connect(self._recalc); self.first_spin.valueChanged.connect(self._recalc)
        self._toggle()
        box=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box,r,0,1,2)
    def _toggle(self,*_):
        vis = self.cmb.currentData() in ('6','8')
        for w in (self.pay_lbl,self.pay_spin,self.first_lbl,self.first_spin,self.next_lbl,self.next_spin): w.setVisible(vis)
        self._recalc()
    def _recalc(self,*_):
        if not self.first_spin.isVisible(): return
        nxt = max(0.0, self.total - self.first_spin.value()) / max(1, self.pay_spin.value()-1)
        self.next_spin.blockSignals(True); self.next_spin.setValue(round(nxt,2)); self.next_spin.blockSignals(False)
    def data(self)->Tuple[str,int,float,float]:
        c=self.cmb.currentData()
        if c in ('6','8'): return c, self.pay_spin.value(), self.first_spin.value(), self.next_spin.value()
        return c,0,0.0,0.0

# ─────────────── (smaller) StartTransaction & Discover dialogs – identical to earlier versions ───────────────
class StartTransactionDlg(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent); self.setWindowTitle("Start Transaction")
        g=QGridLayout(self); self.inv=QLineEdit("100000"); self.cash=QLineEdit(); self.shift=QSpinBox(minimum=0,maximum=9999,value=1)
        self.date=QDateEdit(calendarPopup=True); self.date.setDate(QDate.currentDate())
        self.pos_ip,self.pos_port=QLineEdit(),QLineEdit()
        for i,(lbl,w) in enumerate((("Invoice",self.inv),("Cashier ID",self.cash),("Shift ID",self.shift),
                                    ("Business Date",self.date),("POS IP",self.pos_ip),("POS Port",self.pos_port))):
            g.addWidget(QLabel(lbl),i,0); g.addWidget(w,i,1)
        g.addWidget(QLabel("* optional"),6,0,1,2)
        box=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); box.button(QDialogButtonBox.Ok).setText("Start")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject); g.addWidget(box,7,0,1,2)
    def data(self)->Dict[str,str]:
        d={'INVOICE':self.inv.text(),'CASHIER_ID':self.cash.text(),'SHIFT_ID':str(self.shift.value()) if self.shift.value() else '',
           'BUSINESSDATE':self.date.date().toString("yyyyMMdd"),'POS_IP':self.pos_ip.text(),'POS_PORT':self.pos_port.text()}
        return {k:v for k,v in d.items() if v}

class DiscoverDlg(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent); self.setWindowTitle("Discover – Read Card"); g=QGridLayout(self); r=0
        self.timeout=QSpinBox(minimum=5,maximum=999,value=60); g.addWidget(QLabel("Timeout (s)"),r,0); g.addWidget(self.timeout,r,1); r+=1
        self.manual=QCheckBox("Manual entry"); g.addWidget(self.manual,r,0)
        self.man_reason=QComboBox(); self.man_reason.addItems(["SIG","CNP"]); self.man_reason.setEnabled(False); g.addWidget(self.man_reason,r,1); r+=1
        self.manual.toggled.connect(self.man_reason.setEnabled)
        self.tran_type=QComboBox(); self.tran_type.addItems(["01 Regular","02 Unloading","03 Forced","06 Cashback","30 Balance","53 Refund","55 Loading"])
        g.addWidget(QLabel("Tran. type"),r,0); g.addWidget(self.tran_type,r,1); r+=1
        self.amount=QDoubleSpinBox(decimals=2,maximum=999999,value=10.00); g.addWidget(QLabel("Bill ₪"),r,0); g.addWidget(self.amount,r,1)
        self.currency=QComboBox(); self.currency.addItems(["376 NIS","840 USD","978 EUR"]); g.addWidget(QLabel("Curr"),r,2); g.addWidget(self.currency,r,3); r+=1
        self.cash_chk=QCheckBox("Cash?"); self.cash_amt=QDoubleSpinBox(decimals=2,maximum=999999); self.cash_amt.setEnabled(False)
        g.addWidget(self.cash_chk,r,0); g.addWidget(self.cash_amt,r,1); r+=1; self.cash_chk.toggled.connect(self.cash_amt.setEnabled)
        self.fx_chk=QCheckBox("Convert FX"); self.fx_to=QComboBox(); self.fx_to.addItems(["376 NIS","840 USD","978 EUR"]); self.fx_to.setEnabled(False)
        self.fx_amt=QDoubleSpinBox(decimals=2,maximum=999999); self.fx_amt.setEnabled(False)
        g.addWidget(self.fx_chk,r,0); g.addWidget(QLabel("to"),r,2); g.addWidget(self.fx_to,r,3); r+=1
        g.addWidget(QLabel("Conv amt"),r,2); g.addWidget(self.fx_amt,r,3); r+=1
        self.fx_chk.toggled.connect(lambda b:[w.setEnabled(b) for w in (self.fx_to,self.fx_amt)])
        self.gen_token=QComboBox(); self.gen_token.addItems(["0 None","1 All cards","2 Shufersal"])
        g.addWidget(QLabel("Gen token"),r,0); g.addWidget(self.gen_token,r,1); r+=1
        self.use_tok=QCheckBox("Use token"); self.tok_val=QLineEdit(); self.tok_val.setEnabled(False)
        g.addWidget(self.use_tok,r,0); g.addWidget(self.tok_val,r,1); r+=1; self.use_tok.toggled.connect(self.tok_val.setEnabled)
        self.service=QComboBox(); self.service.addItems(["","1","2","3"]); g.addWidget(QLabel("Service type"),r,0); g.addWidget(self.service,r,1); r+=1
        self.ctls=QCheckBox("CTLS"); self.ctls.setChecked(True); self.allow=QCheckBox("Allow cancel"); self.allow.setChecked(True); self.unatt=QCheckBox("Unattended")
        g.addWidget(self.ctls,r,0); g.addWidget(self.allow,r,1); g.addWidget(self.unatt,r,2); r+=1
        self.op=QComboBox(); self.op.addItems(["03 Inquiry","04 Execute","05 Authorize","06 Capture"])
        g.addWidget(QLabel("Operation"),r,0); g.addWidget(self.op,r,1); r+=1
        box=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel); box.button(QDialogButtonBox.Ok).setText("Discover")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject); g.addWidget(box,r,0,1,4)
    def data(self)->Dict[str,Optional[str]]:
        return {'timeout':str(self.timeout.value()),'restrict_token':self.gen_token.currentText().split()[0],
                'manual':self.manual.isChecked(),'manual_reason':self.man_reason.currentText() if self.manual.isChecked() else '',
                'tran_type':self.tran_type.currentText().split()[0],'amount':to_minor(self.amount.value()),
                'currency':self.currency.currentText().split()[0],'cash':to_minor(self.cash_amt.value()) if self.cash_chk.isChecked() else None,
                'fx':self.fx_chk.isChecked(),'fx_to':self.fx_to.currentText().split()[0] if self.fx_chk.isChecked() else '',
                'fx_amt':to_minor(self.fx_amt.value()) if self.fx_chk.isChecked() else None,
                'use_token':self.use_tok.isChecked(),'token_val':self.tok_val.text().strip(),'service_type':self.service.currentText(),
                'ctls':self.ctls.isChecked(),'allow_cancel':self.allow.isChecked(),'unattended':self.unatt.isChecked(),
                'operation':self.op.currentText().split()[0]}

# ─────────────── Main Window ───────────────
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 – Quick Sale + Webhook")
        self.resize(1020, 820)
        # state
        self.session=self.ktk=self.mac_key=''
        self.threads:List[QThread]=[]; self.qs_active=False
        self.qs_defaults:Dict[str,str] = {}; self.qs_start_body=''
        # --- UI (condensed) ---
        central=QWidget(); lay=QVBoxLayout(central)
        cr=QHBoxLayout(); self.ip,self.port=QLineEdit("192.168.1.202"),QLineEdit("5015")
        cr.addWidget(QLabel("IP:")); cr.addWidget(self.ip); cr.addWidget(QLabel("Port:")); cr.addWidget(self.port); lay.addLayout(cr)
        rr=QHBoxLayout(); self.chain,self.store,self.lane,self.alt=(QLineEdit("39999"),QLineEdit("0123"),QLineEdit("074"),QLineEdit("012345678"))
        self.pos_type=QComboBox(); self.pos_type.addItems(["ATTENDED","UNATTENDED"])
        for lbl,w in (("Chain",self.chain),("Store",self.store),("Lane",self.lane),("Alt‑ID",self.alt)):
            rr.addWidget(QLabel(lbl)); rr.addWidget(w)
        rr.addWidget(QLabel("POS Type")); rr.addWidget(self.pos_type); self.reg_btn=QPushButton("Register",clicked=self.cmd_register); rr.addWidget(self.reg_btn); lay.addLayout(rr)
        kr=QHBoxLayout(); self.ktk_edit=QLineEdit(); self.ktk_edit.setEnabled(False)
        kr.addWidget(QLabel("KTK (16)")); kr.addWidget(self.ktk_edit)
        self.key_btn=QPushButton("Exchange Keys",clicked=self.cmd_keys); self.key_btn.setEnabled(False); kr.addWidget(self.key_btn); lay.addLayout(kr)
        mr=QHBoxLayout(); self.mac_view=QLineEdit(); self.mac_view.setReadOnly(True); mr.addWidget(QLabel("MAC Key")); mr.addWidget(self.mac_view); lay.addLayout(mr)
        br=QHBoxLayout()
        self.start_btn=QPushButton("Start Tran…",clicked=self.do_start); self.start_btn.setEnabled(False)
        self.disc_btn =QPushButton("Discover…", clicked=self.do_discover); self.disc_btn.setEnabled(False)
        br.addWidget(self.start_btn); br.addWidget(self.disc_btn)
        br.addWidget(QLabel("Amount ₪")); self.qs_amt=QDoubleSpinBox(decimals=2,maximum=999999,value=10.00); br.addWidget(self.qs_amt)
        self.quick_btn=QPushButton("Quick Sale",clicked=lambda: self.quick_sale(self.qs_amt.value(),'01')); self.quick_btn.setEnabled(False); br.addWidget(self.quick_btn)
        br.addWidget(QLabel("Admin")); self.cmd_in=QLineEdit(); self.cmd_send=QPushButton("Send",clicked=self.admin_cmd)
        br.addWidget(self.cmd_in); br.addWidget(self.cmd_send); lay.addLayout(br)
        self.status_btn=QPushButton("Status",clicked=self.cmd_status); self.status_btn.setEnabled(False); lay.addWidget(self.status_btn)
        self.sent,self.recv=QTextEdit(readOnly=True),QTextEdit(readOnly=True); split=QSplitter(Qt.Horizontal); split.addWidget(self.sent); split.addWidget(self.recv); lay.addWidget(split)
        srow=QHBoxLayout(); srow.addWidget(QLabel("Search")); self.search_edit=QLineEdit(); self.search_btn=QPushButton("Find",clicked=self.search_logs)
        self.search_edit.returnPressed.connect(self.search_logs); srow.addWidget(self.search_edit); srow.addWidget(self.search_btn); lay.addLayout(srow)
        lay.addWidget(QPushButton("Clear logs",clicked=lambda:(self.sent.clear(),self.recv.clear())))
        self.setCentralWidget(central)
        self._load_mac()
        # webhook
        self.webhook = WebhookServer(parent=self); self.webhook.receivedPayment.connect(self._from_webhook); self.webhook.start()

    # ---------- persistence ----------
    def _load_mac(self):
        if os.path.exists("mac.txt"):
            self.mac_key=open("mac.txt").read().strip()
            if self.mac_key:
                self.mac_view.setText(self.mac_key)
                for b in (self.status_btn,self.start_btn,self.disc_btn,self.quick_btn): b.setEnabled(True)
    def _save_mac(self): open("mac.txt","w").write(self.mac_key)

    # ---------- XML helpers ----------
    def _env(self, fg:str, cmd:str, body:str="")->str:
        ts=datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        hdr=(f"<TRANSACTION><FUNCTION_GROUP>{fg}</FUNCTION_GROUP><COMMAND>{cmd}</COMMAND>"
             f"<SESSION_ID>{self.session}</SESSION_ID><TRAINING_MODE>0</TRAINING_MODE>"
             f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>")
        xml=hdr+body+"<MAC></MAC></TRANSACTION>"
        mac=calc_mac(xml, self.mac_key) if self.mac_key else ''
        return hdr+body+f"<MAC>{mac}</MAC></TRANSACTION>"
    def _send(self,xml:str):
        try: port=int(self.port.text())
        except ValueError: return QMessageBox.critical(self,"Port","Invalid port")
        t=SockThread(self.ip.text().strip(),port,xml); t.result.connect(self._handle); t.finished.connect(lambda: self.threads.remove(t))
        self.threads.append(t); t.start()

    # ---------- body builders ----------
    def _disc_body(self,d:Dict[str,Optional[str]])->str:
        x=(f"<TRANSACTION_DETAILS><RESTRICT_TOKEN>{d['restrict_token']}</RESTRICT_TOKEN>"
           f"<MANUAL>{int(d['manual'])}</MANUAL><CTLS>{int(d['ctls'])}</CTLS>"
           f"<ALLOW_CANCEL>{int(d['allow_cancel'])}</ALLOW_CANCEL><UNATTENDED>{int(d['unattended'])}</UNATTENDED>"
           f"<TRAN_TYPE>{d['tran_type']}</TRAN_TYPE><MTI>100</MTI><ENTRY_MODE>04</ENTRY_MODE>"
           f"<TRANSACTION_AMOUNT>{d['amount']}</TRANSACTION_AMOUNT><ORIGINAL_CURRENCY>{d['currency']}</ORIGINAL_CURRENCY>")
        if d['manual'] and d['manual_reason']: x+=f"<MANUAL_REASON>{d['manual_reason']}</MANUAL_REASON>"
        if d['cash'] is not None: x+=f"<CASH_AMOUNT>{d['cash']}</CASH_AMOUNT>"
        if d['fx']: x+=f"<CONVERTED_AMOUNT>{d['fx_amt']}</CONVERTED_AMOUNT><CONVERTED_CURRENCY>{d['fx_to']}</CONVERTED_CURRENCY>"
        if d['use_token'] and d['token_val']: x+=f"<CARD_TOKEN>{d['token_val']}</CARD_TOKEN>"
        if d['service_type']: x+=f"<SERVICE_TYPE>{d['service_type']}</SERVICE_TYPE>"
        x+=f"<OPERATION>{d['operation']}</OPERATION></TRANSACTION_DETAILS>"
        return f"<TIMEOUT>{d['timeout']}</TIMEOUT>"+x
    def _auth_body(self, base:Dict[str,str], ct:str, pay:int, first:float, nxt:float)->str:
        x=self._disc_body(base)
        inj=f"<CREDIT_TERMS>{ct}</CREDIT_TERMS>"
        if pay: inj+=f"<PAYMENTS_NUMBER>{pay:02d}</PAYMENTS_NUMBER>"
        if first: inj+=f"<FIRST_PAYMENT_AMOUNT>{to_minor(first)}</FIRST_PAYMENT_AMOUNT>"
        if nxt:   inj+=f"<NEXT_PAYMENT_AMOUNT>{to_minor(nxt)}</NEXT_PAYMENT_AMOUNT>"
        return x.replace('</TRANSACTION_DETAILS>', inj+'</TRANSACTION_DETAILS>')

    # ---------- slots ----------
    def cmd_register(self):
        self.session=rand_session()
        body=(f"<CHAIN>{self.chain.text()}</CHAIN><STORE>{self.store.text()}</STORE><LANE>{self.lane.text()}</LANE>"
              f"<TERMINAL_ID>{self.alt.text()}</TERMINAL_ID><POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>")
        self._send(self._env("ADMIN","REGISTER",body))
    def cmd_keys(self):
        self.ktk=self.ktk_edit.text().strip(); self._send(self._env("ADMIN","EXCHANGE_KEYS",f"<KTK>{self.ktk}</KTK>"))
    def cmd_status(self): self._send(self._env("ADMIN","STATUS"))
    def do_start(self):
        dlg=StartTransactionDlg(self); 
        if dlg.exec_()!=QDialog.Accepted: return
        body=''.join(f"<{k}>{v}</{k}>" for k,v in dlg.data().items()); self._send(self._env("SESSION","START_TRAN",body))
    def do_discover(self):
        dlg=DiscoverDlg(self)
        if dlg.exec_()!=QDialog.Accepted: return
        self._send(self._env("PAYMENT","DISCOVERY",self._disc_body(dlg.data())))
    # quick‑sale entry
    def quick_sale(self, amount:float, tcode:str='01'):
        if self.qs_active: return QMessageBox.warning(self,"Quick Sale","Already running.")
        self.qs_amt.setValue(amount)
        self.qs_defaults={'timeout':'60','restrict_token':'0','manual':False,'manual_reason':'','tran_type':tcode,
                          'amount':to_minor(amount),'currency':'376','cash':None,'fx':False,'fx_to':'','fx_amt':None,
                          'use_token':False,'token_val':'','service_type':'','ctls':True,'allow_cancel':True,
                          'unattended':False,'operation':'04'}
        self.qs_start_body=f"<INVOICE>100000</INVOICE><POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>"
        self.qs_active=True; self._send(self._env("SESSION","START_TRAN",self.qs_start_body))
    @pyqtSlot(float,str)
    def _from_webhook(self, amt:float, tcode:str): self.quick_sale(amt,tcode)
    def admin_cmd(self):
        cmd=self.cmd_in.text().strip().upper()
        if cmd: self._send(self._env("ADMIN",cmd))
    def search_logs(self):
        term=self.search_edit.text()
        for pane in (self.sent,self.recv):
            pane.setExtraSelections([])
            if not term: continue
            sels,doc=[],pane.document(); cur=QTextCursor(doc)
            while True:
                cur=doc.find(term,cur)
                if cur.isNull(): break
                sel=QTextEdit.ExtraSelection(); sel.cursor=cur
                fmt=QTextCharFormat(); fmt.setBackground(QColor("#ffff66")); sel.format=fmt
                sels.append(sel)
            pane.setExtraSelections(sels)

    # ---------- device response FSM ----------
    def _handle(self, sent:str, recv:str):
        self.sent.append(sent); self.recv.append(recv)
        if self.qs_active:
            if '<COMMAND>START_TRAN' in sent:
                if '<RESULT_CODE>0<' in recv:
                    self._send(self._env("PAYMENT","DISCOVERY",self._disc_body(self.qs_defaults)))
                else: self.qs_active=False
                return
            if '<COMMAND>DISCOVERY' in sent:
                if '<RESULT_CODE>0<' not in recv: self.qs_active=False; return
                f=lambda n: bool(re.search(fr'<TERMS_{n.upper()}>1</TERMS_{n.upper()}>',recv,re.I))
                flags={k:f(k) for k in ('regular','special','immediate','credit','installments')}
                credit_ok=flags['credit'] or flags['installments']
                if credit_ok:
                    it=lambda tag,d: int(re.search(fr'<{tag}>(\d+)',recv).group(1)) if re.search(fr'<{tag}>(\d+)',recv) else d
                    dlg=CreditTermDlg(flags,max(2,it('CREDIT_MIN_PAYMENTS',2)),max(2,it('CREDIT_MAX_PAYMENTS',36)),self.qs_amt.value(),self)
                    if dlg.exec_()!=QDialog.Accepted: self.qs_active=False; QMessageBox.information(self,"Quick Sale","Cancelled"); return
                    ct,pay,first,nxt=dlg.data()
                else: ct,pay,first,nxt='1',0,0.0,0.0
                self._send(self._env("PAYMENT","AUTHORIZE",self._auth_body(self.qs_defaults,ct,pay,first,nxt))); return
            if '<COMMAND>AUTHORIZE' in sent:
                if '<RESULT_CODE>0<' in recv:
                    if m:=re.search(r'<RECEIPT_ARR>(.*?)</RECEIPT_ARR>',recv,re.S):
                        QMessageBox.information(self,"Receipt",html.unescape(m.group(1).strip()))
                self._send(self._env("SESSION","FINISH_TRAN")); return
            if '<COMMAND>FINISH_TRAN' in sent: self.qs_active=False; return
        # registration & key exchange
        if '<COMMAND>REGISTER' in sent and '<EVENT>COMPLETED' in recv:
            self.ktk_edit.setEnabled(True); self.key_btn.setEnabled(True)
        if '<COMMAND>EXCHANGE_KEYS' in sent and '<MAC_KEY>' in recv:
            if (m:=re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>',recv)):
                try:
                    self.mac_key=des3_decrypt(self.ktk,m.group(1)); self.mac_view.setText(self.mac_key); self._save_mac()
                    for b in (self.status_btn,self.start_btn,self.disc_btn,self.quick_btn): b.setEnabled(True)
                except Exception as e:
                    QMessageBox.critical(self,"Decrypt",str(e))

    def closeEvent(self,ev):
        self.webhook.terminate()
        for t in self.threads: t.quit(); t.wait()
        ev.accept()

# ─────────────── run ───────────────
if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling,True)
    app = QApplication(sys.argv)
    Main().show()
    sys.exit(app.exec_())
