#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# dialogs.py  –  FULL FILE  (v1.7 • credit‑threshold rule)

"""
Qt Dialogs
==========

1. **CreditTermDlg** – בחירת סוג אשראי / מספר תשלומים
   • “03 מיידית” מוצג ראשון.
   • מוצגות *רק* האפשרויות שאושרו בתשובת DISCOVERY (ללא כפתורים מושבתים).
   • **חדש (v1.7)**
       – “06 קרדיט” מופיע רק אם סכום העסקה ≥ ₪ 75.00,
         ובמקרה זה מספר‑התשלומים המינימלי ננעל ל‑3.
   • אם DISCOVERY לא איפשר אף סוג – מוצגת ברירת‑מחדל “03 מיידית”.
   • **v1.6**: שינוי “מס׳ תשלומים” מחשב אוטומטית תשלום‑ראשון/המשך.

2. **DiscoverDlg** – חלון פרמטרים לפקודת DISCOVERY (לשם השלמות).
"""

from typing import Dict, Tuple

from PyQt5.QtCore  import Qt, QEvent
from PyQt5.QtGui   import QFont
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget,
)

from utils import to_minor


# ───────────────────────────────────────────────────────────
#  Shared pastel‑blue stylesheet
# ───────────────────────────────────────────────────────────
_STYLE = """
QDialog {
    background: #f6fbff;
    font-family: "Segoe UI";
    font-size: 10.5pt;
    color: #34495e;
}
QGroupBox {
    border: 1px solid #c9dff5;
    border-radius: 6px;
    margin-top: 14px;
    background: #ffffff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    font-weight: 600;
    color: #1d4f8b;
}
QLabel { color: #34495e; }
QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox {
    border: 1px solid #b3cce6;
    border-radius: 4px;
    padding: 3px 6px;
    min-height: 24px;
    background: #ffffff;
}
QCheckBox { spacing: 6px; }
QPushButton {
    background: #3498db;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 18px;
    font-weight: 500;
}
QPushButton:hover   { background: #2d89c8; }
QPushButton:pressed { background: #2574ad; }
QPushButton:disabled{ background: #a7c3dd; }
QDialogButtonBox QPushButton { min-width: 90px; }
"""


# ═══════════════════════════════════════════════════════════
#                Credit / Installments dialog
# ═══════════════════════════════════════════════════════════
class CreditTermDlg(QDialog):
    """
    חלון בחירת סוג אשראי / תשלומים.

    Parameters
    ----------
    flags : Dict[str, bool]
        אילו סוגי תשלום מותר לכרטיס (מתשובת DISCOVERY).
        המפתחות האפשריים: "regular", "special", "immediate",
                           "credit", "installments"
    mn / mx : int
        גבולות מספר‑התשלומים שהחזיר ה‑P400.
    total : float
        סכום העסקה.
    """

    # כיתובי ממשק ➜ קוד אשראי
    _map: Dict[str, str] = {
        "03 מיידית":     "3",
        "01 רגילה":      "1",
        "02 דחויה(+30)": "2",
        "06 קרדיט":      "6",
        "08 תשלומים":    "8",
    }

    # קוד ➜ שם דגל בתשובת DISCOVERY
    _map_field: Dict[str, str] = {
        "1": "regular",
        "2": "special",
        "3": "immediate",
        "6": "credit",
        "8": "installments",
    }

    _CREDIT_MIN_AMOUNT = 75.0      # ₪ – threshold להצגת “קרדיט”
    _CREDIT_MIN_PAY    = 3         # מינימום מספר‑תשלומים עבור קרדיט

    # ───────────────────────────────────────────────────────
    # Construction
    # ───────────────────────────────────────────────────────
    def __init__(
        self,
        flags: Dict[str, bool],
        mn: int,
        mx: int,
        total: float,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        # Window prefs
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowTitle("בחירת אשראי / תשלומים")
        self.setMinimumWidth(560)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(_STYLE)

        self._total = total

        # Layout root
        main = QVBoxLayout(self)
        main.setContentsMargins(18, 18, 18, 18)
        main.setSpacing(14)

        # ―― group: credit type ――
        grp_type = QGroupBox("אפשרויות נתמכות")
        form_type = QFormLayout(grp_type)
        form_type.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.cmb = QComboBox()
        added_any = False
        for label, code in self._map.items():
            field = self._map_field[code]
            if not flags.get(field, False):
                continue                         # DISCOVERY לא מאפשר
            if code == "6" and total < self._CREDIT_MIN_AMOUNT:
                continue                         # קרדיט מוסתר מתחת ל‑75 ₪
            self.cmb.addItem(label, code)
            added_any = True
        if not added_any:                       # Fallback – Immediate
            self.cmb.addItem("03 מיידית", "3")

        form_type.addRow("סוג אשראי", self.cmb)
        main.addWidget(grp_type)

        # ―― group: installment details ――
        grp_pay = QGroupBox("פרטי תשלומים")
        grid = QGridLayout(grp_pay)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        # labels / editors
        self.pay_lbl   = QLabel("מס׳ תשלומים:")
        self.first_lbl = QLabel("תשלום ראשון ₪:")
        self.next_lbl  = QLabel("תשלום המשך ₪:")
        for lbl in (self.pay_lbl, self.first_lbl, self.next_lbl):
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.pay_spin   = QSpinBox(minimum=mn, maximum=mx, value=mn)
        self.first_spin = QDoubleSpinBox(decimals=2, maximum=total,
                                         value=round(total / max(1, mn), 2))
        self.next_spin  = QDoubleSpinBox(decimals=2, maximum=total)
        self.next_spin.setReadOnly(True)

        grid.addWidget(self.pay_lbl,   0, 0); grid.addWidget(self.pay_spin,  0, 1)
        grid.addWidget(self.first_lbl, 1, 0); grid.addWidget(self.first_spin,1, 1)
        grid.addWidget(self.next_lbl,  2, 0); grid.addWidget(self.next_spin, 2, 1)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        grid.addWidget(sep, 3, 0, 1, 2)
        main.addWidget(grp_pay)

        # ―― buttons ――
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            Qt.Horizontal,
            self
        )
        buttons.button(QDialogButtonBox.Ok).setText("אישור")
        buttons.button(QDialogButtonBox.Cancel).setText("ביטול")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons, alignment=Qt.AlignLeft)

        # Installment‑related widgets for convenience
        self._inst_widgets = [
            self.pay_lbl, self.pay_spin,
            self.first_lbl, self.first_spin,
            self.next_lbl, self.next_spin,
        ]

        # signals
        self.cmb.currentIndexChanged.connect(self._toggle_inst)
        self.pay_spin.valueChanged.connect(self._recalc_next)
        self.first_spin.valueChanged.connect(self._recalc_next)

        self._toggle_inst()         # initial state
        self._recalc_next()         # initial calc

    # ───────────────────────────────────────────────────────
    # Qt events
    # ───────────────────────────────────────────────────────
    def showEvent(self, e: QEvent):  # noqa: D401
        super().showEvent(e)
        self.raise_()
        self.activateWindow()

    # ───────────────────────────────────────────────────────
    # Internals
    # ───────────────────────────────────────────────────────
    def _toggle_inst(self):
        """הצג/הסתר שדות בהתאם לסוג אשראי שנבחר."""
        code = self.cmb.currentData()

        # קרדיט (06) – רק “מס׳ תשלומים”  +  מינימום 3
        if code == "6":
            for w in self._inst_widgets:
                w.setVisible(False)
            self.pay_lbl.setVisible(True)
            self.pay_spin.setVisible(True)

            # Enforce minimum of 3 payments
            if self.pay_spin.minimum() < self._CREDIT_MIN_PAY:
                self.pay_spin.setMinimum(self._CREDIT_MIN_PAY)
            if self.pay_spin.value() < self._CREDIT_MIN_PAY:
                self.pay_spin.setValue(self._CREDIT_MIN_PAY)

        # תשלומים (08) – כל השדות
        elif code == "8":
            for w in self._inst_widgets:
                w.setVisible(True)
            self._recalc_next()

        # מיידית / רגילה / דחויה – ללא שדות
        else:
            for w in self._inst_widgets:
                w.setVisible(False)

    def _recalc_next(self):
        """
        חישוב סכומי תשלומים כך שהסכום המצטבר יתאים בדיוק
        לסכום העסקה – השארית (אם קיימת) נכנסת לתשלום הראשון.
        """
        if not self.first_spin.isVisible():
            return

        payments = max(1, self.pay_spin.value())
        remain_cnt = max(1, payments - 1)

        # =============================
        # חישוב אוטומטי (pay_spin שונה)
        # =============================
        if self.sender() is self.pay_spin:
            # “תשלום המשך” ‑ תמיד נקבע ע"י חיתוך כלפי מטה לשתי ספרות
            all_agorot   = int(round(self._total * 100))
            nxt_agorot   = all_agorot // payments          # floor
            next_payment = nxt_agorot / 100.0

            # השארית מצורפת לתשלום הראשון
            first_payment = round(self._total - next_payment * remain_cnt, 2)

        # ======================================
        # המשתמש ערך ידנית את first_spin → עדכן
        # ======================================
        else:
            first_payment = self.first_spin.value()
            remain_total  = max(0.0, self._total - first_payment)
            next_payment  = round(remain_total / remain_cnt, 2)

        # — הצגה (ללא טריגר חוזר) —
        self.first_spin.blockSignals(True)
        self.first_spin.setValue(first_payment)
        self.first_spin.blockSignals(False)

        self.next_spin.blockSignals(True)
        self.next_spin.setValue(next_payment)
        self.next_spin.blockSignals(False)

    # ───────────────────────────────────────────────────────
    # Public API
    # ───────────────────────────────────────────────────────
    def data(self) -> Tuple[str, int, float, float]:
        """
        Returns
        -------
        tuple (code, payments, first_payment, next_payment)
        """
        code = self.cmb.currentData()

        # קרדיט – מחזיר סכומים 0 כדי שלא יישלחו
        if code == "6":
            return code, self.pay_spin.value(), 0.0, 0.0

        # תשלומים – כל הערכים
        if code == "8":
            return (
                code,
                self.pay_spin.value(),
                self.first_spin.value(),
                self.next_spin.value(),
            )

        # אחרים – רק קוד
        return code, 0, 0.0, 0.0


# ═══════════════════════════════════════════════════════════
#                    Discover‑parameters dialog
# ═══════════════════════════════════════════════════════════
class DiscoverDlg(QDialog):
    """Dialog for DISCOVERY command parameters (UI only)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowTitle("Discover – Read Card")
        self.setMinimumWidth(480)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(_STYLE)

        layout = QFormLayout(self)
        layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # — timeout —
        self.timeout = QSpinBox(minimum=5, maximum=999, value=60)
        layout.addRow("Timeout (s)", self.timeout)

        # — manual entry —
        man_box = QHBoxLayout()
        self.manual = QCheckBox("הזנה ידנית")
        self.man_reason = QComboBox()
        self.man_reason.addItems(["SIG", "CNP"])
        self.man_reason.setEnabled(False)
        self.manual.toggled.connect(self.man_reason.setEnabled)
        man_box.addWidget(self.manual)
        man_box.addWidget(self.man_reason)
        layout.addRow("Manual", man_box)

        # — transaction type —
        self.tran_type = QComboBox()
        self.tran_type.addItems(
            ["01 Regular", "02 Unloading", "03 Forced",
             "06 Cashback", "30 Balance", "53 Refund", "55 Loading"]
        )
        layout.addRow("Tran. type", self.tran_type)

        # — amount / currency —
        amt_box = QHBoxLayout()
        self.amount = QDoubleSpinBox(decimals=2, maximum=999999, value=10.00)
        self.currency = QComboBox()
        self.currency.addItems(["376 NIS", "840 USD", "978 EUR"])
        amt_box.addWidget(self.amount)
        amt_box.addWidget(self.currency)
        layout.addRow("Amount / Curr", amt_box)

        # — cash back —
        cash_box = QHBoxLayout()
        self.cash_chk = QCheckBox("Cash?")
        self.cash_amt = QDoubleSpinBox(decimals=2, maximum=999999)
        self.cash_amt.setEnabled(False)
        self.cash_chk.toggled.connect(self.cash_amt.setEnabled)
        cash_box.addWidget(self.cash_chk)
        cash_box.addWidget(self.cash_amt)
        layout.addRow("Cash back", cash_box)

        # — FX conversion —
        fx_box = QHBoxLayout()
        self.fx_chk = QCheckBox("Convert")
        self.fx_to = QComboBox()
        self.fx_to.addItems(["376 NIS", "840 USD", "978 EUR"])
        self.fx_amt = QDoubleSpinBox(decimals=2, maximum=999999)
        for w in (self.fx_to, self.fx_amt):
            w.setEnabled(False)
        self.fx_chk.toggled.connect(
            lambda b: [w.setEnabled(b) for w in (self.fx_to, self.fx_amt)]
        )
        fx_box.addWidget(self.fx_chk)
        fx_box.addWidget(QLabel("to"))
        fx_box.addWidget(self.fx_to)
        fx_box.addWidget(QLabel("Amt"))
        fx_box.addWidget(self.fx_amt)
        layout.addRow("FX convert", fx_box)

        # — token —
        self.gen_token = QComboBox()
        self.gen_token.addItems(["0 None", "1 All cards", "2 Shufersal"])
        layout.addRow("Gen token", self.gen_token)

        tok2_box = QHBoxLayout()
        self.use_tok = QCheckBox("Use token")
        self.tok_val = QLineEdit()
        self.tok_val.setEnabled(False)
        self.use_tok.toggled.connect(self.tok_val.setEnabled)
        tok2_box.addWidget(self.use_tok)
        tok2_box.addWidget(self.tok_val)
        layout.addRow("", tok2_box)

        # — service type —
        self.service = QComboBox()
        self.service.addItems(["", "1", "2", "3"])
        layout.addRow("Service type", self.service)

        # — flags —
        flag_box = QHBoxLayout()
        self.ctls = QCheckBox("CTLS")
        self.ctls.setChecked(True)
        self.allow = QCheckBox("Allow cancel")
        self.allow.setChecked(True)
        self.unatt = QCheckBox("Unattended")
        flag_box.addWidget(self.ctls)
        flag_box.addWidget(self.allow)
        flag_box.addWidget(self.unatt)
        layout.addRow("Flags", flag_box)

        # — operation —
        self.op = QComboBox()
        self.op.addItems(["03 Inquiry", "04 Execute", "05 Authorize", "06 Capture"])
        layout.addRow("Operation", self.op)

        # — buttons —
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Discover")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    # ───────────────────────────────────────────────────────
    # Public helper
    # ───────────────────────────────────────────────────────
    def data(self) -> Dict:
        return {
            "timeout": str(self.timeout.value()),
            "restrict_token": self.gen_token.currentText().split()[0],
            "manual": self.manual.isChecked(),
            "manual_reason": self.man_reason.currentText() if self.manual.isChecked() else "",
            "tran_type": self.tran_type.currentText().split()[0],
            "amount": to_minor(self.amount.value()),
            "currency": self.currency.currentText().split()[0],
            "cash": to_minor(self.cash_amt.value()) if self.cash_chk.isChecked() else None,
            "fx": self.fx_chk.isChecked(),
            "fx_to": self.fx_to.currentText().split()[0] if self.fx_chk.isChecked() else "",
            "fx_amt": to_minor(self.fx_amt.value()) if self.fx_chk.isChecked() else None,
            "use_token": self.use_tok.isChecked(),
            "token_val": self.tok_val.text().strip(),
            "service_type": self.service.currentText(),
            "ctls": self.ctls.isChecked(),
            "allow_cancel": self.allow.isChecked(),
            "unattended": self.unatt.isChecked(),
            "operation": self.op.currentText().split()[0],
        }
