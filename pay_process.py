#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pay_process.py  –  FULL FILE  (v5.33 • manual-receipt hot-fix)

"""
Verifone-P400 Quick-Sale FSM  –  2025-07-13
------------------------------------------

∆ NEW (v5.33)
~~~~~~~~~~~~~
• After *FINISH_TRAN* is answered, the code now:
    1. Tries to write/overwrite receipt.json from that reply.
    2. Publishes the file (or, if it still doesn’t exist, a fallback
       `{"raw": "<xml>…</xml>"}`) to WebhookServer.
  ⇒  Manual payments (card typed in) show up on `/receipt` immediately.

Other behaviour from v5.32 (instant webhook release, SHVA-ID guard,
debit logic, receipt on every result) is intact.
"""

from __future__ import annotations
import json
import re
from typing import Dict, List

from PyQt5.QtCore    import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtGui     import QFont
from PyQt5.QtWidgets import (
    QWidget, QDialog, QDialogButtonBox, QComboBox,
    QFormLayout, QVBoxLayout, QLabel,
)

from dialogs   import CreditTermDlg
from receipt   import save_receipt
from utils     import rand_session, to_minor, des3_decrypt
from xml_sign  import env_xml
from prompts   import show_prompt, hide_prompt, show_spinner, hide_spinner
from config    import save_mac


# ───────────── helper mini-routines ─────────────
def _result_code(xml: str) -> int:
    m = re.search(r"<RESULT_CODE>\s*(\d+)\s*</RESULT_CODE>", xml)
    return int(m.group(1)) if m else -1


def _issuer_auth_num(xml: str) -> str:
    m = re.search(r"<ISSUER_AUTH_NUM>([^<]*)</ISSUER_AUTH_NUM>", xml)
    if not m:
        return ""
    num = m.group(1).strip()
    return num if num and not re.fullmatch(r"0+", num) else ""


def _pan_all_zeros(xml: str) -> bool:
    for tag in ("FULL_PAN", "MASKED_PAN"):
        m = re.search(fr"<{tag}>([^<]*)</{tag}>", xml)
        if m and re.fullmatch(r"0+", m.group(1)):
            return True
    return False


def _save_receipt_always(xml: str) -> None:
    """Write receipt.json, or silently ignore if parser fails."""
    try:
        save_receipt(xml)
    except Exception:
        pass


# ─────────────── small “Regular / Manual” dialog ───────────────
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

    def is_manual(self) -> bool:
        return bool(self.cmb.currentData())


# ═════════════════════════ Quick-Sale mix-in ════════════════════════
class QuickSaleMixin:
    enqueue_qs = pyqtSignal(float, str)         # webhook → GUI thread

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.enqueue_qs.connect(self.quick_sale)

        # FSM runtime
        self.qs_queue: List[tuple[float, str]] = []
        self.qs_active = False
        self.session   = ""
        self._current_amount = 0.0
        self._qs_phase = ""
        self._trans_id = ""
        self.qs_defaults: Dict = {}
        self.qs_start_body = ""
        self._awaiting_cancel = False
        self._cancel_timer    = None
        self._manual_key_exchange = False

    # ───────── webhook bridge ─────────
    @pyqtSlot(float, str)
    def _from_webhook(self, amt: float, code: str):
        self.enqueue_qs.emit(amt, code)

    def quick_sale(self, amt: float, code: str = "01"):
        self.qs_queue.append((amt, code)); self._kick_next()

    def _kick_next(self):
        if self.qs_active or not self.qs_queue:
            return
        amt, code = self.qs_queue.pop(0)
        self._launch_qs(amt, code)

    # ───────── phase 0 – launcher ─────────
    def _launch_qs(self, amount: float, tran_type: str):
        hide_prompt(); hide_spinner()
        dlg = _ManualChoiceDlg(self)
        if dlg.exec_() != QDialog.Accepted:
            show_prompt("העסקה בוטלה", 3000); return
        manual = dlg.is_manual()

        show_spinner("מעבד תשלום…")
        self.session         = rand_session()
        self._current_amount = amount
        self.qs_amount.setValue(amount)

        self.qs_defaults = {
            "timeout": "60", "restrict_token": "0",
            "manual": manual, "manual_reason": "SIG" if manual else "",
            "tran_type": tran_type, "amount": to_minor(amount), "currency": "376",
            "cash": None, "fx": False, "fx_to": "", "fx_amt": None,
            "use_token": False, "token_val": "", "service_type": "",
            "ctls": True, "allow_cancel": True, "unattended": False,
            "operation": "04",
        }
        self.qs_start_body = (
            "<INVOICE>100000</INVOICE>"
            f"<POS_TYPE>{self.pos_combo.currentText()}</POS_TYPE>"
        )

        self.qs_active = True
        self._qs_phase = "PING"
        self._send(env_xml(
            "ADMIN", "PING", self.session,
            bool(int(self.train_combo.currentText())), self.mac_key
        ))

    # ───────── body builders (DISCOVERY / AUTHORIZE) ─────────
    def _disc_body(self, o: Dict) -> str:
        return (
            f"<TIMEOUT>{o['timeout']}</TIMEOUT>"
            "<TRANSACTION_DETAILS>"
            f"<RESTRICT_TOKEN>{o['restrict_token']}</RESTRICT_TOKEN>"
            f"<MANUAL>{int(o['manual'])}</MANUAL>"
            f"<CTLS>{int(o['ctls'])}</CTLS>"
            f"<ALLOW_CANCEL>{int(o['allow_cancel'])}</ALLOW_CANCEL>"
            f"<UNATTENDED>{int(o['unattended'])}</UNATTENDED>"
            f"<TRAN_TYPE>{o['tran_type']}</TRAN_TYPE>"
            "<MTI>100</MTI>"
            f"<TRANSACTION_AMOUNT>{o['amount']}</TRANSACTION_AMOUNT>"
            f"<ORIGINAL_CURRENCY>{o['currency']}</ORIGINAL_CURRENCY>"
            f"<OPERATION>{o['operation']}</OPERATION>"
            "</TRANSACTION_DETAILS>"
        )

    def _auth_body(self, base: Dict, ct: str,
                   pay: int, first: float, nxt: float) -> str:
        body    = self._disc_body(base)
        add_pay = pay - 1 if ct == "8" and pay else pay
        extra   = f"<CREDIT_TERMS>{ct}</CREDIT_TERMS>"
        if pay:   extra += f"<PAYMENTS_NUMBER>{add_pay:02d}</PAYMENTS_NUMBER>"
        if first: extra += f"<FIRST_PAYMENT_AMOUNT>{to_minor(first)}</FIRST_PAYMENT_AMOUNT>"
        if nxt:   extra += f"<NEXT_PAYMENT_AMOUNT>{to_minor(nxt)}</NEXT_PAYMENT_AMOUNT>"
        return body.replace("</TRANSACTION_DETAILS>",
                            extra + "</TRANSACTION_DETAILS>")

    # ───────── send helpers ─────────
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
        """
        Release FSM *before* sending FINISH_TRAN so /pay is free, but still
        log the final reply when it arrives.
        """
        self._qs_done()
        self._send(env_xml("SESSION", "FINISH_TRAN", self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key))

    # ───────── cancel helpers ─────────
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

    # ───────── DISCOVERY → AUTHORIZE helper ─────────
    def _handle_discovery_ok(self, xml: str):
        self._trans_id = re.search(r"<TRANS_ID>([^<]+)</TRANS_ID>", xml).group(1)

        flags = {k: bool(re.search(fr"<TERMS_{k.upper()}>1</TERMS_{k.upper()}>", xml))
                 for k in ("regular", "special", "credit", "installments", "immediate")}

        credit = any(flags[k] for k in ("regular", "special", "credit", "installments"))
        brand8 = bool(re.search(r"<BRAND>\s*08\s*</BRAND>", xml))
        debit  = (flags["immediate"] and not credit) or brand8
        unsupported = (not credit and not debit) or _pan_all_zeros(xml)

        if unsupported:
            show_prompt("כרטיס אינו נתמך", 4000)
            self._send_cancel(); self._qs_done(); return

        if debit:
            for k in flags:
                flags[k] = (k == "immediate")

        def _iv(tag: str, default: int) -> int:
            m = re.search(fr"<{tag}>(\d+)", xml)
            return int(m.group(1)) if m else default

        mn = max(2, min(_iv("MIN_PAYMENTS", 2), _iv("CREDIT_MIN_PAYMENTS", 2)))
        mx = max(2, max(_iv("MAX_PAYMENTS", 36), _iv("CREDIT_MAX_PAYMENTS", 36)))

        dlg = CreditTermDlg(flags, mn, mx, self._current_amount, None)
        if dlg.exec_() != QDialog.Accepted:
            show_prompt("העסקה לא אושרה", 4000)
            self._send_cancel(); self._qs_done(); return
        ct, pay, first, nxt = dlg.data()

        self._qs_phase = "AUTHORIZE"
        self._send(env_xml(
            "PAYMENT", "AUTHORIZE", self.session,
            bool(int(self.train_combo.currentText())), self.mac_key,
            self._auth_body(self.qs_defaults, ct, pay, first, nxt)
        ))
        show_prompt("נא המתן לאישור עסקה")

    # ───────── housekeeping ─────────
    def _qs_done(self):
        hide_spinner()
        self.qs_active = False
        self._qs_phase = ""
        self._kick_next()

    # ───────── helper: publish latest receipt or raw xml ─────────
    def _publish_receipt_or_raw(self, xml: str):
        try:
            with open("receipt.json", "rb") as fp:
                self.webhook.publish(fp.read())
                return
        except Exception:
            pass  # fall through to raw

        # fallback – wrap raw xml so listener gets *something*
        raw_payload = json.dumps({"raw": xml}, ensure_ascii=False).encode()
        self.webhook.publish(raw_payload)

    # ═════════════ central response handler ═════════════
    def _handle_response(self, sent: str, recv: str):
        self.sent_log.append(sent); self.recv_log.append(recv)

        # --- SHVA-ID guard (runs on every reply) ---
        expected_tid = getattr(self, "termid_field", None)
        if expected_tid:
            expected_tid = expected_tid.text().strip()
            if expected_tid:
                m_tid = re.search(r"<SHVA_TERM_ID>(\d+)</SHVA_TERM_ID>", recv)
                if m_tid and m_tid.group(1) != expected_tid:
                    self._crit("SHVA ID", "מספר SHVA אינו תקין , אנא פנא לטכנאי")
                    show_prompt("מספר SHVA אינו תקין , אנא פנא לטכנאי", 4000)
                    if self.qs_active:
                        self._send_cancel(); self._qs_done()
                    return

        # --- generic auto-cancel on RESULT_CODE ≠ 0 ---
        if _result_code(recv) != 0 and "<COMMAND>CANCEL" not in sent:
            show_prompt("העסקה לא אושרה", 4000)
            self._schedule_cancel()
            _save_receipt_always(recv)
            self._publish_receipt_or_raw(recv)
            return

        # --- Quick-Sale FSM ---
        if self.qs_active:
            if "<COMMAND>PING" in sent:
                self._send_status(); return

            if "<COMMAND>STATUS" in sent:
                self._send_start_tran(); return

            if "<COMMAND>START_TRAN" in sent:
                disc_xml = env_xml(
                    "PAYMENT", "DISCOVERY", self.session,
                    bool(int(self.train_combo.currentText())), self.mac_key,
                    self._disc_body(self.qs_defaults)
                )
                self._qs_phase = "DISCOVERY"
                show_prompt("זיהוי כרטיס", 3000)
                self._send(disc_xml); return

            if "<COMMAND>DISCOVERY" in sent:
                if "<EVENT>COMPLETED" not in recv:
                    return
                if _result_code(recv) == 0:
                    self._handle_discovery_ok(recv)
                else:
                    show_prompt("כרטיס אינו נתמך", 4000)
                    _save_receipt_always(recv)
                    self._publish_receipt_or_raw(recv)
                    self._send_cancel(); self._qs_done()
                return

            if "<COMMAND>AUTHORIZE" in sent:
                if "<EVENT>COMPLETED" in recv:
                    ok   = _result_code(recv) == 0
                    auth = _issuer_auth_num(recv)
                    success = ok and auth
                    msg = (f"סיום עסקה\nהעסקה אושרה בהצלחה\nמספר אישור {auth}"
                           if success else "סיום עסקה\nהעסקה נכשלה")
                    show_prompt(msg, 4000)
                    _save_receipt_always(recv)
                    self._publish_receipt_or_raw(recv)
                    self._send_finish_tran()
                    return

                self._info("Recovery", "AUTHORIZE incomplete – GET_DETAILS")
                self._send_get_details(); return

            if "<COMMAND>GET_TRAN_DETAILS" in sent:
                ok  = _result_code(recv) == 0
                has = "<TRANSACTIONS></TRANSACTIONS>" not in recv
                proc_ok = bool(re.search(r"<PROCESSOR_ERROR>0</PROCESSOR_ERROR>", recv))
                auth = _issuer_auth_num(recv)
                success = ok and has and proc_ok and auth
                msg = (f"סיום עסקה\nהעסקה אושרה בהצלחה\nמספר אישור {auth}"
                       if success else "סיום עסקה\nהעסקה נכשלה")
                show_prompt(msg, 4000)
                _save_receipt_always(recv)
                self._publish_receipt_or_raw(recv)
                self._send_finish_tran(); return

        # --- FINISH_TRAN arrives *after* FSM was already reset ---
        if "<COMMAND>FINISH_TRAN" in sent:
            _save_receipt_always(recv)
            self._publish_receipt_or_raw(recv)
            return

        # --- explicit CANCEL outside FSM ---
        if "<COMMAND>CANCEL" in sent:
            show_prompt("העסקה לא אושרה", 4000)
            _save_receipt_always(recv)
            self._publish_receipt_or_raw(recv)
            self._qs_done()
            return

        # --- key-exchange housekeeping ---
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
