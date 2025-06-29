#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# pay_process.py  –  FULL FILE  (v4.1 • white-text prompts, unchanged FSM)

"""
Quick-Sale finite-state machine extracted from gui/main_window.py v1.65.

Visual layer
============
Every call to ``show_prompt()`` passes *plain text* (no HTML).  
Because the updated prompts.py stylesheet paints a blue frame
(#3498db fill, #2980b9 outline) with *white* text, all messages now appear
white-on-blue:

    התחלת עסקה
    זיהוי כרטיס
    נא המתן לאישור עסקה
    סיום עסקה
    העסקה אושרה בהצלחה
    מספר אישור XXXXXX
    …
    העסקה נכשלה
    נדחה ע`י חברה

FSM & business logic
====================
Identical to original v1.65:

PING → STATUS → START_TRAN → DISCOVERY → AUTHORIZE → FINISH_TRAN  
Automatic recovery (GET_TRAN_DETAILS + VOID) if AUTHORIZE completes
without EVENT=COMPLETED.  RESULT_CODE 2 triggers auto-cancel, etc.
"""

from __future__ import annotations

import re
from typing import Dict, List

from PyQt5.QtCore    import pyqtSlot, QTimer
from PyQt5.QtWidgets import QDialog

from dialogs   import CreditTermDlg
from receipt   import save_receipt
from utils     import rand_session, to_minor, des3_decrypt
from xml_sign  import env_xml
from config    import save_mac
from prompts   import show_prompt, hide_prompt


# ════════════════════════════════════════════════════════
#                     QuickSaleMixin
# ════════════════════════════════════════════════════════
class QuickSaleMixin:
    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------
    def quick_sale(self, amount: float, tran_type: str = "01"):
        self.qs_queue.append((amount, tran_type))
        self._start_next_qs()

    # ------------------------------------------------------------------
    # Queue helpers
    # ------------------------------------------------------------------
    def _start_next_qs(self):
        if self.qs_active or not self.qs_queue:
            return
        amount, code = self.qs_queue.pop(0)
        self._launch_qs(amount, code)

    def _launch_qs(self, amount: float, code: str):
        hide_prompt()                                  # clear previous overlay
        self.session         = rand_session()
        self._current_amount = amount
        self.qs_amount.setValue(amount)

        # Discovery defaults
        self.qs_defaults = {
            "timeout":        "60",
            "restrict_token": "0",
            "manual":         False,
            "manual_reason":  "",
            "tran_type":      code,
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

        ping = env_xml(
            "ADMIN", "PING", self.session,
            bool(int(self.train_combo.currentText())), self.mac_key
        )
        self._send(ping)

    # ------------------------------------------------------------------
    # Send wrappers
    # ------------------------------------------------------------------
    def _send_status(self):
        self._qs_phase = "STATUS"
        xml = env_xml("ADMIN", "STATUS", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key)
        self._send(xml)

    def _send_start_tran(self):
        self._qs_phase = "START_TRAN"
        xml = env_xml("SESSION", "START_TRAN", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key,
                      self.qs_start_body)
        self._send(xml)
        show_prompt("התחלת עסקה", 3000)

    def _send_finish_tran(self):
        xml = env_xml("SESSION", "FINISH_TRAN", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key)
        self._send(xml)

    def _qs_done(self):
        self.qs_active = False
        self._qs_phase = ""
        self._start_next_qs()

    # ------------------------------------------------------------------
    # Webhook bridge
    # ------------------------------------------------------------------
    @pyqtSlot(float, str)
    def _from_webhook(self, amount: float, tran_type: str):
        self.quick_sale(amount, tran_type)

    # ------------------------------------------------------------------
    # Body builders
    # ------------------------------------------------------------------
    def _disc_body(self, o: Dict) -> str:
        parts: List[str] = [
            f"<TIMEOUT>{o['timeout']}</TIMEOUT>",
            "<TRANSACTION_DETAILS>",
            f"<RESTRICT_TOKEN>{o['restrict_token']}</RESTRICT_TOKEN>",
            f"<MANUAL>{int(o['manual'])}</MANUAL>",
            f"<CTLS>{int(o['ctls'])}</CTLS>",
            f"<ALLOW_CANCEL>{int(o['allow_cancel'])}</ALLOW_CANCEL>",
            f"<UNATTENDED>{int(o['unattended'])}</UNATTENDED>",
            f"<TRAN_TYPE>{o['tran_type']}</TRAN_TYPE>",
            "<MTI>100</MTI><ENTRY_MODE>04</ENTRY_MODE>",
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
        patch = f"<CREDIT_TERMS>{ct}</CREDIT_TERMS>"
        if pay:
            patch += f"<PAYMENTS_NUMBER>{pay:02d}</PAYMENTS_NUMBER>"
        if first:
            patch += f"<FIRST_PAYMENT_AMOUNT>{to_minor(first)}</FIRST_PAYMENT_AMOUNT>"
        if nxt:
            patch += f"<NEXT_PAYMENT_AMOUNT>{to_minor(nxt)}</NEXT_PAYMENT_AMOUNT>"
        return body.replace("</TRANSACTION_DETAILS>",
                            patch + "</TRANSACTION_DETAILS>")

    # ------------------------------------------------------------------
    # DISCOVERY → AUTHORIZE helper
    # ------------------------------------------------------------------
    def _handle_discovery_ok(self, recv: str):
        self._trans_id = re.search(r"<TRANS_ID>([^<]+)</TRANS_ID>", recv).group(1)

        flags = {k: bool(re.search(fr"<TERMS_{k.upper()}>1</TERMS_{k.upper()}>", recv))
                 for k in ("regular", "special", "immediate", "credit", "installments")}

        def _int(tag, d):
            m = re.search(fr"<{tag}>(\d+)", recv)
            return int(m.group(1)) if m else d

        mn = max(2, _int("CREDIT_MIN_PAYMENTS", 2))
        mx = max(2, _int("CREDIT_MAX_PAYMENTS", 36))

        dlg = CreditTermDlg(flags, mn, mx, self._current_amount, None)
        if dlg.exec_() != QDialog.Accepted:
            show_prompt("העסקה לא אושרה", 4000)
            self._send_cancel()
            return
        ct, pay, first, nxt = dlg.data()

        body = self._auth_body(self.qs_defaults, ct, pay, first, nxt)
        xml  = env_xml("PAYMENT", "AUTHORIZE", self.session,
                       bool(int(self.train_combo.currentText())), self.mac_key, body)
        self._qs_phase = "AUTHORIZE"
        show_prompt("נא המתן לאישור עסקה")         # stays until replaced
        self._send(xml)

    # ------------------------------------------------------------------
    # Auto-cancel & recovery helpers
    # ------------------------------------------------------------------
    def _schedule_cancel(self):
        if self._awaiting_cancel:
            return
        self._awaiting_cancel = True
        self._cancel_timer = QTimer(self)
        self._cancel_timer.setSingleShot(True)
        self._cancel_timer.timeout.connect(self._send_cancel)
        self._cancel_timer.start(2000)

    def _send_cancel(self):
        self._awaiting_cancel = False
        xml = env_xml("GENERAL", "CANCEL", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key)
        self._send(xml)

    def _send_get_details(self):
        if not self._trans_id:
            self._warn("Recovery", "TRANS_ID missing – cannot query details")
            show_prompt("העסקה לא אושרה", 4000)
            self._qs_done()
            return
        body = f"<TRANS_ID>{self._trans_id}</TRANS_ID>"
        xml = env_xml("REPORT", "GET_TRAN_DETAILS", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key, body)
        self._qs_phase = "GET_DETAILS"
        self._send(xml)

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
        xml = env_xml("PAYMENT", "AUTHORIZE", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key, body)
        self._qs_phase = "VOID"
        self._send(xml)

    # ═══════════════════════════════════════
    # CENTRAL RESPONSE HANDLER
    # ═══════════════════════════════════════
    def _handle_response(self, sent: str, recv: str):
        self.sent_log.append(sent)
        self.recv_log.append(recv)

        # RESULT_CODE 2 → auto-cancel
        if "<RESULT_CODE>2<" in recv and "<COMMAND>CANCEL" not in sent:
            show_prompt("העסקה לא אושרה", 4000)
            self._schedule_cancel()
            return

        # ───────────────────────────────────
        # Quick-Sale FSM
        # ───────────────────────────────────
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
                    # SHVA terminal-ID check
                    expected = self.termid_field.text().strip()
                    m_tid = re.search(r"<SHVA_TERM_ID>(\d+)</SHVA_TERM_ID>", recv)
                    if expected and m_tid and m_tid.group(1) != expected:
                        self._crit("SHVA ID", "מזהה SHVA אינו תקין")
                        show_prompt("העסקה לא אושרה", 4000)
                        self._send_cancel()
                        self._qs_done()
                        return
                    # Auto-EOD if SHVA_STATUS ≠ 1
                    if re.search(r"<SHVA_STATUS>(?!1)", recv):
                        self.cmd_eod()
                    self._send_start_tran()
                else:
                    show_prompt("העסקה לא אושרה", 4000)
                    self._qs_done()
                return

            # ---------- START_TRAN ----------
            if "<COMMAND>START_TRAN" in sent:
                if "<RESULT_CODE>0<" in recv:
                    body = self._disc_body(self.qs_defaults)
                    xml  = env_xml("PAYMENT", "DISCOVERY", self.session,
                                   bool(int(self.train_combo.currentText())), self.mac_key, body)
                    self._qs_phase = "DISCOVERY"
                    show_prompt("זיהוי כרטיס", 3000)
                    self._send(xml)
                else:
                    show_prompt("העסקה לא אושרה", 4000)
                    self._qs_done()
                return

            # ---------- DISCOVERY ----------
            if "<COMMAND>DISCOVERY" in sent:
                if "<RESULT_CODE>0<" in recv:
                    self._handle_discovery_ok(recv)
                else:
                    show_prompt("העסקה לא אושרה", 4000)
                    self._qs_done()
                return

            # ---------- AUTHORIZE ----------
            if "<COMMAND>AUTHORIZE" in sent:
                if "<EVENT>COMPLETED" in recv:
                    ok_code = bool(re.search(r"<RESULT_CODE>0<", recv))
                    m_auth  = re.search(r"<ISSUER_AUTH_NUM>([^<]*)</ISSUER_AUTH_NUM>", recv)
                    auth_no = m_auth.group(1).strip() if m_auth else ""
                    success = ok_code and auth_no and auth_no != "0"
                    issuer_decline = bool(re.search(r"נדחה\s*ע.?י\s*חברה", recv))

                    if success:
                        msg = f"סיום עסקה\nהעסקה אושרה בהצלחה\nמספר אישור {auth_no}"
                    else:
                        msg = "סיום עסקה\nהעסקה נכשלה"
                        if issuer_decline:
                            msg += "\nנדחה ע`י חברה"
                    show_prompt(msg, 4000)

                    save_receipt(recv)
                    try:
                        with open("receipt.json", "rb") as fp:
                            self.webhook.publish(fp.read())
                    except Exception:
                        pass
                    self._send_finish_tran()
                    return

                # Not completed → recovery
                self._info("Recovery", "AUTHORIZE incomplete – querying details")
                self._send_get_details()
                return

            # ---------- GET_TRAN_DETAILS ----------
            if "<COMMAND>GET_TRAN_DETAILS" in sent:
                approved = ("<RESULT_CODE>0<" in recv and
                            "<TRANSACTIONS>" in recv and
                            not "<TRANSACTIONS></TRANSACTIONS>" in recv)
                if approved:
                    self._info("Recovery", "עסקה קיימת ואושרה – שולח VOID")
                    self._send_void()
                else:
                    show_prompt("העסקה לא אושרה", 4000)
                    self._crit("Recovery", "No matching transaction – aborting queue")
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

        # ───────────────────────────────────
        # Cancel outside Quick-Sale
        # ───────────────────────────────────
        if "<COMMAND>CANCEL" in sent:
            show_prompt("העסקה לא אושרה", 4000)
            self._send_finish_tran()
            return

        # ───────────────────────────────────
        # Register / key-exchange housekeeping
        # ───────────────────────────────────
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
