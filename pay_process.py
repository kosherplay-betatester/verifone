#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pay_process.py  –  FULL FILE  (v4.2‑r18 • GET_DETAILS standalone vs recovery fix + missing finish call)

"""
Verifone‑P400 Quick‑Sale finite‑state machine
============================================

r18 (2025‑07‑16)
----------------
• Standalone GET_TRAN_DETAILS (admin or webhook) now always publishes its receipt JSON,
  even if not in a Quick‑Sale flow.
• Recovery GET_TRAN_DETAILS (in‑flow) falls through the FSM so that on error it will
  send FINISH_TRAN (correctly), hide the spinner, and clear the transaction.
• Signal‑based webhook bridge and manual‑entry chooser unchanged.
• All other logic unchanged.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Dict, List, Tuple

from PyQt5.QtCore    import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui     import QFont
from PyQt5.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QComboBox,
    QVBoxLayout, QWidget
)

from dialogs   import CreditTermDlg
from receipt   import save_receipt
from utils     import rand_session, to_minor, des3_decrypt
from xml_sign  import env_xml
from config    import save_mac
from prompts   import show_prompt, hide_prompt, show_spinner, hide_spinner


# ───────────────────────────────────────────────────────────
#  Tiny XML helpers
# ───────────────────────────────────────────────────────────
def _tag(xml: str, name: str, default: str = "") -> str:
    m = re.search(fr"<{name}>([^<]*)</{name}>", xml)
    return m.group(1) if m else default

def _iv(xml: str, tag: str) -> int | None:
    m = re.search(fr"<{tag}>(\d+)", xml)
    return int(m.group(1)) if m else None

def _result_code(xml: str) -> int:
    try:
        return int(_tag(xml, "RESULT_CODE", "-1"))
    except ValueError:
        return -1

def _issuer_auth(xml: str) -> str:
    num = _tag(xml, "ISSUER_AUTH_NUM").strip()
    return num if num and not re.fullmatch(r"0+", num) else ""

def _pan_all_zeros(xml: str) -> bool:
    for tg in ("FULL_PAN", "MASKED_PAN"):
        if re.fullmatch(r"0+", _tag(xml, tg, "")):
            return True
    return False

def _masked_pan_leading_zeros(xml: str) -> bool:
    pan = _tag(xml, "MASKED_PAN", "")
    return bool(re.match(r"0{6,}", pan)) if pan else False

def _is_bad_card(xml: str) -> bool:
    if "תקלה בקבלת נתוני כרטיס" in _tag(xml, "POS_TEXT", ""):
        return True
    if _masked_pan_leading_zeros(xml):
        return True
    if _result_code(xml) == 20:
        return True
    if "Bad Card Read" in _tag(xml, "RESULT_TEXT", ""):
        return True
    if re.search(r"<AGGREGATED_CODE>\s*6\s*</AGGREGATED_CODE>", xml):
        return True
    return False


# ───────────────────────────────────────────────────────────
#  Manual / Regular choice dialog (v5.2 style)
# ───────────────────────────────────────────────────────────
class _ManualChoiceDlg(QDialog):
    """Ask cashier to choose Regular or Manual entry."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowTitle("בחר סוג הזנה")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setFont(QFont("Segoe UI", 10))

        lay  = QVBoxLayout(self)
        form = QFormLayout()
        lay.addLayout(form)

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

    def is_manual(self) -> bool:
        return bool(self.cmb.currentData())


# ══════════════════════════════════════════════════════════
#                        QuickSaleMixin
# ══════════════════════════════════════════════════════════
class QuickSaleMixin:
    """
    Mix‑in holding the full Quick‑Sale FSM.
    Must be first in MainWindow MRO.
    """

    enqueue_qs = pyqtSignal(float, str)  # webhook thread → GUI thread

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enqueue_qs.connect(self.quick_sale)

        # runtime state
        self.qs_queue: List[Tuple[float, str]] = []
        self.qs_active        = False
        self.session          = ""
        self._current_amount  = 0.0
        self._qs_phase        = ""
        self._trans_id        = ""
        self.qs_defaults: Dict = {}
        self.qs_start_body    = ""
        self._awaiting_cancel = False
        self._cancel_timer    = None
        self._receipt_sent    = False
        self._manual_key_exchange = False

    @pyqtSlot(float, str)
    def _from_webhook(self, amount: float, tran_type: str):
        """Called by WebhookServer (background thread)."""
        self.enqueue_qs.emit(amount, tran_type)

    def quick_sale(self, amount: float, tran_type: str = "01"):
        self.qs_queue.append((amount, tran_type))
        self._start_next_qs()

    def _start_next_qs(self):
        if self.qs_active or not self.qs_queue:
            return
        amt, code = self.qs_queue.pop(0)
        self._launch_qs(amt, code)

    def _launch_qs(self, amount: float, tran_type: str):
        hide_prompt()
        hide_spinner()

        dlg = _ManualChoiceDlg(None)
        if dlg.exec_() != QDialog.Accepted:
            show_prompt("העסקה בוטלה", 3000)
            self._qs_done()
            return

        manual = dlg.is_manual()
        show_spinner("מעבד תשלום…")

        self.session         = rand_session()
        self._current_amount = amount
        self.qs_amount.setValue(amount)

        self._receipt_sent    = False
        self._awaiting_cancel = False

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

    def _publish_json_receipt(self, source_xml: str):
        if self._receipt_sent:
            return
        try:
            save_receipt(source_xml)
            with open("receipt.json", "rb") as fp:
                self.webhook.publish(fp.read())
        except Exception:
            payload = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "result": (_tag(source_xml, "POS_TEXT")
                           or _tag(source_xml, "RESULT_TEXT")
                           or "N/A"),
                "raw": source_xml,
            }
            self.webhook.publish(json.dumps(payload, ensure_ascii=False).encode())
        self._receipt_sent = True

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
        if o["cash"] is not None:
            parts.append(f"<CASH_AMOUNT>{o['cash']}</CASH_AMOUNT>")
        if o["fx"]:
            parts += [
                f"<CONVERTED_AMOUNT>{o['fx_amt']}</CONVERTED_AMOUNT>",
                f"<CONVERTED_CURRENCY>{o['fx_to']}</CONVERTED_CURRENCY>",
            ]
        if o["use_token"] and o["token_val"]:
            parts.append(f"<CARD_TOKEN>{o['token_val']}</CARD_TOKEN>")
        if o["service_type"]:
            parts.append(f"<SERVICE_TYPE>{o['service_type']}</SERVICE_TYPE>")
        parts.append(f"<OPERATION>{o['operation']}</OPERATION></TRANSACTION_DETAILS>")
        return "".join(parts)

    def _auth_body(self, base: Dict, ct: str,
                   pay: int, first: float, nxt: float) -> str:
        body = self._disc_body(base)
        if ct == "8" and pay:
            pay -= 1
        extra = f"<CREDIT_TERMS>{ct}</CREDIT_TERMS>"
        if pay:
            extra += f"<PAYMENTS_NUMBER>{pay:02d}</PAYMENTS_NUMBER>"
        if first:
            extra += f"<FIRST_PAYMENT_AMOUNT>{to_minor(first)}</FIRST_PAYMENT_AMOUNT>"
        if nxt:
            extra += f"<NEXT_PAYMENT_AMOUNT>{to_minor(nxt)}</NEXT_PAYMENT_AMOUNT>"
        return body.replace("</TRANSACTION_DETAILS>", extra + "</TRANSACTION_DETAILS>")

    def _send(self, xml: str):
        super()._send(xml)

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

    def _handle_discovery_ok(self, xml: str):
        if _is_bad_card(xml):
            show_prompt("כרטיס אינו נתמך", 4000)
            self._publish_json_receipt(xml)
            self._send_cancel()
            self._qs_done()
            return

        self._trans_id = _tag(xml, "TRANS_ID")

        flags = {
            k: bool(re.search(fr"<TERMS_{k.upper()}>1</TERMS_{k.upper()}>", xml))
            for k in ("regular", "special", "immediate", "credit", "installments")
        }

        if not any(flags.values()) or _pan_all_zeros(xml):
            show_prompt("כרטיס אינו נתמך", 4000)
            self._publish_json_receipt(xml)
            self._send_cancel()
            self._qs_done()
            return

        debit = (
            (flags["immediate"] and not any(flags[x] for x in ("regular", "special", "credit", "installments")))
            or bool(re.search(r"<BRAND>\s*08\s*</BRAND>", xml))
        )
        if debit:
            flags = {"immediate": True}

        mn = max(2, _iv(xml, "MIN_PAYMENTS") or (_iv(xml, "CREDIT_MIN_PAYMENTS") or 2))
        mx = max(2, _iv(xml, "CREDIT_MAX_PAYMENTS") or 36)

        dlg = CreditTermDlg(flags, mn, mx, self._current_amount, None)
        if dlg.exec_() != QDialog.Accepted:
            show_prompt("העסקה לא אושרה", 4000)
            self._send_cancel()
            self._qs_done()
            return

        ct, pay, first, nxt = dlg.data()

        self._qs_phase = "AUTHORIZE"
        self._send(env_xml(
            "PAYMENT", "AUTHORIZE", self.session,
            bool(int(self.train_combo.currentText())), self.mac_key,
            self._auth_body(self.qs_defaults, ct, pay, first, nxt)
        ))
        show_prompt("נא המתן לאישור עסקה")

    def _qs_done(self):
        hide_spinner()
        self.qs_active = False
        self._qs_phase = ""
        self._start_next_qs()

    def _handle_response(self, sent: str, recv: str):
        self.sent_log.append(sent)
        self.recv_log.append(recv)

        # Standalone GET_TRAN_DETAILS (admin/webhook)
        if "<COMMAND>GET_TRAN_DETAILS" in sent and not self.qs_active:
            self._receipt_sent = False
            self._publish_json_receipt(recv)
            return

        # SHVA‑ID guard
        exp_tid = self.termid_field.text().strip()
        got_tid = _tag(recv, "SHVA_TERM_ID")
        if exp_tid and got_tid and exp_tid != got_tid:
            self._crit("SHVA ID", "מספר SHVA אינו תקין , אנא פנה לטכנאי")
            show_prompt("מספר SHVA אינו תקין , אנא פנה לטכנאי", 4000)
            if self.qs_active:
                self._send_cancel()
                self._qs_done()
            return

        # RESULT_CODE 2 → auto‑cancel
        if _result_code(recv) == 2 and "<COMMAND>CANCEL" not in sent:
            show_prompt("העסקה לא אושרה", 4000)
            self._schedule_cancel()
            self._publish_json_receipt(recv)
            return

        if self.qs_active:

            if "<COMMAND>PING" in sent:
                if _result_code(recv) == 0:
                    self._send_status()
                else:
                    show_prompt("העסקה לא אושרה", 4000)
                    self._qs_done()
                return

            if "<COMMAND>STATUS" in sent:
                if _result_code(recv) == 0:
                    if re.search(r"<SHVA_STATUS>(?!1)", recv):
                        self.cmd_eod()
                    self._send_start_tran()
                else:
                    show_prompt("העסקה לא אושרה", 4000)
                    self._qs_done()
                return

            if "<COMMAND>START_TRAN" in sent:
                if _result_code(recv) == 0:
                    self._qs_phase = "DISCOVERY"
                    self._send(env_xml(
                        "PAYMENT", "DISCOVERY", self.session,
                        bool(int(self.train_combo.currentText())), self.mac_key,
                        self._disc_body(self.qs_defaults)
                    ))
                    show_prompt("זיהוי כרטיס", 3000)
                else:
                    show_prompt("העסקה לא אושרה", 4000)
                    self._qs_done()
                return

            if "<COMMAND>DISCOVERY" in sent:
                if _result_code(recv) == 0:
                    self._handle_discovery_ok(recv)
                else:
                    show_prompt("כרטיס אינו נתמך", 4000)
                    self._publish_json_receipt(recv)
                    self._qs_done()
                return

            if "<COMMAND>AUTHORIZE" in sent:
                if "<EVENT>COMPLETED" in recv:
                    auth = _issuer_auth(recv)
                    success = (_result_code(recv) == 0 and auth)
                    msg = (
                        f"סיום עסקה\nהעסקה אושרה בהצלחה\nמספר אישור {auth}"
                        if success else "סיום עסקה\nהעסקה נכשלה"
                    )
                    show_prompt(msg, 4000)
                    self._publish_json_receipt(recv)
                    self._send_finish_tran()
                    return

                # Recovery: AUTHORIZE incomplete → GET_DETAILS
                self._info("Recovery", "AUTHORIZE incomplete – GET_DETAILS")
                body = f"<TRANS_ID>{self._trans_id}</TRANS_ID>"
                self._qs_phase = "GET_DETAILS"
                self._send(env_xml(
                    "REPORT", "GET_TRAN_DETAILS", self.session,
                    bool(int(self.train_combo.currentText())), self.mac_key, body
                ))
                return

            if "<COMMAND>GET_TRAN_DETAILS" in sent:
                # In‑flow GET_DETAILS → publish + finish or void
                auth = _issuer_auth(recv)
                approved = (
                    _result_code(recv) == 0
                    and "<TRANSACTIONS></TRANSACTIONS>" not in recv
                    and auth
                )
                msg = (
                    f"סיום עסקה\nהעסקה אושרה בהצלחה\nמספר אישור {auth}"
                    if approved else "סיום עסקה\nהעסקה נכשלה"
                )
                show_prompt(msg, 4000)
                self._publish_json_receipt(recv)
                if approved:
                    self._qs_phase = "VOID"
                    body = (
                        "<TRANSACTION_DETAILS><OPERATION>04</OPERATION>"
                        "<TRAN_TYPE>01</TRAN_TYPE><MTI>400</MTI>"
                        f"<TRANS_ID>{self._trans_id}</TRANS_ID>"
                        "<TRANSACTION_AMOUNT>000000000000</TRANSACTION_AMOUNT>"
                        "<ORIGINAL_CURRENCY>376</ORIGINAL_CURRENCY>"
                        "</TRANSACTION_DETAILS>"
                    )
                    self._send(env_xml(
                        "PAYMENT", "AUTHORIZE", self.session,
                        bool(int(self.train_combo.currentText())), self.mac_key, body
                    ))
                else:
                    # <—— FIXED: actually call the method, not reference it
                    self._send_finish_tran()
                return

            if self._qs_phase == "VOID" and "<COMMAND>AUTHORIZE" in sent:
                self._send_finish_tran()
                return

            if "<COMMAND>FINISH_TRAN" in sent:
                if not self._receipt_sent:
                    self._publish_json_receipt(recv)
                self._qs_done()
                return

        if "<COMMAND>CANCEL" in sent:
            show_prompt("העסקה לא אושרה", 4000)
            self._publish_json_receipt(recv)
            self._send_finish_tran()
            return

        if ("<COMMAND>EXCHANGE_KEYS" in sent and "<MAC_KEY>" in recv
                and getattr(self, "_manual_key_exchange", False)):
            m = re.search(r"<MAC_KEY>([^<]+)</MAC_KEY>", recv)
            if m:
                try:
                    self.mac_key = des3_decrypt(
                        self.ktk_field.text().strip(), m.group(1)
                    )
                    save_mac(self.mac_key)
                    self.mac_view.setText(self.mac_key)
                    for b in (
                        self.status_btn, self.eod_btn, self.qs_btn,
                        self.free_send_btn, *self.free_cmds
                    ):
                        b.setEnabled(True)
                except Exception as exc:
                    self._crit("Decrypt", str(exc))
                finally:
                    self._manual_key_exchange = False
