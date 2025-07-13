#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pay_process.py  –  FULL FILE  (v5.8 • exact RESULT_CODE + abort on non-credit)

"""
Verifone-P400 Quick-Sale finite-state machine
============================================

What’s new in v5.8
------------------
1. **Exact RESULT_CODE parsing**  
   Every comparison now looks at the *full* `<RESULT_CODE>` value
   instead of a fragile substring search.  
   • Any reply whose code ≠ 0 triggers an immediate auto-cancel.  
   • Discovery replies with codes such as 20 (“Bad Card Read”) are no
     longer mistaken for success.

2. **Unsupported cards abort early** (kept from v5.7)  
   • Cards that are *not* credit-capable **and** not debit (e.g. bus /
     membership) cause: “כרטיס לא נתמך” → CANCEL → no dialog.

3. **Everything else unchanged**  
   • Debit cards (brand 08 or Immediate-only profile) still work.  
   • Receipt on decline when `<PRINT_RECEIPT>` = 1 or 2.  
   • Zero-lag restart after failure.  
   • Automatic recovery with `GET_TRAN_DETAILS`.  
   • Prompt “מספר אישור …” only when the number is present and non-zero.
"""

from __future__ import annotations
import re
from typing import Dict, List

from PyQt5.QtCore    import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui     import QFont
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QVBoxLayout, QWidget,
)

from dialogs   import CreditTermDlg
from receipt   import save_receipt
from utils     import rand_session, to_minor, des3_decrypt
from xml_sign  import env_xml
from config    import save_mac
from prompts   import show_prompt, hide_prompt, show_spinner, hide_spinner


# ════════════════════════════════════════════════════════
#  Helper functions
# ════════════════════════════════════════════════════════
def _result_code(xml: str) -> int:
    """Return the integer <RESULT_CODE>, or –1 if missing."""
    m = re.search(r"<RESULT_CODE>\s*(\d+)\s*</RESULT_CODE>", xml)
    return int(m.group(1)) if m else -1


def _issuer_auth_num(xml: str) -> str:
    """Return non-zero ISSUER_AUTH_NUM or ''."""
    m = re.search(r"<ISSUER_AUTH_NUM>([^<]*)</ISSUER_AUTH_NUM>", xml)
    if not m:
        return ""
    num = m.group(1).strip()
    return num if num and not re.fullmatch(r"0+", num) else ""


def _maybe_save_receipt(xml: str) -> bool:
    """
    Save *xml* to receipt.json only if <PRINT_RECEIPT> is 1 or 2.
    Returns True if a file was written.
    """
    m = re.search(r"<PRINT_RECEIPT>([012])</PRINT_RECEIPT>", xml)
    if m and m.group(1) != "0":
        save_receipt(xml)
        return True
    return False


# ════════════════════════════════════════════════════════
#  “Regular / Manual” choice dialog
# ════════════════════════════════════════════════════════
class _ManualChoiceDlg(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowTitle("בחר סוג הזנה")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFont(QFont("Segoe UI", 10))

        lay  = QVBoxLayout(self)
        form = QFormLayout(); lay.addLayout(form)

        self.cmb = QComboBox()
        self.cmb.addItem("רגיל", False)
        self.cmb.addItem("ידני (הקלדת כרטיס)", True)
        form.addRow(QLabel("סוג עסקה:"), self.cmb)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("אישור")
        btns.button(QDialogButtonBox.Cancel).setText("ביטול")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def is_manual(self) -> bool:           # noqa: D401
        return bool(self.cmb.currentData())


# ════════════════════════════════════════════════════════
#  QuickSaleMixin
# ════════════════════════════════════════════════════════
class QuickSaleMixin:
    """
    Add Quick-Sale FSM behaviour to MainWindow.
    Must be *first* in MainWindow’s MRO.
    """

    enqueue_qs = pyqtSignal(float, str)   # webhook thread → GUI thread

    # ─────────────────────────── ctor ───────────────────────────
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enqueue_qs.connect(self.quick_sale)

        # runtime state
        self.qs_queue: List[tuple[float, str]] = []
        self.qs_active        = False
        self.session          = ""
        self._current_amount  = 0.0
        self._qs_phase        = ""
        self._trans_id        = ""
        self.qs_defaults: Dict = {}
        self.qs_start_body    = ""
        self._awaiting_cancel = False
        self._cancel_timer    = None
        self._manual_key_exchange = False

    # ───────────────────── webhook bridge ─────────────────────
    @pyqtSlot(float, str)
    def _from_webhook(self, amount: float, tran_type: str):
        self.enqueue_qs.emit(amount, tran_type)

    # ───────────────────── public entry ─────────────────────
    def quick_sale(self, amount: float, tran_type: str = "01"):
        self.qs_queue.append((amount, tran_type))
        self._start_next_qs()

    # ───────────────────── queue helpers ─────────────────────
    def _start_next_qs(self):
        if self.qs_active or not self.qs_queue:
            return
        amt, code = self.qs_queue.pop(0)
        self._launch_qs(amt, code)

    # ───────────────────── launcher ─────────────────────
    def _launch_qs(self, amount: float, tran_type: str):
        hide_prompt(); hide_spinner()

        dlg = _ManualChoiceDlg(self)
        if dlg.exec_() != QDialog.Accepted:
            show_prompt("העסקה בוטלה", 3000)
            return
        manual = dlg.is_manual()

        show_spinner("מעבד תשלום…")

        self.session         = rand_session()
        self._current_amount = amount
        self.qs_amount.setValue(amount)

        self.qs_defaults = {
            "timeout":        "60",
            "restrict_token": "0",
            "manual":         manual,
            "manual_reason":  "SIG" if manual else "",
            "tran_type":      tran_type,
            "amount":         to_minor(amount),
            "currency":       "376",
            "cash":           None,
            "fx":             False,
            "fx_to":          "",
            "fx_amt":         None,
            "use_token":      False,
            "token_val":      "",
            "service_type":   "",
            "ctls":           True,
            "allow_cancel":   True,
            "unattended":     False,
            "operation":      "04",
        }
        self.qs_start_body = (
            "<INVOICE>100000</INVOICE>"
            f"<POS_TYPE>{self.pos_combo.currentText()}</POS_TYPE>"
        )

        self.qs_active = True
        self._qs_phase = "PING"
        self._trans_id = ""

        self._send(env_xml(
            "ADMIN", "PING", self.session,
            bool(int(self.train_combo.currentText())), self.mac_key
        ))

    # ───────────────────── XML helpers ─────────────────────
    def _disc_body(self, o: Dict) -> str:
        parts = [
            f"<TIMEOUT>{o['timeout']}</TIMEOUT>",
            "<TRANSACTION_DETAILS>",
            f"<RESTRICT_TOKEN>{o['restrict_token']}</RESTRICT_TOKEN>",
            f"<MANUAL>{int(o['manual'])}</MANUAL>",
            f"<CTLS>{int(o['ctls'])}</CTLS>",
            f"<ALLOW_CANCEL>{int(o['allow_cancel'])}</ALLOW_CANCEL>",
            f"<UNATTENDED>{int(o['unattended'])}</UNATTENDED>",
            f"<TRAN_TYPE>{o['tran_type']}</TRAN_TYPE>",
            "<MTI>100</MTI>",
            f"<TRANSACTION_AMOUNT>{o['amount']}</TRANSACTION_AMOUNT>",
            f"<ORIGINAL_CURRENCY>{o['currency']}</ORIGINAL_CURRENCY>",
        ]
        if o["manual"] and o["manual_reason"]:
            parts.append(f"<MANUAL_REASON>{o['manual_reason']}</MANUAL_REASON>")
        parts.append(f"<OPERATION>{o['operation']}</OPERATION></TRANSACTION_DETAILS>")
        return "".join(parts)

    def _auth_body(self, base: Dict, ct: str,
                   pay: int, first: float, nxt: float) -> str:
        body = self._disc_body(base)
        add_pay = pay - 1 if ct == "8" and pay > 0 else pay
        patch = f"<CREDIT_TERMS>{ct}</CREDIT_TERMS>"
        if pay:
            patch += f"<PAYMENTS_NUMBER>{add_pay:02d}</PAYMENTS_NUMBER>"
        if first:
            patch += f"<FIRST_PAYMENT_AMOUNT>{to_minor(first)}</FIRST_PAYMENT_AMOUNT>"
        if nxt:
            patch += f"<NEXT_PAYMENT_AMOUNT>{to_minor(nxt)}</NEXT_PAYMENT_AMOUNT>"
        return body.replace("</TRANSACTION_DETAILS>",
                            patch + "</TRANSACTION_DETAILS>")

    # ───────────────────── send helpers ─────────────────────
    def _send_status(self):
        self._qs_phase = "STATUS"
        self._send(env_xml("ADMIN", "STATUS", self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key))

    def _send_start_tran(self):
        self._qs_phase = "START_TRAN"
        self._send(env_xml("SESSION", "START_TRAN", self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key,
                           self.qs_start_body))
        show_prompt("התחלת עסקה", 3000)

    def _send_finish_tran(self, *, immediate: bool = False):
        if immediate:
            self._qs_done()
        self._send(env_xml("SESSION", "FINISH_TRAN", self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key))

    # ───────────────────── cancel / recovery ─────────────────────
    def _schedule_cancel(self):
        if self._awaiting_cancel:
            return
        self._awaiting_cancel = True
        self._cancel_timer = QTimer(self)
        self._cancel_timer.setSingleShot(True)
        self._cancel_timer.timeout.connect(self._send_cancel)
        self._cancel_timer.start(2000)

    def _send_cancel(self):
        hide_spinner()
        self._awaiting_cancel = False
        self._send(env_xml("GENERAL", "CANCEL", self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key))

    def _send_get_details(self):
        body = f"<TRANS_ID>{self._trans_id}</TRANS_ID>"
        self._qs_phase = "GET_DETAILS"
        self._send(env_xml("REPORT", "GET_TRAN_DETAILS", self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key, body))

    # ───────────────────── DISCOVERY → AUTHORIZE ─────────────────────
    def _handle_discovery_ok(self, recv: str):
        self._trans_id = re.search(r"<TRANS_ID>([^<]+)</TRANS_ID>", recv).group(1)

        flags = {k: bool(re.search(fr"<TERMS_{k.upper()}>1</TERMS_{k.upper()}>", recv))
                 for k in ("regular", "special", "immediate", "credit", "installments")}

        brand08 = bool(re.search(r"<BRAND>\s*08\s*</BRAND>", recv))

        credit_capable = flags["regular"] or flags["special"] or flags["credit"] or flags["installments"]
        debit_card     = flags["immediate"] and brand08
        non_credit     = not credit_capable and not debit_card

        if non_credit:                         # unsupported card
            show_prompt("כרטיס לא נתמך", 4000)
            self._send_cancel(); self._qs_done()
            return

        if debit_card:                         # debit → only Immediate
            for k in flags:
                if k != "immediate":
                    flags[k] = False

        # min / max payments for dialog
        def _iv(tag: str, d: int) -> int:
            m = re.search(fr"<{tag}>(\d+)", recv)
            return int(m.group(1)) if m else d

        mn = max(2, min(_iv("MIN_PAYMENTS", 2), _iv("CREDIT_MIN_PAYMENTS", 2)))
        mx = max(2, max(_iv("MAX_PAYMENTS", 36), _iv("CREDIT_MAX_PAYMENTS", 36)))

        dlg = CreditTermDlg(flags, mn, mx, self._current_amount, None)
        if dlg.exec_() != QDialog.Accepted:
            show_prompt("העסקה לא אושרה", 4000)
            self._send_cancel(); self._qs_done()
            return
        ct, pay, first, nxt = dlg.data()

        self._qs_phase = "AUTHORIZE"
        self._send(env_xml(
            "PAYMENT", "AUTHORIZE", self.session,
            bool(int(self.train_combo.currentText())), self.mac_key,
            self._auth_body(self.qs_defaults, ct, pay, first, nxt)
        ))
        show_prompt("נא המתן לאישור עסקה")

    # ───────────────────── end-of-sale housekeeping ─────────────────────
    def _qs_done(self):
        hide_spinner()
        self.qs_active = False
        self._qs_phase = ""
        self._start_next_qs()

    # ═════════════════════════════════════════════════════
    #  central response handler
    # ═════════════════════════════════════════════════════
    def _handle_response(self, sent: str, recv: str):
        self.sent_log.append(sent); self.recv_log.append(recv)

        # —— auto-cancel on any non-zero RESULT_CODE ——
        if _result_code(recv) != 0 and "<COMMAND>CANCEL" not in sent:
            show_prompt("העסקה לא אושרה", 4000)
            self._schedule_cancel()
            return

        # ───────────────────────────────────
        # Quick-Sale FSM
        # ───────────────────────────────────
        if self.qs_active:

            # ---------- PING ----------
            if "<COMMAND>PING" in sent:
                if _result_code(recv) == 0:
                    self._send_status()
                else:
                    show_prompt("העסקה לא אושרה", 4000); self._qs_done()
                return

            # ---------- STATUS ----------
            if "<COMMAND>STATUS" in sent:
                if _result_code(recv) == 0:
                    if re.search(r"<SHVA_STATUS>(?!1)", recv):
                        self.cmd_eod()
                    self._send_start_tran()
                else:
                    show_prompt("העסקה לא אושרה", 4000); self._qs_done()
                return

            # ---------- START_TRAN ----------
            if "<COMMAND>START_TRAN" in sent:
                if _result_code(recv) == 0:
                    disc_xml = env_xml("PAYMENT", "DISCOVERY", self.session,
                                       bool(int(self.train_combo.currentText())),
                                       self.mac_key, self._disc_body(self.qs_defaults))
                    self._qs_phase = "DISCOVERY"
                    show_prompt("זיהוי כרטיס", 3000)
                    self._send(disc_xml)
                else:
                    show_prompt("העסקה לא אושרה", 4000); self._qs_done()
                return

            # ---------- DISCOVERY ----------
            if "<COMMAND>DISCOVERY" in sent:
                if _result_code(recv) == 0:
                    self._handle_discovery_ok(recv)
                else:
                    show_prompt("זיהוי כרטיס נכשל", 4000); self._qs_done()
                return

            # ---------- AUTHORIZE ----------
            if "<COMMAND>AUTHORIZE" in sent:

                if "<EVENT>COMPLETED" in recv and _result_code(recv) == 0:
                    auth_no = _issuer_auth_num(recv)

                    if auth_no:                    # approved
                        show_prompt(
                            f"סיום עסקה\nהעסקה אושרה בהצלחה\nמספר אישור {auth_no}",
                            4000
                        )
                        save_receipt(recv)
                        try:
                            with open("receipt.json", "rb") as fp:
                                self.webhook.publish(fp.read())
                        except Exception:
                            pass
                        self._send_finish_tran()

                    else:                          # declined
                        show_prompt("העסקה נכשלה", 4000)
                        _maybe_save_receipt(recv)
                        self._send_finish_tran(immediate=True)
                    return

                # Not completed → recovery
                self._info("Recovery", "AUTHORIZE incomplete / failed – querying details")
                self._send_get_details()
                return

            # ---------- GET_TRAN_DETAILS ----------
            if "<COMMAND>GET_TRAN_DETAILS" in sent:
                ok_rc    = _result_code(recv) == 0
                has_tran = "<TRANSACTIONS></TRANSACTIONS>" not in recv
                if ok_rc and has_tran:
                    proc_err = re.search(r"<PROCESSOR_ERROR>(\d+)", recv)
                    auth_no  = _issuer_auth_num(recv)
                    if proc_err and proc_err.group(1) == "0" and auth_no:
                        show_prompt(
                            f"סיום עסקה\nהעסקה אושרה בהצלחה\nמספר אישור {auth_no}",
                            4000
                        )
                        save_receipt(recv)
                        try:
                            with open("receipt.json", "rb") as fp:
                                self.webhook.publish(fp.read())
                        except Exception:
                            pass
                        self._send_finish_tran()
                    else:
                        show_prompt("העסקה נכשלה", 4000)
                        _maybe_save_receipt(recv)
                        self._send_finish_tran(immediate=True)
                else:
                    show_prompt("העסקה נכשלה", 4000)
                    _maybe_save_receipt(recv)
                    self._send_finish_tran(immediate=True)
                return

            # ---------- VOID flow ----------
            if self._qs_phase == "VOID" and "<COMMAND>AUTHORIZE" in sent:
                self._send_finish_tran()   # waits
                return

            # ---------- FINISH_TRAN ----------
            if "<COMMAND>FINISH_TRAN" in sent:
                self._qs_done()
                return

        # ───────────────────────────────────
        # CANCEL outside Quick-Sale
        # ───────────────────────────────────
        if "<COMMAND>CANCEL" in sent:
            show_prompt("העסקה לא אושרה", 4000)
            _maybe_save_receipt(recv)
            self._qs_done()
            return

        # ───────────────────────────────────
        # Key-exchange housekeeping
        # ───────────────────────────────────
        if ("<COMMAND>EXCHANGE_KEYS" in sent and "<MAC_KEY>" in recv
                and self._manual_key_exchange):
            m = re.search(r"<MAC_KEY>([^<]+)</MAC_KEY>", recv)
            if m:
                try:
                    self.mac_key = des3_decrypt(self.ktk_field.text().strip(), m.group(1))
                    save_mac(self.mac_key)
                    self.mac_view.setText(self.mac_key)
                    for b in (self.status_btn, self.eod_btn, self.qs_btn,
                              self.free_send_btn, *self.free_cmds):
                        b.setEnabled(True)
                except Exception as exc:
                    self._crit("Decrypt", str(exc))
                finally:
                    self._manual_key_exchange = False
