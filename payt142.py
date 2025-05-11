#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Verifone P400 connector – Quick-Sale flow, webhook trigger & Hebrew receipts
# 2025-05-12  v1.42  (UI state management for transactions)
#
# • RESULT_CODE 2  → 2-second GENERAL/CANCEL  → SESSION/FINISH_TRAN
# • CANCEL result codes 0 / 50 / 51 are considered success
# • Quick-Sale allocates a random 16-digit SESSION_ID when none exists
# • Every successful PAYMENT/AUTHORIZE (RESULT_CODE 0) writes a UTF-8
#   Hebrew receipt in ./receipts/‎YYMMDD_HHMMSS_receipt.txt
# • New: check “Training mode” to send <TRAINING_MODE>1</TRAINING_MODE>
# • UI buttons are disabled during multi-step transactions and re-enabled on FINISH_TRAN.

import sys, os, socket, random, re, html
from datetime   import datetime, timezone
from base64     import b64decode, b64encode
from typing     import Dict, List, Optional, Tuple

from PyQt5.QtCore    import Qt, QThread, pyqtSignal, pyqtSlot, QDate, QTimer
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QCheckBox,
    QGridLayout, QVBoxLayout, QHBoxLayout, QSplitter, QMessageBox,
    QDialogButtonBox
)
from PyQt5.QtGui     import QTextCursor, QTextCharFormat, QColor
from Crypto.Cipher   import DES3
from Crypto.Hash     import SHA256
from http.server     import HTTPServer, BaseHTTPRequestHandler
from urllib.parse    import urlparse, parse_qs

# ───────────────────── helpers ─────────────────────
VALID_TYPES = {'01','02','03','06','30','53','55'}
rand_session = lambda: ''.join(random.choice('0123456789') for _ in range(16))
to_minor     = lambda v: str(int(round(v*100)))
calc_mac     = lambda xml,key: b64encode(SHA256.new((xml+key).encode()).digest()).decode()

def des3_decrypt(ktk: str, mac_b64: str) -> str:
    if len(ktk) != 16:
        raise ValueError("KTK must be 16 ASCII chars")
    key24 = DES3.adjust_key_parity(ktk.encode() + ktk.encode()[:8])
    return DES3.new(key24, DES3.MODE_ECB).decrypt(b64decode(mac_b64)).rstrip(b'\0').decode()

# ───────────────────── threaded I/O ─────────────────────
class SockThread(QThread):
    result = pyqtSignal(str, str)           # sent-xml, recv-xml
    def __init__(self, ip:str, port:int, msg:str):
        super().__init__(); self.ip,self.port,self.msg = ip,port,msg
    def run(self):
        sent, recv = self.msg, ''
        try:
            with socket.create_connection((self.ip,self.port),timeout=7) as s:
                s.sendall(sent.encode())
                chunks=[]
                while True:
                    try: buf=s.recv(4096)
                    except socket.timeout: break
                    if not buf: break
                    chunks.append(buf)
                recv=b''.join(chunks).decode('utf-8','replace')
        except Exception as e:
            recv=f"Error: {e}"
        self.result.emit(sent, recv)

class WebhookServer(QThread):
    receivedPayment = pyqtSignal(float, str)   # amount, type
    def __init__(self, host='localhost', port=8080, parent=None):
        super().__init__(parent); self.host,self.port = host,port
    def run(self):
        outer=self
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                p=urlparse(self.path)
                if p.path!='/pay': return self._r(404,b"Not Found")
                q=parse_qs(p.query)
                if 'amount' not in q: return self._r(400,b"amount param required")
                try: amt=float(q['amount'][0]); assert amt>0
                except Exception: return self._r(400,b"amount must be positive")
                code=q.get('type',['01'])[0]
                if code not in VALID_TYPES: return self._r(400,b"invalid type")
                self._r(200,b"OK"); outer.receivedPayment.emit(amt,code)
            def log_message(self,*_): return
            def _r(self,c,b):
                self.send_response(c); self.send_header("Content-Type","text/plain")
                self.send_header("Content-Length",str(len(b))); self.end_headers(); self.wfile.write(b)
        srv=HTTPServer((self.host,self.port),H); srv.allow_reuse_address=True
        try: srv.serve_forever()
        finally: srv.server_close()

# ───────────────────── dialogs ─────────────────────
class CreditTermDlg(QDialog):
    _map = {'01 רגילה':'1','02 דחויה(+30)':'2','03 מיידית':'3',
            '06 אשראי':'6','08 תשלומים':'8'}
    def __init__(self, flags:Dict[str,bool], mn:int, mx:int, total:float, parent=None):
        super().__init__(parent); self.total=total; self.setWindowTitle("בחירת אשראי / תשלומים")
        g=QGridLayout(self); r=0
        g.addWidget(QLabel("אפשרויות נתמכות:"),r,0,1,2); r+=1
        self.cmb=QComboBox()
        for lbl,code in self._map.items():
            field={'1':'regular','2':'special','3':'immediate','6':'credit','8':'installments'}[code]
            self.cmb.addItem(lbl,code)
            self.cmb.model().item(self.cmb.count()-1).setEnabled(flags.get(field,False))
        g.addWidget(self.cmb,r,0,1,2); r+=1
        self.pay_lbl,self.pay_spin = QLabel("מס׳ תשלומים:"), QSpinBox(minimum=mn,maximum=mx,value=mn)
        g.addWidget(self.pay_lbl,r,0); g.addWidget(self.pay_spin,r,1); r+=1
        self.first_lbl,self.first_spin = QLabel("תשלום ראשון ₪:"), QDoubleSpinBox(decimals=2,maximum=total,value=round(total/mn,2) if mn > 0 else total)
        self.next_lbl,self.next_spin  = QLabel("תשלום המשך ₪:"), QDoubleSpinBox(decimals=2,maximum=total,value=self.first_spin.value())
        self.next_spin.setReadOnly(True)
        g.addWidget(self.first_lbl,r,0); g.addWidget(self.first_spin,r,1); r+=1
        g.addWidget(self.next_lbl,r,0); g.addWidget(self.next_spin,r,1); r+=1
        self.cmb.currentIndexChanged.connect(self._toggle)
        self.pay_spin.valueChanged.connect(self._recalc)
        self.first_spin.valueChanged.connect(self._recalc)
        self._toggle()
        box=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box,r,0,1,2)
    def _toggle(self,*_):
        vis=self.cmb.currentData() in ('6','8')
        for w in (self.pay_lbl,self.pay_spin,self.first_lbl,self.first_spin,
                  self.next_lbl,self.next_spin): w.setVisible(vis)
        self._recalc()
    def _recalc(self,*_):
        if not self.first_spin.isVisible(): return
        num_payments = self.pay_spin.value()
        first_payment = self.first_spin.value()
        remaining_amount = max(0.0, self.total - first_payment)
        remaining_payments = max(1, num_payments - 1)
        next_payment = round(remaining_amount / remaining_payments, 2) if remaining_payments > 0 else 0.0
        self.next_spin.blockSignals(True); self.next_spin.setValue(next_payment); self.next_spin.blockSignals(False)

    def data(self)->Tuple[str,int,float,float]:
        c=self.cmb.currentData()
        if c in ('6','8'): return c,self.pay_spin.value(),self.first_spin.value(),self.next_spin.value()
        return c,0,0.0,0.0

class StartTransactionDlg(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent); self.setWindowTitle("Start Transaction"); g=QGridLayout(self)
        self.inv,self.cash=QLineEdit("100000"),QLineEdit()
        self.shift=QSpinBox(minimum=0,maximum=9999,value=1)
        self.date=QDateEdit(calendarPopup=True); self.date.setDate(QDate.currentDate())
        self.pos_ip,self.pos_port=QLineEdit(),QLineEdit()
        for i,(lbl,w) in enumerate((
            ("Invoice",self.inv),("Cashier",self.cash),("Shift ID",self.shift),
            ("Business Date",self.date),("POS IP",self.pos_ip),("POS Port",self.pos_port))):
            g.addWidget(QLabel(lbl),i,0); g.addWidget(w,i,1)
        g.addWidget(QLabel("* optional"),6,0,1,2)
        box=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Start")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box,7,0,1,2)
    def data(self)->Dict[str,str]:
        d={'INVOICE':self.inv.text(),'CASHIER_ID':self.cash.text(),
           'SHIFT_ID':str(self.shift.value()) if self.shift.value() else '',
           'BUSINESSDATE':self.date.date().toString("yyyyMMdd"),
           'POS_IP':self.pos_ip.text(),'POS_PORT':self.pos_port.text()}
        return {k:v for k,v in d.items() if v}

class DiscoverDlg(QDialog):
    def __init__(self,parent=None):
        super().__init__(parent); self.setWindowTitle("Discover – Read Card"); g=QGridLayout(self); r=0
        self.timeout=QSpinBox(minimum=5,maximum=999,value=60)
        g.addWidget(QLabel("Timeout (s)"),r,0); g.addWidget(self.timeout,r,1); r+=1
        self.manual=QCheckBox("Manual entry")
        g.addWidget(self.manual,r,0)
        self.man_reason=QComboBox(); self.man_reason.addItems(["SIG","CNP"]); self.man_reason.setEnabled(False)
        g.addWidget(self.man_reason,r,1); r+=1
        self.manual.toggled.connect(self.man_reason.setEnabled)
        self.tran_type=QComboBox(); self.tran_type.addItems(
            ["01 Regular","02 Unloading","03 Forced","06 Cashback","30 Balance","53 Refund","55 Loading"])
        g.addWidget(QLabel("Tran. type"),r,0); g.addWidget(self.tran_type,r,1); r+=1
        self.amount=QDoubleSpinBox(decimals=2,maximum=999999,value=10.00)
        g.addWidget(QLabel("Bill ₪"),r,0); g.addWidget(self.amount,r,1)
        self.currency=QComboBox(); self.currency.addItems(["376 NIS","840 USD","978 EUR"])
        g.addWidget(QLabel("Curr"),r,2); g.addWidget(self.currency,r,3); r+=1
        self.cash_chk=QCheckBox("Cash?"); self.cash_amt=QDoubleSpinBox(decimals=2,maximum=999999); self.cash_amt.setEnabled(False)
        g.addWidget(self.cash_chk,r,0); g.addWidget(self.cash_amt,r,1); r+=1
        self.cash_chk.toggled.connect(self.cash_amt.setEnabled)
        self.fx_chk=QCheckBox("Convert FX")
        self.fx_to=QComboBox(); self.fx_to.addItems(["376 NIS","840 USD","978 EUR"]); self.fx_to.setEnabled(False)
        self.fx_amt=QDoubleSpinBox(decimals=2,maximum=999999); self.fx_amt.setEnabled(False)
        g.addWidget(self.fx_chk,r,0); g.addWidget(QLabel("to"),r,2); g.addWidget(self.fx_to,r,3); r+=1
        g.addWidget(QLabel("Conv amt"),r,2); g.addWidget(self.fx_amt,r,3); r+=1
        self.fx_chk.toggled.connect(lambda b:[w.setEnabled(b) for w in (self.fx_to,self.fx_amt)])
        self.gen_token=QComboBox(); self.gen_token.addItems(["0 None","1 All cards","2 Shufersal"])
        g.addWidget(QLabel("Gen token"),r,0); g.addWidget(self.gen_token,r,1); r+=1
        self.use_tok=QCheckBox("Use token"); self.tok_val=QLineEdit(); self.tok_val.setEnabled(False)
        g.addWidget(self.use_tok,r,0); g.addWidget(self.tok_val,r,1); r+=1
        self.use_tok.toggled.connect(self.tok_val.setEnabled)
        self.service=QComboBox(); self.service.addItems(["","1","2","3"])
        g.addWidget(QLabel("Service type"),r,0); g.addWidget(self.service,r,1); r+=1
        self.ctls=QCheckBox("CTLS"); self.ctls.setChecked(True)
        self.allow=QCheckBox("Allow cancel"); self.allow.setChecked(True)
        self.unatt=QCheckBox("Unattended")
        g.addWidget(self.ctls,r,0); g.addWidget(self.allow,r,1); g.addWidget(self.unatt,r,2); r+=1
        self.op=QComboBox(); self.op.addItems(["03 Inquiry","04 Execute","05 Authorize","06 Capture"])
        g.addWidget(QLabel("Operation"),r,0); g.addWidget(self.op,r,1); r+=1
        box=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Discover")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box,r,0,1,4)
    def data(self)->Dict[str,Optional[str]]:
        return {'timeout':str(self.timeout.value()),
                'restrict_token':self.gen_token.currentText().split()[0],
                'manual':self.manual.isChecked(),
                'manual_reason':self.man_reason.currentText() if self.manual.isChecked() else '',
                'tran_type':self.tran_type.currentText().split()[0],
                'amount':to_minor(self.amount.value()),
                'currency':self.currency.currentText().split()[0],
                'cash':to_minor(self.cash_amt.value()) if self.cash_chk.isChecked() else None,
                'fx':self.fx_chk.isChecked(),
                'fx_to':self.fx_to.currentText().split()[0] if self.fx_chk.isChecked() else '',
                'fx_amt':to_minor(self.fx_amt.value()) if self.fx_chk.isChecked() else None,
                'use_token':self.use_tok.isChecked(),
                'token_val':self.tok_val.text().strip(),
                'service_type':self.service.currentText(),
                'ctls':self.ctls.isChecked(),
                'allow_cancel':self.allow.isChecked(),
                'unattended':self.unatt.isChecked(),
                'operation':self.op.currentText().split()[0]}

# ───────────────────── main window ─────────────────────
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 – Quick Sale + Webhook")
        self.resize(1020,820)

        # state
        self.session=self.ktk=self.mac_key=''
        self.threads:List[QThread]=[]
        self.qs_active=False
        self.qs_defaults:Dict[str,str]={}; self.qs_start_body=''
        self._cancel_timer:Optional[QTimer]=None; self._awaiting_cancel=False

        # UI layout
        central=QWidget(); lay=QVBoxLayout(central)

        # connection + training
        con=QHBoxLayout()
        self.ip,self.port=QLineEdit("192.168.1.202"),QLineEdit("5015")
        self.training_chk=QCheckBox("Training mode")
        for lbl,w in (("IP:",self.ip),("Port:",self.port)):
            con.addWidget(QLabel(lbl)); con.addWidget(w)
        con.addWidget(self.training_chk)
        lay.addLayout(con)

        # register
        rr=QHBoxLayout()
        self.chain,self.store,self.lane,self.alt=(QLineEdit("39999"),QLineEdit("0123"),
                                                  QLineEdit("074"),QLineEdit("012345678"))
        self.pos_type=QComboBox(); self.pos_type.addItems(["ATTENDED","UNATTENDED"])
        for lbl,w in (("Chain",self.chain),("Store",self.store),
                      ("Lane",self.lane),("Alt-ID",self.alt)):
            rr.addWidget(QLabel(lbl)); rr.addWidget(w)
        rr.addWidget(QLabel("POS Type")); rr.addWidget(self.pos_type)
        self.reg_btn=QPushButton("Register",clicked=self.cmd_register); rr.addWidget(self.reg_btn)
        lay.addLayout(rr)

        # key exchange
        kr=QHBoxLayout(); self.ktk_edit=QLineEdit(); self.ktk_edit.setEnabled(False)
        kr.addWidget(QLabel("KTK (16)")); kr.addWidget(self.ktk_edit)
        self.key_btn=QPushButton("Exchange Keys",clicked=self.cmd_keys); self.key_btn.setEnabled(False)
        kr.addWidget(self.key_btn); lay.addLayout(kr)

        # MAC
        macr=QHBoxLayout(); self.mac_view=QLineEdit(); self.mac_view.setReadOnly(True)
        macr.addWidget(QLabel("MAC Key")); macr.addWidget(self.mac_view); lay.addLayout(macr)

        # action buttons
        br=QHBoxLayout()
        self.start_btn=QPushButton("Start Tran…",clicked=self.do_start); self.start_btn.setEnabled(False)
        self.disc_btn=QPushButton("Discover…",clicked=self.do_discover); self.disc_btn.setEnabled(False)
        br.addWidget(self.start_btn); br.addWidget(self.disc_btn)
        br.addWidget(QLabel("Amount ₪")); self.qs_amt=QDoubleSpinBox(decimals=2,maximum=999999,value=10.00); br.addWidget(self.qs_amt)
        self.quick_btn=QPushButton("Quick Sale",clicked=lambda:self.quick_sale(self.qs_amt.value(),'01'))
        self.quick_btn.setEnabled(False); br.addWidget(self.quick_btn)
        br.addWidget(QLabel("Admin")); self.cmd_in=QLineEdit()
        self.cmd_send=QPushButton("Send",clicked=self.admin_cmd)
        br.addWidget(self.cmd_in); br.addWidget(self.cmd_send)
        lay.addLayout(br)

        self.status_btn=QPushButton("Status",clicked=self.cmd_status); self.status_btn.setEnabled(False)
        lay.addWidget(self.status_btn)

        # logs
        self.sent,self.recv=QTextEdit(readOnly=True),QTextEdit(readOnly=True)
        split=QSplitter(Qt.Horizontal); split.addWidget(self.sent); split.addWidget(self.recv); lay.addWidget(split)

        # search
        srow=QHBoxLayout(); srow.addWidget(QLabel("Search"))
        self.search_edit=QLineEdit(); self.search_btn=QPushButton("Find",clicked=self.search_logs)
        self.search_edit.returnPressed.connect(self.search_logs)
        srow.addWidget(self.search_edit); srow.addWidget(self.search_btn); lay.addLayout(srow)
        lay.addWidget(QPushButton("Clear logs",clicked=lambda:(self.sent.clear(),self.recv.clear())))

        self.setCentralWidget(central)

        self._load_mac()
        self.webhook=WebhookServer(parent=self); self.webhook.receivedPayment.connect(self._from_webhook); self.webhook.start()

    # --- UI Enable/Disable Helper ---
    def _set_transaction_buttons_enabled(self, enabled: bool):
        """Enables or disables main transaction initiating buttons."""
        if not self.mac_key: # Always disabled if no MAC key
            enabled = False
        self.start_btn.setEnabled(enabled)
        self.disc_btn.setEnabled(enabled)
        self.quick_btn.setEnabled(enabled)
        # self.status_btn.setEnabled(enabled) # Status can often be an exception or handled separately

    # ── persistence ──
    def _load_mac(self):
        if os.path.exists("mac.txt"):
            self.mac_key=open("mac.txt").read().strip()
            if self.mac_key:
                self.mac_view.setText(self.mac_key)
                self._set_transaction_buttons_enabled(True)
                self.status_btn.setEnabled(True)
            else:
                self._set_transaction_buttons_enabled(False)
                self.status_btn.setEnabled(False)
        else:
            self._set_transaction_buttons_enabled(False)
            self.status_btn.setEnabled(False)

    def _save_mac(self): open("mac.txt","w",encoding='utf-8').write(self.mac_key)

    # ── XML envelope ──
    def _env(self, fg:str, cmd:str, body:str="")->str:
        ts=datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        training='1' if self.training_chk.isChecked() else '0'
        hdr=(f"<TRANSACTION><FUNCTION_GROUP>{fg}</FUNCTION_GROUP><COMMAND>{cmd}</COMMAND>"
             f"<SESSION_ID>{self.session}</SESSION_ID><TRAINING_MODE>{training}</TRAINING_MODE>"
             f"<TRANSACTION_TIME>{ts}</TRANSACTION_TIME>")
        xml=hdr+body+"<MAC></MAC></TRANSACTION>"
        mac=calc_mac(xml,self.mac_key) if self.mac_key else ''
        return hdr+body+f"<MAC>{mac}</MAC></TRANSACTION>"

    def _send(self, xml:str):
        try: port=int(self.port.text())
        except ValueError: return QMessageBox.critical(self,"Port","Invalid port")
        t=SockThread(self.ip.text().strip(),port,xml)
        t.result.connect(self._handle); t.finished.connect(lambda:self.threads.remove(t))
        self.threads.append(t); t.start()

    # ── body builders ──
    def _disc_body(self,d:Dict[str,Optional[str]])->str:
        x=(f"<TRANSACTION_DETAILS><RESTRICT_TOKEN>{d['restrict_token']}</RESTRICT_TOKEN>"
           f"<MANUAL>{int(d['manual'])}</MANUAL><CTLS>{int(d['ctls'])}</CTLS>"
           f"<ALLOW_CANCEL>{int(d['allow_cancel'])}</ALLOW_CANCEL><UNATTENDED>{int(d['unattended'])}</UNATTENDED>"
           f"<TRAN_TYPE>{d['tran_type']}</TRAN_TYPE><MTI>100</MTI><ENTRY_MODE>04</ENTRY_MODE>"
           f"<TRANSACTION_AMOUNT>{d['amount']}</TRANSACTION_AMOUNT><ORIGINAL_CURRENCY>{d['currency']}</ORIGINAL_CURRENCY>")
        if d['manual'] and d['manual_reason']: x+=f"<MANUAL_REASON>{d['manual_reason']}</MANUAL_REASON>"
        if d['cash'] is not None:             x+=f"<CASH_AMOUNT>{d['cash']}</CASH_AMOUNT>"
        if d['fx']:                           x+=(f"<CONVERTED_AMOUNT>{d['fx_amt']}</CONVERTED_AMOUNT>"
                                                  f"<CONVERTED_CURRENCY>{d['fx_to']}</CONVERTED_CURRENCY>")
        if d['use_token'] and d['token_val']: x+=f"<CARD_TOKEN>{d['token_val']}</CARD_TOKEN>"
        if d['service_type']:                 x+=f"<SERVICE_TYPE>{d['service_type']}</SERVICE_TYPE>"
        x+=f"<OPERATION>{d['operation']}</OPERATION></TRANSACTION_DETAILS>"
        return f"<TIMEOUT>{d['timeout']}</TIMEOUT>"+x
    def _auth_body(self,base:Dict[str,str],ct:str,pay:int,first:float,nxt:float)->str:
        x=self._disc_body(base)
        inj=f"<CREDIT_TERMS>{ct}</CREDIT_TERMS>"
        if pay:   inj+=f"<PAYMENTS_NUMBER>{pay:02d}</PAYMENTS_NUMBER>"
        if first: inj+=f"<FIRST_PAYMENT_AMOUNT>{to_minor(first)}</FIRST_PAYMENT_AMOUNT>"
        if nxt:   inj+=f"<NEXT_PAYMENT_AMOUNT>{to_minor(nxt)}</NEXT_PAYMENT_AMOUNT>"
        return x.replace('</TRANSACTION_DETAILS>',inj+'</TRANSACTION_DETAILS>')

    # ── receipt writer ──
    def _save_receipt(self, xml:str):
        tag=lambda t:(re.search(fr'<{t}>([^<]*)',xml) or ['',None])[1]
        ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        amount=int(tag('TRANSACTION_AMOUNT') or 0)/100
        curr='₪' if tag('ORIGINAL_CURRENCY')=='376' else tag('ORIGINAL_CURRENCY') or ''
        result=tag('POS_TEXT') or tag('RESULT_TEXT') or ''
        lines=["קבלה – Verifone P400",
               "====================",
               f"תאריך/שעה    : {ts}",
               f"סכום         : {amount:.2f} {curr}",
               f"תוצאה        : {result}",
               "",
               "כרטיס",
               "-----",
               f"מספר מוסתר   : {tag('MASKED_PAN') or 'N/A'}",
               f"מותג / מנפיק : {tag('BRAND') or 'N/A'} / {tag('ISSUER') or 'N/A'}",
               f"תוקף         : {tag('CARD_EXPIRY') or 'N/A'}",
               "",
               "עסקה",
               "-----",
               f"סוג           : {tag('TRAN_TYPE') or ''}",
               f"מס׳ אישור     : {tag('ISSUER_AUTH_NUM') or tag('AQUIRER_AUTH_NUM') or 'N/A'}",
               f"מזהה עסקה    : {tag('TRANS_ID') or 'N/A'}",
               f"אסמכתא (RRN) : {tag('RRN') or 'N/A'}",
               ""]
        if m:=re.search(r'<RECEIPT_ARR>(.*?)</RECEIPT_ARR>',xml,re.S):
            for item in re.findall(r'<RECEIPT_ARR_ITEM>(.*?)</RECEIPT_ARR_ITEM>',m.group(1),re.S):
                t_match=re.search(r'<HEBREW_TITLE>([^<]*)',item)
                v_match=re.search(r'<VALUE>([^<]*)',item)
                if t_match and v_match: lines.append(f"{t_match.group(1)} : {v_match.group(1)}")
        os.makedirs("receipts",exist_ok=True)
        fname=datetime.now().strftime("receipts/%y%m%d_%H%M%S_receipt.txt")
        with open(fname,"w",encoding="utf-8") as f: f.write('\n'.join(lines))
        QMessageBox.information(self,"הקבלה נשמרה",f"קובץ הקבלה נוצר:\n{fname}")

    # ── cancel helpers ──
    def _schedule_cancel(self):
        if self._awaiting_cancel: return
        self._awaiting_cancel=True
        self._cancel_timer=QTimer(self); self._cancel_timer.setSingleShot(True)
        self._cancel_timer.timeout.connect(self._send_cancel); self._cancel_timer.start(2000)
    def _send_cancel(self):
        self._cancel_timer=None
        # No need to check self._awaiting_cancel here again, as _schedule_cancel handles it
        if self.session: # Only send cancel if there's an active session context
            self._send(self._env("GENERAL","CANCEL"))

    # ── buttons / slots ──
    def cmd_register(self):
        self.session=rand_session()
        body=(f"<CHAIN>{self.chain.text()}</CHAIN><STORE>{self.store.text()}</STORE>"
              f"<LANE>{self.lane.text()}</LANE><TERMINAL_ID>{self.alt.text()}</TERMINAL_ID>"
              f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>")
        self._send(self._env("ADMIN","REGISTER",body))
    def cmd_keys(self):
        self.ktk=self.ktk_edit.text().strip()
        if len(self.ktk) != 16:
            QMessageBox.warning(self, "Invalid KTK", "KTK must be 16 characters.")
            return
        self._send(self._env("ADMIN","EXCHANGE_KEYS",f"<KTK>{self.ktk}</KTK>"))
    def cmd_status(self): self._send(self._env("ADMIN","STATUS"))
    def do_start(self):
        self._set_transaction_buttons_enabled(False) # Disable buttons for this flow
        dlg=StartTransactionDlg(self)
        if dlg.exec_()!=QDialog.Accepted:
            self._set_transaction_buttons_enabled(True) # Re-enable if user cancels dialog
            return
        body=''.join(f"<{k}>{v}</{k}>" for k,v in dlg.data().items())
        self.session = rand_session() # Start a new session
        self._send(self._env("SESSION","START_TRAN",body))
    def do_discover(self):
        self._set_transaction_buttons_enabled(False) # Disable buttons for this flow
        dlg=DiscoverDlg(self)
        if dlg.exec_()!=QDialog.Accepted:
            self._set_transaction_buttons_enabled(True) # Re-enable if user cancels dialog
            return
        if not self.session: self.session = rand_session() # Ensure session exists
        self._send(self._env("PAYMENT","DISCOVERY",self._disc_body(dlg.data())))
    def quick_sale(self, amount:float, tcode:str='01'):
        if self.qs_active:
            return QMessageBox.warning(self,"Quick Sale","Already running.")
        if not self.mac_key:
             QMessageBox.warning(self,"Quick Sale","MAC Key not set. Please Register and Exchange Keys first.")
             return

        self._set_transaction_buttons_enabled(False)
        self.session=rand_session() # Always start a new session for Quick Sale

        self.qs_amt.setValue(amount)
        self.qs_defaults={'timeout':'60','restrict_token':'0','manual':False,'manual_reason':'',
                          'tran_type':tcode,'amount':to_minor(amount),'currency':'376','cash':None,
                          'fx':False,'fx_to':'','fx_amt':None,'use_token':False,'token_val':'',
                          'service_type':'','ctls':True,'allow_cancel':True,'unattended':False,'operation':'04'}
        self.qs_start_body="<INVOICE>100000</INVOICE><POS_TYPE>"+self.pos_type.currentText()+"</POS_TYPE>" # Example invoice
        self.qs_active=True; self._send(self._env("SESSION","START_TRAN",self.qs_start_body))
    @pyqtSlot(float,str)
    def _from_webhook(self, amt:float, tcode:str): self.quick_sale(amt,tcode)
    def admin_cmd(self):
        cmd=self.cmd_in.text().strip().upper()
        if cmd:
            if not self.session and cmd not in ("REGISTER", "EXCHANGE_KEYS", "STATUS", "PING"): # PING might be MAC-less too
                self.session = rand_session()
            self._send(self._env("ADMIN",cmd))

    def search_logs(self):
        term=self.search_edit.text()
        for pane in (self.sent,self.recv):
            pane.setExtraSelections([]); sels=[]
            if not term: continue
            doc=pane.document(); cur=QTextCursor(doc)
            fmt_highlight=QTextCharFormat(); fmt_highlight.setBackground(QColor("#ffff66"))
            # Reset cursor to the beginning for each search to find all occurrences
            cur.movePosition(QTextCursor.Start)
            while True:
                cur=doc.find(term,cur)
                if cur.isNull(): break
                sel=QTextEdit.ExtraSelection(); sel.cursor=cur
                sel.format=fmt_highlight; sels.append(sel)
            pane.setExtraSelections(sels)

    # ── FSM / response handler ──
    def _handle(self, sent:str, recv:str):
        self.sent.append(html.escape(sent)); self.recv.append(html.escape(recv))

        # auto-cancel on RESULT_CODE 2
        if '<RESULT_CODE>2<' in recv and '<COMMAND>CANCEL' not in sent and not self._awaiting_cancel:
            if self.qs_active or '<COMMAND>START_TRAN' in sent or '<COMMAND>DISCOVERY' in sent : # Only for ongoing transactions
                self._schedule_cancel()
            return

        if self.qs_active:
            if '<COMMAND>START_TRAN' in sent:
                if '<RESULT_CODE>0<' in recv:
                    self._send(self._env("PAYMENT","DISCOVERY",self._disc_body(self.qs_defaults)))
                else: # START_TRAN failed
                    self.qs_active=False
                    self._set_transaction_buttons_enabled(True)
                return
            if '<COMMAND>DISCOVERY' in sent:
                if '<RESULT_CODE>0<' not in recv: # DISCOVERY failed
                    self.qs_active=False
                    self._set_transaction_buttons_enabled(True)
                    self._send(self._env("SESSION", "FINISH_TRAN")) # Try to clean up session
                    return

                f=lambda n:bool(re.search(fr'<TERMS_{n.upper()}>1</TERMS_{n.upper()}>',recv,re.I))
                flags={k:f(k) for k in ('regular','special','immediate','credit','installments')}
                min_pays = 2 # Default
                max_pays = 36 # Default
                if m_min := re.search(r'<MIN_PAYMENTS>(\d+)</MIN_PAYMENTS>', recv): min_pays = int(m_min.group(1))
                if m_max := re.search(r'<MAX_PAYMENTS>(\d+)</MAX_PAYMENTS>', recv): max_pays = int(m_max.group(1))

                dlg=CreditTermDlg(flags,min_pays, max_pays, self.qs_amt.value(),self)

                if dlg.exec_()!=QDialog.Accepted:
                    self.qs_active=False
                    self._set_transaction_buttons_enabled(True)
                    QMessageBox.information(self,"Quick Sale","בוטל")
                    self._send(self._env("GENERAL", "CANCEL")) # This will trigger FINISH_TRAN later
                    return
                ct,pay,first,nxt=dlg.data()
                self._send(self._env("PAYMENT","AUTHORIZE",self._auth_body(self.qs_defaults,ct,pay,first,nxt))); return
            if '<COMMAND>AUTHORIZE' in sent:
                if '<RESULT_CODE>0<' in recv:
                    self._save_receipt(recv)
                # Always send FINISH_TRAN to clean up the session.
                # qs_active and button state will be handled by FINISH_TRAN response.
                self._send(self._env("SESSION","FINISH_TRAN"))
                return

        # Handle START_TRAN or DISCOVERY outside Quick Sale (if you implement such flows)
        if '<COMMAND>START_TRAN' in sent and not self.qs_active:
            if '<RESULT_CODE>0<' not in recv:
                 self._set_transaction_buttons_enabled(True) # Re-enable if general START_TRAN fails
            # else: waiting for next step in the custom flow
            return
        if '<COMMAND>DISCOVERY' in sent and not self.qs_active:
            if '<RESULT_CODE>0<' not in recv:
                self._set_transaction_buttons_enabled(True) # Re-enable if general DISCOVERY fails
                self._send(self._env("SESSION", "FINISH_TRAN"))
            # else: waiting for next step (e.g. manual Authorize)
            return


        if '<COMMAND>FINISH_TRAN' in sent:
            self.qs_active = False  # Reset quick sale flag
            self._set_transaction_buttons_enabled(True) # Re-enable buttons
            if self._awaiting_cancel :
                self._awaiting_cancel = False
            self.session = '' # Clear session ID after finishing
            return

        if '<COMMAND>CANCEL' in sent:
            code_match=re.search(r'<RESULT_CODE>(\d+)<',recv)
            code = code_match.group(1) if code_match else 'UNKNOWN'
            ok = code in ('0','50','51')
            if not ok:
                QMessageBox.warning(self,"Cancel failed",f"Result code: {code}")
            # _awaiting_cancel is true if this CANCEL was scheduled.
            # If it's a manual CANCEL, _awaiting_cancel might be false.
            # The FINISH_TRAN call below will handle qs_active and button state.
            self._send(self._env("SESSION","FINISH_TRAN")); return

        if '<COMMAND>REGISTER' in sent and '<EVENT>COMPLETED' in recv:
            if '<RESULT_CODE>0<' in recv:
                self.ktk_edit.setEnabled(True); self.key_btn.setEnabled(True)
                QMessageBox.information(self, "Register", "Register POS successful. Please enter KTK.")
            else:
                QMessageBox.critical(self, "Register Failed", f"Could not register POS. Response:\n{recv}")
                self.session = '' # Clear session if register failed
        if '<COMMAND>EXCHANGE_KEYS' in sent:
            if '<RESULT_CODE>0<' in recv and (m:=re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>',recv)):
                try:
                    self.mac_key=des3_decrypt(self.ktk,m.group(1)); self.mac_view.setText(self.mac_key); self._save_mac()
                    self._set_transaction_buttons_enabled(True)
                    self.status_btn.setEnabled(True)
                    QMessageBox.information(self, "Exchange Keys", "MAC Key exchanged and saved successfully.")
                except Exception as exc:
                    QMessageBox.critical(self,"Decrypt",str(exc))
                    self.mac_key = ""; self.mac_view.clear(); self._save_mac() # Clear and save empty
                    self._set_transaction_buttons_enabled(False)
                    self.status_btn.setEnabled(False)
            else:
                QMessageBox.critical(self, "Exchange Keys Failed", f"Could not exchange keys. Response:\n{recv}")
                self.mac_key = ""; self.mac_view.clear(); self._save_mac()
                self._set_transaction_buttons_enabled(False)
                self.status_btn.setEnabled(False)


    # ── cleanup ──
    def closeEvent(self,ev):
        self.webhook.terminate()
        self.webhook.wait()
        for t in self.threads: t.quit(); t.wait()
        ev.accept()

# ───────────────────── run ─────────────────────
if __name__=="__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling,True)
    app=QApplication(sys.argv); Main().show(); sys.exit(app.exec_())