"""
Qt Dialogs for interactive transaction steps:
- CreditTermDlg: choose credit/installment options
- DiscoverDlg: configure DISCOVERY parameters (timeout, manual entry, FX, etc.)
"""

from PyQt5.QtWidgets import (
    QDialog, QGridLayout, QLabel, QComboBox, QSpinBox,
    QDoubleSpinBox, QCheckBox, QDialogButtonBox, QLineEdit
)
from PyQt5.QtCore import Qt
from typing import Dict, Tuple
from utils import to_minor

class CreditTermDlg(QDialog):
    """
    Credit/installment selection dialog.
    """

    _map = {
        '01 רגילה':    '1',
        '02 דחויה(+30)':'2',
        '03 מיידית':    '3',
        '06 אשראי':     '6',
        '08 תשלומים':   '8'
    }

    def __init__(self, flags: Dict[str,bool], mn: int, mx: int, total: float, parent=None):
        super().__init__(parent)
        # keep this dialog above all other windows
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.total = total
        self.setWindowTitle("בחירת אשראי / תשלומים")

        layout, row = QGridLayout(self), 0

        # Title row
        layout.addWidget(QLabel("אפשרויות נתמכות:"), row, 0, 1, 2)
        row += 1

        # Credit type dropdown
        self.cmb = QComboBox()
        for label, code in self._map.items():
            field = {
                '1':'regular','2':'special','3':'immediate',
                '6':'credit','8':'installments'
            }[code]
            self.cmb.addItem(label, code)
            self.cmb.model().item(self.cmb.count()-1).setEnabled(flags.get(field, False))
        layout.addWidget(self.cmb, row, 0, 1, 2)
        row += 1

        # Number of payments
        self.pay_lbl  = QLabel("מס׳ תשלומים:")
        self.pay_spin = QSpinBox(minimum=mn, maximum=mx, value=mn)
        layout.addWidget(self.pay_lbl, row, 0)
        layout.addWidget(self.pay_spin, row, 1)
        row += 1

        # First and next payment amounts
        self.first_lbl = QLabel("תשלום ראשון ₪:")
        self.first_spin= QDoubleSpinBox(decimals=2, maximum=total, value=total/mn)
        self.next_lbl  = QLabel("תשלום המשך ₪:")
        self.next_spin = QDoubleSpinBox(decimals=2, maximum=total, value=self.first_spin.value())
        self.next_spin.setReadOnly(True)
        layout.addWidget(self.first_lbl, row, 0)
        layout.addWidget(self.first_spin, row, 1)
        row += 1
        layout.addWidget(self.next_lbl, row, 0)
        layout.addWidget(self.next_spin, row, 1)
        row += 1

        # Connect signals
        self.cmb.currentIndexChanged.connect(self._toggle)
        self.pay_spin.valueChanged.connect(self._recalc)
        self.first_spin.valueChanged.connect(self._recalc)
        self._toggle()

        # OK / Cancel buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, row, 0, 1, 2)

    def _toggle(self):
        visible = self.cmb.currentData() in ('6','8')
        for w in (self.pay_lbl, self.pay_spin, self.first_lbl, self.first_spin, self.next_lbl, self.next_spin):
            w.setVisible(visible)
        self._recalc()

    def _recalc(self):
        if not self.first_spin.isVisible():
            return
        count = max(1, self.pay_spin.value() - 1)
        nxt   = max(0.0, self.total - self.first_spin.value()) / count
        self.next_spin.blockSignals(True)
        self.next_spin.setValue(round(nxt, 2))
        self.next_spin.blockSignals(False)

    def data(self) -> Tuple[str,int,float,float]:
        code = self.cmb.currentData()
        if code in ('6','8'):
            return code, self.pay_spin.value(), self.first_spin.value(), self.next_spin.value()
        return code, 0, 0.0, 0.0

class DiscoverDlg(QDialog):
    """
    Dialog for DISCOVERY command parameters.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # keep this dialog above all other windows
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("Discover – Read Card")
        layout, row = QGridLayout(self), 0

        # Timeout spinner
        self.timeout = QSpinBox(minimum=5, maximum=999, value=60)
        layout.addWidget(QLabel("Timeout (s)"), row, 0)
        layout.addWidget(self.timeout, row, 1)
        row += 1

        # Manual entry toggle + reason
        self.manual     = QCheckBox("Manual entry")
        self.man_reason = QComboBox()
        self.man_reason.addItems(["SIG","CNP"])
        self.man_reason.setEnabled(False)
        self.manual.toggled.connect(self.man_reason.setEnabled)
        layout.addWidget(self.manual, row, 0)
        layout.addWidget(self.man_reason, row, 1)
        row += 1

        # Transaction type selector
        self.tran_type = QComboBox()
        self.tran_type.addItems([
            "01 Regular","02 Unloading","03 Forced",
            "06 Cashback","30 Balance","53 Refund","55 Loading"
        ])
        layout.addWidget(QLabel("Tran. type"), row, 0)
        layout.addWidget(self.tran_type, row, 1)
        row += 1

        # Amount & currency
        self.amount   = QDoubleSpinBox(decimals=2, maximum=999999, value=10.00)
        self.currency = QComboBox()
        self.currency.addItems(["376 NIS","840 USD","978 EUR"])
        layout.addWidget(QLabel("Bill ₪"), row, 0)
        layout.addWidget(self.amount, row, 1)
        layout.addWidget(QLabel("Curr"), row, 2)
        layout.addWidget(self.currency, row, 3)
        row += 1

        # Cash refund option
        self.cash_chk = QCheckBox("Cash?")
        self.cash_amt = QDoubleSpinBox(decimals=2, maximum=999999)
        self.cash_amt.setEnabled(False)
        self.cash_chk.toggled.connect(self.cash_amt.setEnabled)
        layout.addWidget(self.cash_chk, row, 0)
        layout.addWidget(self.cash_amt, row, 1)
        row += 1

        # FX conversion option
        self.fx_chk = QCheckBox("Convert FX")
        self.fx_to  = QComboBox(); self.fx_to.addItems(["376 NIS","840 USD","978 EUR"])
        self.fx_amt = QDoubleSpinBox(decimals=2, maximum=999999)
        for w in (self.fx_to, self.fx_amt): w.setEnabled(False)
        self.fx_chk.toggled.connect(lambda b: [w.setEnabled(b) for w in (self.fx_to, self.fx_amt)])
        layout.addWidget(self.fx_chk, row, 0)
        layout.addWidget(QLabel("to"), row, 2)
        layout.addWidget(self.fx_to, row, 3)
        row += 1
        layout.addWidget(QLabel("Conv amt"), row, 2)
        layout.addWidget(self.fx_amt, row, 3)
        row += 1

        # Token generation & usage
        self.gen_token = QComboBox()
        self.gen_token.addItems(["0 None","1 All cards","2 Shufersal"])
        self.use_tok   = QCheckBox("Use token")
        self.tok_val   = QLineEdit()
        self.tok_val.setEnabled(False)
        self.use_tok.toggled.connect(self.tok_val.setEnabled)
        layout.addWidget(QLabel("Gen token"), row, 0)
        layout.addWidget(self.gen_token, row, 1)
        row += 1
        layout.addWidget(self.use_tok, row, 0)
        layout.addWidget(self.tok_val, row, 1)
        row += 1

        # Service type
        self.service = QComboBox()
        self.service.addItems(["","1","2","3"])
        layout.addWidget(QLabel("Service type"), row, 0)
        layout.addWidget(self.service, row, 1)
        row += 1

        # CTLS / allow cancel / unattended flags
        self.ctls  = QCheckBox("CTLS");   self.ctls.setChecked(True)
        self.allow = QCheckBox("Allow cancel"); self.allow.setChecked(True)
        self.unatt= QCheckBox("Unattended")
        layout.addWidget(self.ctls, row, 0)
        layout.addWidget(self.allow, row, 1)
        layout.addWidget(self.unatt, row, 2)
        row += 1

        # Operation code dropdown
        self.op = QComboBox()
        self.op.addItems(["03 Inquiry","04 Execute","05 Authorize","06 Capture"])
        layout.addWidget(QLabel("Operation"), row, 0)
        layout.addWidget(self.op, row, 1)
        row += 1

        # OK / Cancel buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Discover")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons, row, 0, 1, 4)

    def data(self) -> Dict:
        return {
            'timeout':       str(self.timeout.value()),
            'restrict_token': self.gen_token.currentText().split()[0],
            'manual':        self.manual.isChecked(),
            'manual_reason': self.man_reason.currentText() if self.manual.isChecked() else '',
            'tran_type':     self.tran_type.currentText().split()[0],
            'amount':        to_minor(self.amount.value()),
            'currency':      self.currency.currentText().split()[0],
            'cash':          to_minor(self.cash_amt.value()) if self.cash_chk.isChecked() else None,
            'fx':            self.fx_chk.isChecked(),
            'fx_to':         self.fx_to.currentText().split()[0] if self.fx_chk.isChecked() else '',
            'fx_amt':        to_minor(self.fx_amt.value()) if self.fx_chk.isChecked() else None,
            'use_token':     self.use_tok.isChecked(),
            'token_val':     self.tok_val.text().strip(),
            'service_type':  self.service.currentText(),
            'ctls':          self.ctls.isChecked(),
            'allow_cancel':  self.allow.isChecked(),
            'unattended':    self.unatt.isChecked(),
            'operation':     self.op.currentText().split()[0],
        }
