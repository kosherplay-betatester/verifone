# dialogs.py
"""
Qt Dialogs
----------

- CreditTermDlg  – בחירת סוג אשראי / מספר תשלומים
- DiscoverDlg    – קונפיגורציית DISCOVERY

Both windows use a pastel-blue theme, “Segoe UI” fonts and rounded inputs.
"""

from typing import Dict, Tuple

from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from utils import to_minor


# ───────────────────────────────────────────────────────────
#  Shared stylesheet  (pastel blue + rounded widgets)
# ───────────────────────────────────────────────────────────
_STYLE = """
/* window */
QDialog {
    background: #f6fbff;
    font-family: "Segoe UI";
    font-size: 10.5pt;
    color: #34495e;
}

/* group-boxes */
QGroupBox {
    border: 1px solid #c9dff5;
    border-radius: 6px;
    margin-top: 14px;               /* leave space for title */
    background: #ffffff;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
    font-weight: 600;
    color: #1d4f8b;
}

/* labels */
QLabel {
    color: #34495e;
}

/* editable fields */
QLineEdit,
QSpinBox,
QDoubleSpinBox,
QComboBox {
    border: 1px solid #b3cce6;
    border-radius: 4px;
    padding: 3px 6px;
    min-height: 24px;
    background: #ffffff;
}

/* check-boxes */
QCheckBox {
    spacing: 6px;
}

/* push-buttons */
QPushButton {
    background: #3498db;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px 18px;
    font-weight: 500;
}
QPushButton:hover { background: #2d89c8; }
QPushButton:pressed { background: #2574ad; }
QPushButton:disabled { background: #a7c3dd; }

/* dialog-button-box buttons */
QDialogButtonBox QPushButton { min-width: 90px; }
"""


# ───────────────────────────────────────────────────────────
#  Credit / Installments dialog
# ───────────────────────────────────────────────────────────
class CreditTermDlg(QDialog):
    """
    Dialog לבחירת סוג אשראי / מס׳ תשלומים
    • Always-on-top, application-modal.
    """

    _map = {
        "01 רגילה": "1",
        "02 דחויה(+30)": "2",
        "03 מיידית": "3",
        "06 אשראי": "6",
        "08 תשלומים": "8",
    }

    def __init__(
        self,
        flags: Dict[str, bool],
        mn: int,
        mx: int,
        total: float,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)

        # ---------- window prefs ----------
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowTitle("בחירת אשראי / תשלומים")
        self.setMinimumWidth(560)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFont(QFont("Segoe UI", 10))
        self.setStyleSheet(_STYLE)

        self._total = total

        main = QVBoxLayout(self)
        main.setContentsMargins(18, 18, 18, 18)
        main.setSpacing(14)

        # ――― credit type ―――
        grp_type = QGroupBox("אפשרויות נתמכות")
        form_type = QFormLayout(grp_type)
        form_type.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.cmb = QComboBox()
        for label, code in self._map.items():
            field = {
                "1": "regular",
                "2": "special",
                "3": "immediate",
                "6": "credit",
                "8": "installments",
            }[code]
            self.cmb.addItem(label, code)
            self.cmb.model().item(self.cmb.count() - 1).setEnabled(
                flags.get(field, False)
            )
        form_type.addRow("סוג אשראי", self.cmb)
        main.addWidget(grp_type)

        # ――― installment details ―――
        grp_pay = QGroupBox("פרטי תשלומים")
        grid = QGridLayout(grp_pay)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        # labels / editors
        self.pay_lbl = QLabel("מס׳ תשלומים:")
        self.first_lbl = QLabel("תשלום ראשון ₪:")
        self.next_lbl = QLabel("תשלום המשך ₪:")

        for lbl in (self.pay_lbl, self.first_lbl, self.next_lbl):
            lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.pay_spin = QSpinBox(minimum=mn, maximum=mx, value=mn)
        self.first_spin = QDoubleSpinBox(decimals=2, maximum=total, value=total / mn)
        self.next_spin = QDoubleSpinBox(decimals=2, maximum=total)
        self.next_spin.setReadOnly(True)

        grid.addWidget(self.pay_lbl, 0, 0)
        grid.addWidget(self.pay_spin, 0, 1)
        grid.addWidget(self.first_lbl, 1, 0)
        grid.addWidget(self.first_spin, 1, 1)
        grid.addWidget(self.next_lbl, 2, 0)
        grid.addWidget(self.next_spin, 2, 1)

        # subtle divider
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        grid.addWidget(sep, 3, 0, 1, 2)
        main.addWidget(grp_pay)

        # ――― buttons ―――
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.button(QDialogButtonBox.Ok).setText("אישור")
        buttons.button(QDialogButtonBox.Cancel).setText("ביטול")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main.addWidget(buttons, alignment=Qt.AlignLeft)

        # widgets toggled together
        self._inst_widgets = [
            self.pay_lbl,
            self.pay_spin,
            self.first_lbl,
            self.first_spin,
            self.next_lbl,
            self.next_spin,
        ]

        # signal connections
        self.cmb.currentIndexChanged.connect(self._toggle_inst)
        self.pay_spin.valueChanged.connect(self._recalc_next)
        self.first_spin.valueChanged.connect(self._recalc_next)

        self._toggle_inst()            # initial state

    # ---------- raised / focused ----------
    def showEvent(self, e: QEvent):  # noqa: D401
        super().showEvent(e)
        self.raise_()
        self.activateWindow()

    # ---------- internals ----------
    def _toggle_inst(self):
        active = self.cmb.currentData() in ("6", "8")
        for w in self._inst_widgets:
            w.setVisible(active)
        self._recalc_next()

    def _recalc_next(self):
        if not self.first_spin.isVisible():
            return
        payments = max(1, self.pay_spin.value() - 1)
        remaining = max(0.0, self._total - self.first_spin.value())
        nxt = remaining / payments if payments else 0.0
        self.next_spin.blockSignals(True)
        self.next_spin.setValue(round(nxt, 2))
        self.next_spin.blockSignals(False)

    # ---------- public ----------
    def data(self) -> Tuple[str, int, float, float]:
        code = self.cmb.currentData()
        if code in ("6", "8"):
            return (
                code,
                self.pay_spin.value(),
                self.first_spin.value(),
                self.next_spin.value(),
            )
        return code, 0, 0.0, 0.0


# ───────────────────────────────────────────────────────────
#  Discover-parameters dialog
# ───────────────────────────────────────────────────────────
class DiscoverDlg(QDialog):
    """
    Dialog for DISCOVERY command parameters (same API, prettier UI).
    """

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
            [
                "01 Regular",
                "02 Unloading",
                "03 Forced",
                "06 Cashback",
                "30 Balance",
                "53 Refund",
                "55 Loading",
            ]
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
        self.ctls = QCheckBox("CTLS");  self.ctls.setChecked(True)
        self.allow = QCheckBox("Allow cancel"); self.allow.setChecked(True)
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

    # ---------- public ----------
    def data(self) -> Dict:
        return {
            "timeout": str(self.timeout.value()),
            "restrict_token": self.gen_token.currentText().split()[0],
            "manual": self.manual.isChecked(),
            "manual_reason": self.man_reason.currentText()
            if self.manual.isChecked()
            else "",
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
