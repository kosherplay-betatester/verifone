#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# gui/main_window.py  –  FULL FILE  (v1.63 • SHVA_TERM_ID validation + Save-Settings)

"""
Main admin window for Verifone-P400 desktop app
==============================================

Key features
------------
* Webhook + full Quick-Sale queue:
      PING → STATUS → START_TRAN → DISCOVERY → AUTHORIZE → FINISH_TRAN
* Manual REGISTER / EXCHANGE_KEYS / STATUS / EOD / generic ADMIN commands
* Auto-EOD once every 24 h at a user-selected HH:mm
* Free-Call XML sandbox with auto-signing
* Auto-cancel on RESULT_CODE 2 **and** on user “ביטול” in Credit dialog
* Receipt for any AUTHORIZE reply is always written to *receipt.json*
* Logs of every request/response pair with 14-day auto-rotation
* v1.63 additions:
    • “SHVA ID” field, persisted in *settings.txt* (key: **shva_term_id**)
    • “Save Settings” button
    • During Quick-Sale STATUS the device’s <SHVA_TERM_ID> must match
      the saved ID – otherwise the sale is cancelled and an error shown:
        מזהה SHVA אינו תקין, אנא פנה לטכנאי
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta

from PyQt5.QtCore import Qt, QTimer, pyqtSlot, QTime, QDateTime
from PyQt5.QtGui  import QTextCursor, QTextCharFormat, QColor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QVBoxLayout, QHBoxLayout, QSplitter,
    QDoubleSpinBox, QDialog, QTimeEdit
)

from config   import load_settings, load_mac, save_mac, save_settings
from dialogs  import CreditTermDlg, DiscoverDlg   # DiscoverDlg still available for manual ops
from logger   import log_traffic
from network  import SockThread, WebhookServer
from receipt  import save_receipt
from utils    import rand_session, to_minor, des3_decrypt
from xml_sign import env_xml, sign_xml, template_xml


# ══════════════════════════════════════════════════════════
#                     MainWindow
# ══════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    """Admin GUI + business logic for the desktop application."""

    # ───────────────────────────────────────────────────────
    # Construction
    # ───────────────────────────────────────────────────────
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 – Quick Sale / Webhook / Admin")
        self.resize(1120, 860)

        # ----- runtime state -----
        self.session                 = ""
        self.mac_key                 = load_mac()
        self.threads: list[SockThread] = []
        self.qs_active               = False
        self.qs_queue: list[tuple[float, str]] = []
        self._current_amount         = 0.0
        self.qs_defaults: dict       = {}
        self.qs_start_body           = ""
        self._awaiting_cancel        = False
        self._cancel_timer           = None
        self._manual_key_exchange    = False
        self._qs_phase               = ""

        cfg = load_settings()

        # ═════════════════════════ UI BUILD ═════════════════════════
        central = QWidget(); root = QVBoxLayout(central)

        # ─ Connection row ─
        row = QHBoxLayout()
        self.ip_field    = QLineEdit(cfg["ip"])
        self.port_field  = QLineEdit(cfg["port"])
        self.train_combo = QComboBox(); self.train_combo.addItems(["0", "1"])
        self.train_combo.setCurrentIndex(1 if cfg.get("training") else 0)
        for lbl, w in [("IP", self.ip_field), ("Port", self.port_field),
                       ("Training", self.train_combo)]:
            row.addWidget(QLabel(lbl)); row.addWidget(w)
        root.addLayout(row)

        # ─ Register row ─
        row = QHBoxLayout()
        self.chain_field = QLineEdit(cfg["chain"])
        self.store_field = QLineEdit(cfg["store"])
        self.lane_field  = QLineEdit(cfg["lane"])
        self.alt_field   = QLineEdit(cfg["alt"])
        self.termid_field = QLineEdit(cfg.get("shva_term_id", ""))    # NEW
        self.pos_combo = QComboBox(); self.pos_combo.addItems(["ATTENDED", "UNATTENDED"])
        self.pos_combo.setCurrentText(cfg.get("pos_type", "ATTENDED"))

        for lbl, w in [
            ("Chain", self.chain_field), ("Store", self.store_field),
            ("Lane",  self.lane_field),  ("Alt-ID", self.alt_field),
            ("SHVA ID", self.termid_field)
        ]:
            row.addWidget(QLabel(lbl)); row.addWidget(w)
        row.addWidget(QLabel("POS Type")); row.addWidget(self.pos_combo)
        self.reg_btn = QPushButton("Register"); self.reg_btn.clicked.connect(self.cmd_register)
        row.addWidget(self.reg_btn)
        root.addLayout(row)

        # ─ Save-Settings row ─
        row = QHBoxLayout(); row.addStretch(1)
        self.save_btn = QPushButton("Save Settings"); self.save_btn.clicked.connect(self._save_settings)
        row.addWidget(self.save_btn); root.addLayout(row)

        # ─ Key-exchange row ─
        row = QHBoxLayout()
        self.ktk_field = QLineEdit(cfg.get("ktk", "")); self.ktk_field.setEnabled(False)
        self.key_btn = QPushButton("Exchange Keys"); self.key_btn.setEnabled(False)
        self.key_btn.clicked.connect(self.cmd_keys)
        row.addWidget(QLabel("KTK (16)")); row.addWidget(self.ktk_field); row.addWidget(self.key_btn)
        root.addLayout(row)

        # ─ MAC view ─
        row = QHBoxLayout()
        self.mac_view = QLineEdit(self.mac_key); self.mac_view.setReadOnly(True)
        row.addWidget(QLabel("MAC Key")); row.addWidget(self.mac_view)
        root.addLayout(row)

        # ─ Quick-Sale row ─
        row = QHBoxLayout()
        row.addWidget(QLabel("Amount ₪"))
        self.qs_amount = QDoubleSpinBox(decimals=2, maximum=999999, value=cfg.get("quick_amount", 0.0))
        row.addWidget(self.qs_amount)
        self.qs_btn = QPushButton("Quick Sale")
        self.qs_btn.setEnabled(bool(self.mac_key))
        self.qs_btn.clicked.connect(lambda: self.quick_sale(self.qs_amount.value(), "01"))
        row.addWidget(self.qs_btn); row.addStretch(1)
        row.addWidget(QLabel("Admin CMD"))
        self.admin_edit = QLineEdit()
        self.admin_send = QPushButton("Send"); self.admin_send.clicked.connect(self.admin_cmd)
        row.addWidget(self.admin_edit); row.addWidget(self.admin_send)
        root.addLayout(row)

        # ─ Status / EOD buttons ─
        row = QHBoxLayout()
        self.status_btn = QPushButton("Status"); self.status_btn.setEnabled(bool(self.mac_key))
        self.status_btn.clicked.connect(self.cmd_status); row.addWidget(self.status_btn)
        self.eod_btn = QPushButton("EOD"); self.eod_btn.setEnabled(bool(self.mac_key))
        self.eod_btn.clicked.connect(self.cmd_eod); row.addWidget(self.eod_btn)
        root.addLayout(row)

        # ─ Auto-EOD scheduler ─
        row = QHBoxLayout()
        row.addWidget(QLabel("Auto EOD at"))
        self.eod_time_edit = QTimeEdit(); self.eod_time_edit.setDisplayFormat("HH:mm")
        self.eod_time_edit.setTime(QTime(2, 0))
        row.addWidget(self.eod_time_edit)
        root.addLayout(row)
        self._eod_timer = QTimer(self); self._eod_timer.setSingleShot(True)
        self._eod_timer.timeout.connect(self._handle_auto_eod)
        self.eod_time_edit.timeChanged.connect(self._schedule_auto_eod)
        self._schedule_auto_eod(self.eod_time_edit.time())

        # ─ Logs splitter ─
        self.sent_log = QTextEdit(readOnly=True); self.recv_log = QTextEdit(readOnly=True)
        spl = QSplitter(Qt.Horizontal); spl.addWidget(self.sent_log); spl.addWidget(self.recv_log)
        root.addWidget(spl, stretch=1)

        # ─ Search row ─
        row = QHBoxLayout(); row.addWidget(QLabel("Search"))
        self.search_edit = QLineEdit()
        self.search_btn  = QPushButton("Find"); self.search_btn.clicked.connect(self.search_logs)
        self.search_edit.returnPressed.connect(self.search_logs)
        row.addWidget(self.search_edit); row.addWidget(self.search_btn)
        root.addLayout(row)
        clr = QPushButton("Clear logs"); clr.clicked.connect(lambda: (self.sent_log.clear(), self.recv_log.clear()))
        root.addWidget(clr)

        # ─ Free-Call sandbox ─
        fc = QVBoxLayout(); fc.addWidget(QLabel("<b>Free-Call XML Sandbox</b>"))
        self.free_xml_edit = QTextEdit(); fc.addWidget(self.free_xml_edit, stretch=1)
        bar = QHBoxLayout()
        self.free_send_btn = QPushButton("Send Free Call")
        self.free_send_btn.setEnabled(bool(self.mac_key))
        self.free_send_btn.clicked.connect(self.free_send)
        bar.addWidget(self.free_send_btn)
        self.free_cmds: list[QPushButton] = []
        for fg, cmd, lbl in [
            ("SESSION", "START_TRAN",  "Start Tran"),
            ("PAYMENT", "DISCOVERY",   "Discover"),
            ("PAYMENT", "AUTHORIZE",   "Authorize"),
            ("SESSION", "FINISH_TRAN", "Finish Tran"),
        ]:
            b = QPushButton(lbl); b.setEnabled(bool(self.mac_key))
            b.clicked.connect(lambda _, g=fg, c=cmd: self.fill_template(g, c))
            bar.addWidget(b); self.free_cmds.append(b)
        fc.addLayout(bar); root.addLayout(fc, stretch=2)

        self.setCentralWidget(central)

        # ─ Webhook server ─
        host = cfg.get("webhook_host", "localhost") or "localhost"
        try: port = int(cfg.get("webhook_port", 8080))
        except (ValueError, TypeError): port = 8080
        self.webhook = WebhookServer(host, port, self._from_webhook); self.webhook.start()

    # ══════════════════════════════════════════════════════
    # Save-Settings helper
    # ══════════════════════════════════════════════════════
    def _save_settings(self):
        try:
            data = {
                "ip":       self.ip_field.text().strip(),
                "port":     self.port_field.text().strip(),
                "training": self.train_combo.currentText() == "1",
                "chain":    self.chain_field.text().strip(),
                "store":    self.store_field.text().strip(),
                "lane":     self.lane_field.text().strip(),
                "alt":      self.alt_field.text().strip(),
                "pos_type": self.pos_combo.currentText(),
                "ktk":      self.ktk_field.text().strip(),
                "quick_amount": self.qs_amount.value(),
                "webhook_host": "localhost",
                "webhook_port": 8080,
                "shva_term_id": self.termid_field.text().strip()
            }
            save_settings(data)
            self._info("Settings", "Saved successfully.")
        except Exception as exc:                                         # pragma: no cover
            self._crit("Settings", f"Save failed: {exc}")

    # ══════════════════════════════════════════════════════
    # Convenience log helpers
    # ══════════════════════════════════════════════════════
    def _info(self, title: str, text: str):
        self.sent_log.append(f'<span style="color:#27ae60;">[INFO] {title}: {text}</span>')
        self.sent_log.moveCursor(QTextCursor.End)

    def _warn(self, title: str, text: str):
        self.recv_log.append(f'<span style="color:#e67e22;">[WARN] {title}: {text}</span>')
        self.recv_log.moveCursor(QTextCursor.End)

    def _crit(self, title: str, text: str):
        self.recv_log.append(f'<span style="color:#c0392b;">[ERROR] {title}: {text}</span>')
        self.recv_log.moveCursor(QTextCursor.End)

    # ══════════════════════════════════════════════════════
    #  Register / Keys / Status / EOD / Admin
    # ══════════════════════════════════════════════════════
    def cmd_register(self):
        self.session = rand_session()
        body = (
            f"<CHAIN>{self.chain_field.text()}</CHAIN>"
            f"<STORE>{self.store_field.text()}</STORE>"
            f"<LANE>{self.lane_field.text()}</LANE>"
            f"<TERMINAL_ID>{self.alt_field.text()}</TERMINAL_ID>"
            f"<POS_TYPE>{self.pos_combo.currentText()}</POS_TYPE>"
        )
        xml = env_xml("ADMIN", "REGISTER", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key, body)
        self._send(xml)

    def cmd_keys(self):
        self._manual_key_exchange = True
        ktk = self.ktk_field.text().strip()
        xml = env_xml("ADMIN", "EXCHANGE_KEYS", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key,
                      f"<KTK>{ktk}</KTK>")
        self._send(xml)

    def cmd_status(self):
        xml = env_xml("ADMIN", "STATUS", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key)
        self._send(xml)

    def cmd_eod(self):
        xml = env_xml("ADMIN", "EOD", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key)
        self._send(xml)

    def admin_cmd(self):
        cmd = self.admin_edit.text().strip().upper()
        if not cmd:
            return
        xml = env_xml("ADMIN", cmd, self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key)
        self._send(xml)

    # ══════════════════════════════════════════════════════
    # Auto-EOD scheduling
    # ══════════════════════════════════════════════════════
    def _schedule_auto_eod(self, tm: QTime):
        now = QDateTime.currentDateTime()
        target = QDateTime(now.date(), tm)
        if target <= now: target = target.addDays(1)
        self._eod_timer.start(now.msecsTo(target))

    def _handle_auto_eod(self):
        self.cmd_eod()
        self._eod_timer.start(24 * 60 * 60 * 1000)   # same hour next day

    # ══════════════════════════════════════════════════════
    # Quick-Sale helpers (queue)
    # ══════════════════════════════════════════════════════
    def quick_sale(self, amount: float, tran_type: str = "01"):
        self.qs_queue.append((amount, tran_type))
        self._start_next_qs()

    def _start_next_qs(self):
        if self.qs_active or not self.qs_queue:
            return
        amt, tcode = self.qs_queue.pop(0)
        self._launch_qs(amt, tcode)

    def _launch_qs(self, amount: float, tcode: str):
        self.session = rand_session()
        self._current_amount = amount
        self.qs_amount.setValue(amount)
        self.qs_defaults = {
            "timeout": "60", "restrict_token": "0", "manual": False, "manual_reason": "",
            "tran_type": tcode, "amount": to_minor(amount), "currency": "376",
            "cash": None, "fx": False, "fx_to": "", "fx_amt": None,
            "use_token": False, "token_val": "", "service_type": "",
            "ctls": True, "allow_cancel": True, "unattended": False, "operation": "04",
        }
        self.qs_start_body = (
            "<INVOICE>100000</INVOICE>"
            f"<POS_TYPE>{self.pos_combo.currentText()}</POS_TYPE>"
        )
        self.qs_active = True
        self._qs_phase = "PING"
        ping = env_xml("ADMIN", "PING", self.session,
                       bool(int(self.train_combo.currentText())), self.mac_key)
        self._send(ping)

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

    def _qs_done(self):
        self.qs_active = False
        self._qs_phase = ""
        self._start_next_qs()

    # called by WebhookServer
    @pyqtSlot(float, str)
    def _from_webhook(self, amount: float, tcode: str):
        self.quick_sale(amount, tcode)

    # ══════════════════════════════════════════════════════
    # DISCOVERY / AUTHORIZE body builders
    # ══════════════════════════════════════════════════════
    def _disc_body(self, o: dict) -> str:
        parts = [
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

    def _auth_body(self, base: dict, ct: str, pay: int, first: float, nxt: float) -> str:
        body = self._disc_body(base)
        inj = f"<CREDIT_TERMS>{ct}</CREDIT_TERMS>"
        if pay:   inj += f"<PAYMENTS_NUMBER>{pay:02d}</PAYMENTS_NUMBER>"
        if first: inj += f"<FIRST_PAYMENT_AMOUNT>{to_minor(first)}</FIRST_PAYMENT_AMOUNT>"
        if nxt:   inj += f"<NEXT_PAYMENT_AMOUNT>{to_minor(nxt)}</NEXT_PAYMENT_AMOUNT>"
        return body.replace("</TRANSACTION_DETAILS>", inj + "</TRANSACTION_DETAILS>")

    # ══════════════════════════════════════════════════════
    # TCP send helper
    # ══════════════════════════════════════════════════════
    def _send(self, xml: str):
        try: port = int(self.port_field.text())
        except ValueError:
            return self._crit("Port", "Invalid port number")
        thr = SockThread(self.ip_field.text().strip(), port, xml)
        thr.result.connect(self._handle_response)
        thr.finished.connect(lambda: self.threads.remove(thr))
        self.threads.append(thr); thr.start()

    # ══════════════════════════════════════════════════════
    # Free-Call helpers
    # ══════════════════════════════════════════════════════
    def fill_template(self, fg: str, cmd: str):
        xml = template_xml(fg, cmd, self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key)
        self.free_xml_edit.setPlainText(xml)

    def free_send(self):
        raw = self.free_xml_edit.toPlainText().strip()
        if not raw: return self._info("Free-Call", "XML area is empty.")
        try:
            signed = raw if re.search(r"<MAC>.*?</MAC>", raw, re.S) else sign_xml(raw, self.mac_key)
        except Exception as exc:
            return self._warn("Free-Call", str(exc))
        self.free_xml_edit.setPlainText(signed)
        self._send(signed)

    # ══════════════════════════════════════════════════════
    # Search-in-logs
    # ══════════════════════════════════════════════════════
    def search_logs(self):
        term = self.search_edit.text()
        for pane in (self.sent_log, self.recv_log):
            pane.setExtraSelections([])
            if not term: continue
            doc = pane.document(); cur = QTextCursor(doc); sels = []
            while True:
                cur = doc.find(term, cur)
                if cur.isNull(): break
                sel = QTextEdit.ExtraSelection(); sel.cursor = cur
                fmt = QTextCharFormat(); fmt.setBackground(QColor("#ffff66"))
                sel.format = fmt; sels.append(sel)
            pane.setExtraSelections(sels)

    # ══════════════════════════════════════════════════════
    # Auto-cancel helpers
    # ══════════════════════════════════════════════════════
    def _schedule_cancel(self):
        if self._awaiting_cancel: return
        self._awaiting_cancel = True
        self._cancel_timer = QTimer(self); self._cancel_timer.setSingleShot(True)
        self._cancel_timer.timeout.connect(self._send_cancel)
        self._cancel_timer.start(2000)

    def _send_cancel(self):
        self._awaiting_cancel = False
        xml = env_xml("GENERAL", "CANCEL", self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key)
        self._send(xml)

    # ══════════════════════════════════════════════════════
    # Central response handler
    # ══════════════════════════════════════════════════════
    def _handle_response(self, sent: str, recv: str):
        self.sent_log.append(sent); self.recv_log.append(recv)

        # auto-cancel on RESULT_CODE 2
        if "<RESULT_CODE>2<" in recv and "<COMMAND>CANCEL" not in sent:
            self._schedule_cancel(); return

        # ═════════════════════ Quick-Sale FSM ════════════════
        if self.qs_active:

            # PING → STATUS
            if "<COMMAND>PING" in sent:
                if "<RESULT_CODE>0<" in recv: self._send_status()
                else: self._qs_done()
                return

            # STATUS validation
            if "<COMMAND>STATUS" in sent:
                if "<RESULT_CODE>0<" in recv:
                    # --- SHVA_TERM_ID check ---
                    expected = self.termid_field.text().strip()
                    m_tid = re.search(r"<SHVA_TERM_ID>(\d+)</SHVA_TERM_ID>", recv)
                    if expected and m_tid and m_tid.group(1) != expected:
                        self._crit("SHVA ID", "מזהה SHVA אינו תקין, אנא פנה לטכנאי")
                        self._send_cancel(); self._qs_done(); return
                    # SHVA_STATUS auto-EOD
                    m_stat = re.search(r"<SHVA_STATUS>(\d+)", recv)
                    if m_stat and m_stat.group(1) != "1": self.cmd_eod()
                    self._send_start_tran()
                else:
                    self._qs_done()
                return

            # START_TRAN → DISCOVERY
            if "<COMMAND>START_TRAN" in sent:
                if "<RESULT_CODE>0<" in recv:
                    body = self._disc_body(self.qs_defaults)
                    xml  = env_xml("PAYMENT", "DISCOVERY", self.session,
                                   bool(int(self.train_combo.currentText())), self.mac_key, body)
                    self._send(xml)
                else:
                    self._qs_done()
                return

            # DISCOVERY → CreditTermDlg → AUTHORIZE
            if "<COMMAND>DISCOVERY" in sent:
                if "<RESULT_CODE>0<" not in recv:
                    self._qs_done(); return
                flags = {k: bool(re.search(fr"<TERMS_{k.upper()}>1</TERMS_{k.upper()}>", recv))
                         for k in ("regular", "special", "immediate", "credit", "installments")}
                def _i(tag, d): m = re.search(fr"<{tag}>(\d+)", recv); return int(m.group(1)) if m else d
                mn = max(2, _i("CREDIT_MIN_PAYMENTS", 2)); mx = max(2, _i("CREDIT_MAX_PAYMENTS", 36))
                dlg = CreditTermDlg(flags, mn, mx, self._current_amount, None)
                if dlg.exec_() != QDialog.Accepted:
                    self._send_cancel(); return
                ct, pay, first, nxt = dlg.data()
                body = self._auth_body(self.qs_defaults, ct, pay, first, nxt)
                xml  = env_xml("PAYMENT", "AUTHORIZE", self.session,
                               bool(int(self.train_combo.currentText())), self.mac_key, body)
                self._send(xml); return

            # AUTHORIZE → save_receipt → FINISH_TRAN
            if "<COMMAND>AUTHORIZE" in sent:
                save_receipt(recv)
                try:
                    with open("receipt.json", "rb") as fp:
                        self.webhook.publish(fp.read())
                except Exception:
                    pass
                xml = env_xml("SESSION", "FINISH_TRAN", self.session,
                              bool(int(self.train_combo.currentText())), self.mac_key)
                self._send(xml); return

            # FINISH_TRAN → queue done
            if "<COMMAND>FINISH_TRAN" in sent:
                self._qs_done(); return

        # Cancel reply outside QS
        if "<COMMAND>CANCEL" in sent:
            m = re.search(r"<RESULT_CODE>(\d+)<", recv); code = m.group(1) if m else None
            if code not in ("0", "50", "51"):
                self._warn("Cancel failed", f"code {code or '?'}")
            xml = env_xml("SESSION", "FINISH_TRAN", self.session,
                          bool(int(self.train_combo.currentText())), self.mac_key)
            self._send(xml); return

        # REGISTER completed → enable Exchange-Keys
        if "<COMMAND>REGISTER" in sent and "<EVENT>COMPLETED" in recv:
            self.ktk_field.setEnabled(True); self.key_btn.setEnabled(True)

        # EXCHANGE_KEYS (manual) → decrypt MAC
        if "<COMMAND>EXCHANGE_KEYS" in sent and "<MAC_KEY>" in recv and self._manual_key_exchange:
            m = re.search(r"<MAC_KEY>([^<]+)</MAC_KEY>", recv)
            if m:
                enc = m.group(1)
                try:
                    self.mac_key = des3_decrypt(self.ktk_field.text().strip(), enc)
                    save_mac(self.mac_key)
                    self.mac_view.setText(self.mac_key)
                    # enable UI
                    for b in (self.status_btn, self.eod_btn, self.qs_btn,
                              self.free_send_btn, *self.free_cmds):
                        b.setEnabled(True)
                except Exception as exc:
                    self._crit("Decrypt", str(exc))
                finally:
                    self._manual_key_exchange = False

    # =================================================================
    # End class
    # =================================================================


# ══════════════════════════════════════════════════════════
# Stand-alone bootstrap
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv); win = MainWindow(); win.show()
    sys.exit(app.exec_())
