import re
from typing import Dict, Tuple, Optional
from PyQt5.QtCore import QDate
from PyQt5.QtWidgets import (
    QDialog, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QGridLayout, QDialogButtonBox, QCheckBox, QLineEdit, QDateEdit
)
from utils import to_minor
from xml_builder import VALID_TYPES

class CreditTermDlg(QDialog):
    _map = {'01 רגילה':'1','02 דחויה(+30)':'2','03 מיידית':'3',
            '06 אשראי':'6','08 תשלומים':'8'}
    def __init__(self, flags:Dict[str,bool], mn:int, mx:int, total:float, parent=None):
        super().__init__(parent)
        self.total = total
        self.setWindowTitle("בחירת אשראי / תשלומים")
        g = QGridLayout(self); r = 0
        g.addWidget(QLabel("אפשרויות נתמכות:"),r,0,1,2); r+=1
        self.cmb = QComboBox()
        for lbl,code in self._map.items():
            field = {'1':'regular','2':'special','3':'immediate','6':'credit','8':'installments'}[code]
            self.cmb.addItem(lbl, code)
            self.cmb.model().item(self.cmb.count()-1).setEnabled(flags.get(field, False))
        g.addWidget(self.cmb, r,0,1,2); r+=1

        self.pay_lbl, self.pay_spin = QLabel("מס׳ תשלומים:"), QSpinBox()
        self.pay_spin.setRange(mn, mx)
        g.addWidget(self.pay_lbl, r,0); g.addWidget(self.pay_spin, r,1); r+=1

        self.first_lbl, self.first_spin = QLabel("תשלום ראשון ₪:"), QDoubleSpinBox()
        self.first_spin.setDecimals(2); self.first_spin.setMaximum(total); self.first_spin.setValue(total/mn)
        self.next_lbl, self.next_spin = QLabel("תשלום המשך ₪:"), QDoubleSpinBox()
        self.next_spin.setDecimals(2); self.next_spin.setMaximum(total); self.next_spin.setReadOnly(True)

        g.addWidget(self.first_lbl, r,0); g.addWidget(self.first_spin, r,1); r+=1
        g.addWidget(self.next_lbl, r,0); g.addWidget(self.next_spin, r,1); r+=1

        self.cmb.currentIndexChanged.connect(self._toggle)
        self.pay_spin.valueChanged.connect(self._recalc)
        self.first_spin.valueChanged.connect(self._recalc)
        self._toggle()

        box = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box, r,0,1,2)

    def _toggle(self, *_):
        vis = self.cmb.currentData() in ('6','8')
        for w in (self.pay_lbl,self.pay_spin,self.first_lbl,self.first_spin,self.next_lbl,self.next_spin):
            w.setVisible(vis)
        self._recalc()

    def _recalc(self, *_):
        if not self.first_spin.isVisible(): return
        nxt = max(0.0, self.total - self.first_spin.value()) / max(1, self.pay_spin.value() - 1)
        self.next_spin.blockSignals(True)
        self.next_spin.setValue(round(nxt, 2))
        self.next_spin.blockSignals(False)

    def data(self) -> Tuple[str,int,str,str]:
        c = self.cmb.currentData()
        if c in ('6','8'):
            return (
                c,
                self.pay_spin.value(),
                to_minor(self.first_spin.value()),
                to_minor(self.next_spin.value())
            )
        return (c, 0, "", "")


class StartTransactionDlg(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Start Transaction")
        g = QGridLayout(self)
        self.inv = QLineEdit("100000")
        self.cash = QLineEdit()
        self.shift = QSpinBox(); self.shift.setRange(0,9999); self.shift.setValue(1)
        self.date = QDateEdit(calendarPopup=True); self.date.setDate(QDate.currentDate())
        self.pos_ip = QLineEdit(); self.pos_port = QLineEdit()
        for i, (lbl, w) in enumerate([
            ("Invoice",self.inv),
            ("Cashier",self.cash),
            ("Shift ID",self.shift),
            ("Business Date",self.date),
            ("POS IP",self.pos_ip),
            ("POS Port",self.pos_port),
        ]):
            g.addWidget(QLabel(lbl), i,0)
            g.addWidget(w, i,1)
        g.addWidget(QLabel("* optional"), 6,0,1,2)
        box = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Start")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box, 7,0,1,2)

    def data(self) -> Dict[str,str]:
        d = {
            'INVOICE': self.inv.text(),
            'CASHIER_ID': self.cash.text(),
            'SHIFT_ID': str(self.shift.value()) if self.shift.value() else '',
            'BUSINESSDATE': self.date.date().toString("yyyyMMdd"),
            'POS_IP': self.pos_ip.text(),
            'POS_PORT': self.pos_port.text(),
        }
        return {k:v for k,v in d.items() if v}


class DiscoverDlg(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Discover – Read Card")
        g = QGridLayout(self); r = 0

        self.timeout = QSpinBox(); self.timeout.setRange(5,999); self.timeout.setValue(60)
        g.addWidget(QLabel("Timeout (s)"), r,0); g.addWidget(self.timeout, r,1); r+=1

        self.manual = QCheckBox("Manual entry")
        g.addWidget(self.manual, r,0)
        self.man_reason = QComboBox()
        self.man_reason.addItems(["SIG","CNP"])
        self.man_reason.setEnabled(False)
        g.addWidget(self.man_reason, r,1); r+=1
        self.manual.toggled.connect(self.man_reason.setEnabled)

        self.tran_type = QComboBox()
        self.tran_type.addItems([
            "01 Regular","02 Unloading","03 Forced",
            "06 Cashback","30 Balance","53 Refund","55 Loading"
        ])
        g.addWidget(QLabel("Tran. type"), r,0); g.addWidget(self.tran_type, r,1); r+=1

        self.amount = QDoubleSpinBox(); self.amount.setDecimals(2); self.amount.setMaximum(999999); self.amount.setValue(10.00)
        g.addWidget(QLabel("Bill ₪"), r,0); g.addWidget(self.amount, r,1)

        self.currency = QComboBox(); self.currency.addItems(["376 NIS","840 USD","978 EUR"])
        g.addWidget(QLabel("Curr"), r,2); g.addWidget(self.currency, r,3); r+=1

        self.cash_chk = QCheckBox("Cash?")
        self.cash_amt = QDoubleSpinBox(); self.cash_amt.setDecimals(2); self.cash_amt.setMaximum(999999)
        self.cash_amt.setEnabled(False)
        g.addWidget(self.cash_chk, r,0); g.addWidget(self.cash_amt, r,1); r+=1
        self.cash_chk.toggled.connect(self.cash_amt.setEnabled)

        self.fx_chk = QCheckBox("Convert FX")
        self.fx_to = QComboBox(); self.fx_to.addItems(["376 NIS","840 USD","978 EUR"])
        self.fx_amt = QDoubleSpinBox(); self.fx_amt.setDecimals(2); self.fx_amt.setMaximum(999999)
        for w in (self.fx_to, self.fx_amt):
            w.setEnabled(False)
        g.addWidget(self.fx_chk, r,0); g.addWidget(QLabel("to"), r,2); g.addWidget(self.fx_to, r,3); r+=1
        g.addWidget(QLabel("Conv amt"), r,2); g.addWidget(self.fx_amt, r,3); r+=1
        self.fx_chk.toggled.connect(lambda b: [w.setEnabled(b) for w in (self.fx_to,self.fx_amt)])

        self.gen_token = QComboBox()
        self.gen_token.addItems(["0 None","1 All cards","2 Shufersal"])
        g.addWidget(QLabel("Gen token"), r,0); g.addWidget(self.gen_token, r,1); r+=1

        self.use_tok = QCheckBox("Use token")
        self.tok_val = QLineEdit(); self.tok_val.setEnabled(False)
        g.addWidget(self.use_tok, r,0); g.addWidget(self.tok_val, r,1); r+=1
        self.use_tok.toggled.connect(self.tok_val.setEnabled)

        self.service = QComboBox(); self.service.addItems(["","1","2","3"])
        g.addWidget(QLabel("Service type"), r,0); g.addWidget(self.service, r,1); r+=1

        self.ctls = QCheckBox("CTLS"); self.ctls.setChecked(True)
        self.allow = QCheckBox("Allow cancel"); self.allow.setChecked(True)
        self.unatt = QCheckBox("Unattended")
        g.addWidget(self.ctls, r,0); g.addWidget(self.allow, r,1); g.addWidget(self.unatt, r,2); r+=1

        self.op = QComboBox()
        self.op.addItems(["03 Inquiry","04 Execute","05 Authorize","06 Capture"])
        g.addWidget(QLabel("Operation"), r,0); g.addWidget(self.op, r,1); r+=1

        box = QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Discover")
        box.accepted.connect(self.accept); box.rejected.connect(self.reject)
        g.addWidget(box, r,0,1,4)

    def data(self) -> Dict[str,Optional[str]]:
        return {
            'timeout': str(self.timeout.value()),
            'restrict_token': self.gen_token.currentText().split()[0],
            'manual': self.manual.isChecked(),
            'manual_reason': self.man_reason.currentText() if self.manual.isChecked() else None,
            'tran_type': self.tran_type.currentText().split()[0],
            'amount': to_minor(self.amount.value()),
            'currency': self.currency.currentText().split()[0],
            'cash': to_minor(self.cash_amt.value()) if self.cash_chk.isChecked() else None,
            'fx': self.fx_chk.isChecked(),
            'fx_to': self.fx_to.currentText().split()[0] if self.fx_chk.isChecked() else None,
            'fx_amt': to_minor(self.fx_amt.value()) if self.fx_chk.isChecked() else None,
            'use_token': self.use_tok.isChecked(),
            'token_val': self.tok_val.text().strip(),
            'service_type': self.service.currentText(),
            'ctls': self.ctls.isChecked(),
            'allow_cancel': self.allow.isChecked(),
            'unattended': self.unatt.isChecked(),
            'operation': self.op.currentText().split()[0],
        }
