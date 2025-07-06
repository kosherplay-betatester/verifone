#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pay_process.py  –  FULL FILE  (v4.8.2 • dialog + PAYMENTS_NUMBER=N-1 + 2-installments fix + webhook callback)

"""
Quick-Sale finite-state machine for the Verifone-P400 desktop app
================================================================

Key points
----------
1. **Manual / Regular chooser**
   • Immediately when Quick-Sale starts, a small dialog asks the cashier
     whether the card will be read *Regularly* (default) or *Manually*
     (key-in on the P400).  
     • Enter = OK • Esc / Cancel = abort transaction.

2. **`<MANUAL>` & `<MANUAL_REASON>`**
   • If the user picked “Manual”, `<MANUAL>1</MANUAL>` and
     `<MANUAL_REASON>SIG</MANUAL_REASON>` are added to DISCOVERY.

3. **Installments: PAYMENTS_NUMBER = N-1**
   • For credit-terms **08 (Installments)** we send
     `<PAYMENTS_NUMBER>` = *N – 1* (the first payment is “0” on the P400).

4. **2-installments enabled**
   • If the device returns `<MIN_PAYMENTS>2</…>` but
     `<CREDIT_MIN_PAYMENTS>3</…>`, we adopt the smaller value (2) so the
     cashier can indeed pick 2 installments (displayed as 0 + 1).

5. **Webhook callback fixed**
   • Method `_from_webhook()` exists and is connected to `enqueue_qs`, so
     `WebhookServer` can safely post Quick-Sale requests back to the GUI
     thread.

The rest of the logic (auto-EOD, recovery, spinner prompts, etc.) is
carried over unchanged from v4.5.
"""

from __future__ import annotations

import re
from typing import Dict, List

from PyQt5.QtCore    import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui     import QFont
from PyQt5.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QLabel, QVBoxLayout, QWidget
)

from dialogs   import CreditTermDlg
from receipt   import save_receipt
from utils     import rand_session, to_minor, des3_decrypt
from xml_sign  import env_xml
from config    import save_mac
from prompts   import show_prompt, hide_prompt, show_spinner, hide_spinner


# ═══════════════════════════════════════════════════════════
#        Mini dialog – Regular / Manual  (default Regular)
# ═══════════════════════════════════════════════════════════
class _ManualChoiceDlg(QDialog):
    """Ask cashier to choose Regular (default) or Manual entry."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowTitle("בחר סוג הזנה")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFont(QFont("Segoe UI", 10))

        lay = QVBoxLayout(self)
        form = QFormLayout(); lay.addLayout(form)

        self.cmb = QComboBox()
        self.cmb.addItem("רגיל (תקני)", False)       # data = is_manual?
        self.cmb.addItem("ידני (הקלדת כרטיס)", True)
        form.addRow(QLabel("סוג עסקה:"), self.cmb)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("אישור")
        btns.button(QDialogButtonBox.Cancel).setText("ביטול")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def is_manual(self) -> bool:
        return bool(self.cmb.currentData())


# ═══════════════════════════════════════════════════════════
#                       QuickSaleMixin
# ═══════════════════════════════════════════════════════════
class QuickSaleMixin:
    """
    Mix-in containing the full Quick-Sale FSM.
    Must be first in the MainWindow MRO so that its `_send()` etc.
    refer to MainWindow methods.
    """

    # Webhook thread → GUI thread bridge
    enqueue_qs = pyqtSignal(float, str)

    # ───────────────────────────────
    # ctor
    # ───────────────────────────────
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enqueue_qs.connect(self.quick_sale)

        # Runtime state
        self.qs_queue: list[tuple[float, str]] = []
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

    # ───────────────────────────────
    # Webhook callback  (background thread)
    # ───────────────────────────────
    @pyqtSlot(float, str)
    def _from_webhook(self, amount: float, tran_type: str):
        """Called by WebhookServer; hops to GUI thread via enqueue_qs."""
        self.enqueue_qs.emit(amount, tran_type)

    # ───────────────────────────────
    # Public entry
    # ───────────────────────────────
    def quick_sale(self, amount: float, tran_type: str = "01"):
        self.qs_queue.append((amount, tran_type))
        self._start_next_qs()

    # ───────────────────────────────
    # Queue helpers
    # ───────────────────────────────
    def _start_next_qs(self):
        if self.qs_active or not self.qs_queue:
            return
        amount, code = self.qs_queue.pop(0)
        self._launch_qs(amount, code)

    # ───────────────────────────────
    # Launcher
    # ───────────────────────────────
    def _launch_qs(self, amount: float, tran_type: str):
        hide_prompt(); hide_spinner()

        dlg = _ManualChoiceDlg()
        if dlg.exec_() != QDialog.Accepted:
            show_prompt("העסקה בוטלה", 3000)
            self._qs_done()
            return
        manual = dlg.is_manual()

        show_spinner("מעבד תשלום…")

        self.session         = rand_session()
        self._current_amount = amount
        self.qs_amount.setValue(amount)

        self.qs_defaults = {
            "timeout": "60",
            "restrict_token": "0",
            "manual": manual,
            "manual_reason": "SIG" if manual else "",
            "tran_type": tran_type,
            "amount": to_minor(amount),
            "currency": "376",
            "cash": None,
            "fx": False,
            "fx_to": "",
            "fx_amt": None,
            "use_token": False,
            "token_val": "",
            "service_type": "",
            "ctls": True,
            "allow_cancel": True,
            "unattended": False,
            "operation": "04",
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

    # ───────────────────────────────
    # XML body helpers
    # ───────────────────────────────
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
        """Add payment fields; PAYMENTS_NUMBER = pay-1 for installments."""
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

    # ───────────────────────────────
    # Send helpers
    # ───────────────────────────────
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

    def _send_finish_tran(self):
        self._send(env_xml("SESSION", "FINISH_TRAN", self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key))

    # ───────────────────────────────
    # Cancel / recovery helpers
    # ───────────────────────────────
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

    def _send_void(self):
        body = (
            "<TRANSACTION_DETAILS>"
            "<OPERATION>04</OPERATION><TRAN_TYPE>01</TRAN_TYPE>"
            "<MTI>400</MTI>"
            f"<TRANS_ID>{self._trans_id}</TRANS_ID>"
            "<TRANSACTION_AMOUNT>000000000000</TRANSACTION_AMOUNT>"
            "<ORIGINAL_CURRENCY>376</ORIGINAL_CURRENCY>"
            "</TRANSACTION_DETAILS>"
        )
        self._qs_phase = "VOID"
        self._send(env_xml("PAYMENT", "AUTHORIZE", self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key, body))

    # ───────────────────────────────
    # Discovery → Authorize
    # ───────────────────────────────
    def _handle_discovery_ok(self, recv: str):
        """Called only after RESULT_CODE 0 for DISCOVERY."""
        self._trans_id = re.search(r"<TRANS_ID>([^<]+)</TRANS_ID>", recv).group(1)

        flags = {k: bool(re.search(fr"<TERMS_{k.upper()}>1</TERMS_{k.upper()}>", recv))
                 for k in ("regular", "special", "immediate", "credit", "installments")}

        is_debit = (
            (flags.get("immediate") and not any(flags[x] for x in (
                "regular", "special", "credit", "installments")))
            or bool(re.search(r"<BRAND>\s*08\s*</BRAND>", recv))
        )
        if is_debit:
            show_prompt("זיהוי כרטיס נכשל", 4000)
            self._send_cancel()
            return

        def _iv(tag: str, default: int) -> int:
            m = re.search(fr"<{tag}>(\d+)", recv)
            return int(m.group(1)) if m else default

        # Allow 2 installments even if CREDIT_MIN_PAYMENTS is 3
        mn = max(2, min(_iv("MIN_PAYMENTS", 2), _iv("CREDIT_MIN_PAYMENTS", 2)))
        mx = max(2, max(_iv("MAX_PAYMENTS", 36), _iv("CREDIT_MAX_PAYMENTS", 36)))

        dlg = CreditTermDlg(flags, mn, mx, self._current_amount, None)
        if dlg.exec_() != QDialog.Accepted:
            show_prompt("העסקה לא אושרה", 4000)
            self._send_cancel()
            return
        ct, pay, first, nxt = dlg.data()

        self._qs_phase = "AUTHORIZE"
        self._send(env_xml(
            "PAYMENT", "AUTHORIZE", self.session,
            bool(int(self.train_combo.currentText())), self.mac_key,
            self._auth_body(self.qs_defaults, ct, pay, first, nxt)
        ))
        show_prompt("נא המתן לאישור עסקה")

    # ───────────────────────────────
    # Finish current QS & start next
    # ───────────────────────────────
    def _qs_done(self):
        hide_spinner()
        self.qs_active = False
        self._qs_phase = ""
        self._start_next_qs()

    # ═══════════════════════════════════════════════════════
    # CENTRAL RESPONSE HANDLER  (full FSM)
    # ═══════════════════════════════════════════════════════
    def _handle_response(self, sent: str, recv: str):
        """Dispatcher for every XML reply from _send()."""
        self.sent_log.append(sent)
        self.recv_log.append(recv)

        # Auto-cancel path
        if "<RESULT_CODE>2<" in recv and "<COMMAND>CANCEL" not in sent:
            show_prompt("העסקה לא אושרה", 4000)
            self._schedule_cancel()
            return

        # --------------------------------------------------
        # Quick-Sale FSM
        # --------------------------------------------------
        if self.qs_active:

            # ---------- PING ----------
            if "<COMMAND>PING" in sent:
                if "<RESULT_CODE>0<" in recv:
                    self._send_status()
                else:
                    show_prompt("העסקה לא אושרה", 4000)
                    self._qs_done()
                return

            # ---------- STATUS ----------
            if "<COMMAND>STATUS" in sent:
                if "<RESULT_CODE>0<" in recv:
                    expected = self.termid_field.text().strip()
                    m_tid = re.search(r"<SHVA_TERM_ID>(\d+)</SHVA_TERM_ID>", recv)
                    if expected and m_tid and m_tid.group(1) != expected:
                        self._crit("SHVA ID", "מזהה SHVA אינו תקין")
                        show_prompt("העסקה לא אושרה", 4000)
                        self._send_cancel(); self._qs_done(); return
                    if re.search(r"<SHVA_STATUS>(?!1)", recv):
                        self.cmd_eod()
                    self._send_start_tran()
                else:
                    show_prompt("העסקה לא אושרה", 4000); self._qs_done()
                return

            # ---------- START_TRAN ----------
            if "<COMMAND>START_TRAN" in sent:
                if "<RESULT_CODE>0<" in recv:
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
                if "<RESULT_CODE>0<" in recv:
                    self._handle_discovery_ok(recv)
                else:
                    show_prompt("זיהוי כרטיס נכשל", 4000); self._qs_done()
                return

            # ---------- AUTHORIZE ----------
            if "<COMMAND>AUTHORIZE" in sent:

                if "<EVENT>COMPLETED" in recv:
                    ok = "<RESULT_CODE>0<" in recv
                    m_auth = re.search(r"<ISSUER_AUTH_NUM>([^<]*)</ISSUER_AUTH_NUM>", recv)
                    auth_no = (m_auth.group(1) if m_auth else "").strip()
                    issuer_decline = bool(re.search(r"נדחה\s*ע.?י\s*חברה", recv))

                    if ok and auth_no and auth_no != "0":
                        msg = f"סיום עסקה\nהעסקה אושרה בהצלחה\nמספר אישור {auth_no}"
                    else:
                        msg = "סיום עסקה\nהעסקה נכשלה"
                        if issuer_decline:
                            msg += "\nנדחה ע\"י חברה"

                    show_prompt(msg, 4000)

                    save_receipt(recv)
                    try:
                        with open("receipt.json", "rb") as fp:
                            self.webhook.publish(fp.read())
                    except Exception:
                        pass

                    self._send_finish_tran()
                    return

                # Incomplete → recovery
                self._info("Recovery", "AUTHORIZE incomplete – querying details")
                self._send_get_details()
                return

            # ---------- GET_DETAILS ----------
            if "<COMMAND>GET_TRAN_DETAILS" in sent:
                approved = ("<RESULT_CODE>0<" in recv and
                            "<TRANSACTIONS>" in recv and
                            "<TRANSACTIONS></TRANSACTIONS>" not in recv)
                if approved:
                    self._info("Recovery", "עסקה קיימת ואושרה – שולח VOID")
                    self._send_void()
                else:
                    show_prompt("העסקה לא אושרה", 4000)
                    self._crit("Recovery", "No matching transaction – abort queue")
                    self._qs_done()
                return

            # ---------- VOID flow ----------
            if self._qs_phase == "VOID" and "<COMMAND>AUTHORIZE" in sent:
                self._send_finish_tran()
                return

            # ---------- FINISH_TRAN ----------
            if "<COMMAND>FINISH_TRAN" in sent:
                self._qs_done()
                return

        # --------------------------------------------------
        # CANCEL outside Quick-Sale
        # --------------------------------------------------
        if "<COMMAND>CANCEL" in sent:
            show_prompt("העסקה לא אושרה", 4000)
            self._send_finish_tran()
            return

        # --------------------------------------------------
        # Register / EXCHANGE_KEYS side effects
        # --------------------------------------------------
        if "<COMMAND>REGISTER" in sent and "<EVENT>COMPLETED" in recv:
            self.ktk_field.setEnabled(True)
            self.key_btn.setEnabled(True)

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
