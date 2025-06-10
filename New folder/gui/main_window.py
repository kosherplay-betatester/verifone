#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# gui/main_window.py  –  FULL FILE  (v1.60 • Auto-EOD on SHVA_STATUS≠1 during Quick-Sale)

"""
Main admin window for Verifone-P400 desktop app
==============================================

• Quick-Sale queue (Webhook / UI):
      PING → STATUS → START_TRAN → DISCOVERY → AUTHORIZE → FINISH_TRAN
• Manual Register / Exchange-Keys / Status / EOD / generic ADMIN commands
• Auto-click EOD at a user-selected time each day
• During Quick-Sale, if <SHVA_STATUS>≠1 in STATUS reply, also send EOD
• Free-Call XML sandbox with auto-signing
• Auto-cancel on RESULT_CODE 2 **and** על “ביטול” בחלון האשראי
• v1.54: AUTHORIZE reply (success *or* error) נכתב תמיד ל-receipt.json
• v1.55: כל QMessageBox הוחלפו בלוג-פאנלים
• v1.56: QDialog imported (NameError fix)
• v1.57: הוספת Ping+Status אוטומטיים לפני כל Quick-Sale
• v1.58: הוספת כפתור EOD להרצת פקודת EOD
• v1.59: הוספת Auto-EOD – תזמון יומי להפעלת EOD
• v1.60: במהלך Quick-Sale, אם SHVA_STATUS≠1 ב-STATUS, שולח גם EOD
"""

from __future__ import annotations
import re, sys
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, pyqtSlot, QTime, QDateTime
from PyQt5.QtGui import QTextCursor, QTextCharFormat, QColor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QVBoxLayout, QHBoxLayout, QSplitter,
    QDoubleSpinBox, QDialog, QTimeEdit
)

from config   import load_settings, load_mac, save_mac
from dialogs  import CreditTermDlg, DiscoverDlg
from logger   import log_traffic
from network  import SockThread, WebhookServer
from receipt  import save_receipt
from utils    import rand_session, to_minor, des3_decrypt
from xml_sign import env_xml, sign_xml, template_xml


class MainWindow(QMainWindow):
    """Admin UI + business logic (silent error handling)."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 – Quick Sale + Webhook + Free-Call")
        self.resize(1120, 850)

        # ─ runtime state ───────────────────────────────────────────
        self.session               = ""
        self.mac_key               = load_mac()
        self.threads: list[SockThread] = []
        self.qs_active             = False
        self.qs_queue: list[tuple[float, str]] = []
        self._current_amount       = 0.0
        self.qs_defaults: dict     = {}
        self.qs_start_body         = ""
        self._cancel_timer         = None
        self._awaiting_cancel      = False
        self._manual_key_exchange  = False
        self._qs_phase             = ""

        settings = load_settings()

        central = QWidget()
        layout = QVBoxLayout(central)

        # ─ Connection row ────────────────────────────────────────
        conn = QHBoxLayout()
        self.ip_field    = QLineEdit(settings["ip"])
        self.port_field  = QLineEdit(settings["port"])
        self.train_combo = QComboBox()
        self.train_combo.addItems(["0", "1"])
        self.train_combo.setCurrentIndex(1 if settings.get("training") else 0)
        for lbl, w in [("IP", self.ip_field), ("Port", self.port_field), ("Training", self.train_combo)]:
            conn.addWidget(QLabel(lbl)); conn.addWidget(w)
        layout.addLayout(conn)

        # ─ Register row ─────────────────────────────────────────
        reg = QHBoxLayout()
        self.chain_field = QLineEdit(settings["chain"])
        self.store_field = QLineEdit(settings["store"])
        self.lane_field  = QLineEdit(settings["lane"])
        self.alt_field   = QLineEdit(settings["alt"])
        self.pos_combo   = QComboBox()
        self.pos_combo.addItems(["ATTENDED", "UNATTENDED"])
        self.pos_combo.setCurrentText(settings.get("pos_type", "ATTENDED"))
        for lbl, w in [("Chain", self.chain_field), ("Store", self.store_field),
                       ("Lane", self.lane_field), ("Alt-ID", self.alt_field)]:
            reg.addWidget(QLabel(lbl)); reg.addWidget(w)
        reg.addWidget(QLabel("POS Type")); reg.addWidget(self.pos_combo)
        self.reg_btn = QPushButton("Register")
        self.reg_btn.clicked.connect(self.cmd_register)
        reg.addWidget(self.reg_btn)
        layout.addLayout(reg)

        # ─ Key-exchange row ──────────────────────────────────────
        keys = QHBoxLayout()
        self.ktk_field = QLineEdit(settings.get("ktk", ""))
        self.ktk_field.setEnabled(False)
        self.key_btn = QPushButton("Exchange Keys")
        self.key_btn.setEnabled(False)
        self.key_btn.clicked.connect(self.cmd_keys)
        keys.addWidget(QLabel("KTK (16)")); keys.addWidget(self.ktk_field); keys.addWidget(self.key_btn)
        layout.addLayout(keys)

        # ─ MAC viewer ────────────────────────────────────────────
        macrow = QHBoxLayout()
        self.mac_view = QLineEdit(self.mac_key)
        self.mac_view.setReadOnly(True)
        macrow.addWidget(QLabel("MAC Key")); macrow.addWidget(self.mac_view)
        layout.addLayout(macrow)

        # ─ Quick-Sale & admin-cmd row ────────────────────────────
        act = QHBoxLayout()
        act.addWidget(QLabel("Amount ₪"))
        self.qs_amount = QDoubleSpinBox(decimals=2, maximum=999999, value=settings.get("quick_amount", 0.0))
        act.addWidget(self.qs_amount)
        self.qs_btn = QPushButton("Quick Sale")
        self.qs_btn.setEnabled(bool(self.mac_key))
        self.qs_btn.clicked.connect(lambda: self.quick_sale(self.qs_amount.value(), "01"))
        act.addWidget(self.qs_btn); act.addStretch()
        act.addWidget(QLabel("Admin CMD"))
        self.admin_edit = QLineEdit()
        self.admin_send = QPushButton("Send")
        self.admin_send.clicked.connect(self.admin_cmd)
        act.addWidget(self.admin_edit); act.addWidget(self.admin_send)
        layout.addLayout(act)

        # ─ Status & EOD buttons ──────────────────────────────────
        shortcuts = QHBoxLayout()
        self.status_btn = QPushButton("Status")
        self.status_btn.setEnabled(bool(self.mac_key))
        self.status_btn.clicked.connect(self.cmd_status)
        shortcuts.addWidget(self.status_btn)

        self.eod_btn = QPushButton("EOD")
        self.eod_btn.setEnabled(bool(self.mac_key))
        self.eod_btn.clicked.connect(self.cmd_eod)
        shortcuts.addWidget(self.eod_btn)
        layout.addLayout(shortcuts)

        # ─ Auto-EOD scheduling ───────────────────────────────────
        sched = QHBoxLayout()
        sched.addWidget(QLabel("Auto EOD at"))
        self.eod_time_edit = QTimeEdit()
        self.eod_time_edit.setDisplayFormat("HH:mm")
        default_time = QTime(2, 0)  # 02:00 AM default
        self.eod_time_edit.setTime(default_time)
        sched.addWidget(self.eod_time_edit)
        layout.addLayout(sched)

        self._eod_timer = QTimer(self)
        self._eod_timer.setSingleShot(True)
        self._eod_timer.timeout.connect(self._handle_auto_eod)
        self.eod_time_edit.timeChanged.connect(self._schedule_auto_eod)
        self._schedule_auto_eod(self.eod_time_edit.time())

        # ─ Logs splitter ─────────────────────────────────────────
        self.sent_log = QTextEdit(readOnly=True)
        self.recv_log = QTextEdit(readOnly=True)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.sent_log); splitter.addWidget(self.recv_log)
        layout.addWidget(splitter, stretch=1)

        # ─ Search ────────────────────────────────────────────────
        srch = QHBoxLayout()
        srch.addWidget(QLabel("Search"))
        self.search_edit = QLineEdit()
        self.search_btn  = QPushButton("Find")
        self.search_btn.clicked.connect(self.search_logs)
        self.search_edit.returnPressed.connect(self.search_logs)
        srch.addWidget(self.search_edit); srch.addWidget(self.search_btn)
        layout.addLayout(srch)
        clr_btn = QPushButton("Clear logs")
        clr_btn.clicked.connect(lambda: (self.sent_log.clear(), self.recv_log.clear()))
        layout.addWidget(clr_btn)

        # ─ Free-Call sandbox ────────────────────────────────────
        sb = QVBoxLayout()
        sb.addWidget(QLabel("<b>Free-Call XML Sandbox</b>"))
        self.free_xml_edit = QTextEdit()
        sb.addWidget(self.free_xml_edit, stretch=1)
        fr = QHBoxLayout()
        self.free_send_btn = QPushButton("Send Free Call")
        self.free_send_btn.setEnabled(bool(self.mac_key))
        self.free_send_btn.clicked.connect(self.free_send)
        fr.addWidget(self.free_send_btn)
        self.free_cmds: list[QPushButton] = []
        for fg, cmd, lbl in [
            ("SESSION", "START_TRAN",  "Start Tran"),
            ("PAYMENT", "DISCOVERY",   "Discover"),
            ("PAYMENT", "AUTHORIZE",   "Authorize"),
            ("SESSION", "FINISH_TRAN", "Finish Tran"),
        ]:
            b = QPushButton(lbl)
            b.setEnabled(bool(self.mac_key))
            b.clicked.connect(lambda _, g=fg, c=cmd: self.fill_template(g, c))
            fr.addWidget(b)
            self.free_cmds.append(b)
        sb.addLayout(fr)
        layout.addLayout(sb, stretch=2)

        self.setCentralWidget(central)

        # ─ Webhook server ────────────────────────────────────────
        host = settings.get("webhook_host", "localhost") or "localhost"
        try:
            port = int(settings.get("webhook_port", 8080))
        except (ValueError, TypeError):
            port = 8080
        self.webhook = WebhookServer(host, port, self._from_webhook)
        self.webhook.start()

    # ─── Message helpers ───────────────────────────────────────
    def _info(self, title: str, text: str):
        self.sent_log.append(f'<span style="color:#27ae60;">[INFO] {title}: {text}</span>')
        self.sent_log.moveCursor(QTextCursor.End)

    def _warn(self, title: str, text: str):
        self.recv_log.append(f'<span style="color:#e67e22;">[WARN] {title}: {text}</span>')
        self.recv_log.moveCursor(QTextCursor.End)

    def _crit(self, title: str, text: str):
        self.recv_log.append(f'<span style="color:#c0392b;">[ERROR] {title}: {text}</span>')
        self.recv_log.moveCursor(QTextCursor.End)

    # ─── Register / Keys / Status / EOD / Admin ────────────────
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
        """Send EOD command."""
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

    # ─── Auto-EOD scheduling helpers ───────────────────────────
    def _schedule_auto_eod(self, time: QTime):
        now = QDateTime.currentDateTime()
        target = QDateTime(now.date(), time)
        if target <= now:
            target = target.addDays(1)
        self._eod_timer.start(now.msecsTo(target))

    def _handle_auto_eod(self):
        self.cmd_eod()
        # reschedule for next day
        self._eod_timer.start(24 * 60 * 60 * 1000)

    # ─── Quick-Sale queue ─────────────────────────────────────
    def quick_sale(self, amount: float, tcode: str = "01"):
        self.qs_queue.append((amount, tcode))
        self._start_next_qs()

    def _start_next_qs(self):
        if self.qs_active or not self.qs_queue:
            return
        amt, code = self.qs_queue.pop(0)
        self._launch_qs(amt, code)

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

    @pyqtSlot(float, str)
    def _from_webhook(self, amount: float, tcode: str):
        self.quick_sale(amount, tcode)

    # ─── Discovery & Authorize bodies ─────────────────────────
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
        if pay:
            inj += f"<PAYMENTS_NUMBER>{pay:02d}</PAYMENTS_NUMBER>"
        if first:
            inj += f"<FIRST_PAYMENT_AMOUNT>{to_minor(first)}</FIRST_PAYMENT_AMOUNT>"
        if nxt:
            inj += f"<NEXT_PAYMENT_AMOUNT>{to_minor(nxt)}</NEXT_PAYMENT_AMOUNT>"
        return body.replace("</TRANSACTION_DETAILS>", inj + "</TRANSACTION_DETAILS>")

    # ─── TCP send ──────────────────────────────────────────────
    def _send(self, xml: str):
        try:
            port = int(self.port_field.text())
        except ValueError:
            return self._crit("Port", "Invalid port number")
        t = SockThread(self.ip_field.text().strip(), port, xml)
        t.result.connect(self._handle_response)
        t.finished.connect(lambda: self.threads.remove(t))
        self.threads.append(t)
        t.start()

    # ─── Free-Call sandbox ────────────────────────────────────
    def fill_template(self, fg: str, cmd: str):
        xml = template_xml(fg, cmd, self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key)
        self.free_xml_edit.setPlainText(xml)

    def free_send(self):
        raw = self.free_xml_edit.toPlainText().strip()
        if not raw:
            return self._info("Free-Call", "XML area is empty.")
        try:
            signed = raw if re.search(r"<MAC>.*?</MAC>", raw, re.S) else sign_xml(raw, self.mac_key)
        except Exception as exc:
            return self._warn("Free-Call", str(exc))
        self.free_xml_edit.setPlainText(signed)
        self._send(signed)

    # ─── Search-in-logs ───────────────────────────────────────
    def search_logs(self):
        term = self.search_edit.text()
        for pane in (self.sent_log, self.recv_log):
            pane.setExtraSelections([])
            if not term:
                continue
            doc = pane.document()
            cur = QTextCursor(doc)
            sels = []
            while True:
                cur = doc.find(term, cur)
                if cur.isNull():
                    break
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cur
                fmt = QTextCharFormat()
                fmt.setBackground(QColor("#ffff66"))
                sel.format = fmt
                sels.append(sel)
            pane.setExtraSelections(sels)

    # ─── Auto-cancel helpers ───────────────────────────────────
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

    # ─── Central response handler ─────────────────────────────
    def _handle_response(self, sent: str, recv: str):
        self.sent_log.append(sent)
        self.recv_log.append(recv)

        # auto-cancel
        if "<RESULT_CODE>2<" in recv and "<COMMAND>CANCEL" not in sent:
            self._schedule_cancel()
            return

        # Quick-Sale FSM
        if self.qs_active:
            # PING → STATUS
            if "<COMMAND>PING" in sent:
                if "<RESULT_CODE>0<" in recv:
                    self._send_status()
                else:
                    self._qs_done()
                return

            # STATUS → START_TRAN (with SHVA_STATUS check)
            if "<COMMAND>STATUS" in sent:
                if "<RESULT_CODE>0<" in recv:
                    # if SHVA_STATUS exists and ≠1, send EOD
                    m = re.search(r"<SHVA_STATUS>(\d+)", recv)
                    if m and m.group(1) != "1":
                        self.cmd_eod()
                    self._send_start_tran()
                else:
                    self._qs_done()
                return

            # START_TRAN → DISCOVERY
            if "<COMMAND>START_TRAN" in sent:
                if "<RESULT_CODE>0<" in recv:
                    body = self._disc_body(self.qs_defaults)
                    xml = env_xml("PAYMENT", "DISCOVERY", self.session,
                                  bool(int(self.train_combo.currentText())), self.mac_key, body)
                    self._send(xml)
                else:
                    self._qs_done()
                return

            # DISCOVERY → CreditTermDlg → AUTHORIZE
            if "<COMMAND>DISCOVERY" in sent:
                if "<RESULT_CODE>0<" not in recv:
                    self._qs_done()
                    return
                flags = {k: bool(re.search(fr"<TERMS_{k.upper()}>1</TERMS_{k.upper()}>", recv))
                         for k in ("regular", "special", "immediate", "credit", "installments")}
                def get_int(tag, default):
                    m = re.search(fr"<{tag}>(\d+)", recv)
                    return int(m.group(1)) if m else default
                mn = max(2, get_int("CREDIT_MIN_PAYMENTS", 2))
                mx = max(2, get_int("CREDIT_MAX_PAYMENTS", 36))
                dlg = CreditTermDlg(flags, mn, mx, self._current_amount, None)
                if dlg.exec_() != QDialog.Accepted:
                    self._send_cancel()
                    return
                ct, pay, first, nxt = dlg.data()
                body = self._auth_body(self.qs_defaults, ct, pay, first, nxt)
                xml = env_xml("PAYMENT", "AUTHORIZE", self.session,
                              bool(int(self.train_combo.currentText())), self.mac_key, body)
                self._send(xml)
                return

            # AUTHORIZE → save_receipt → FINISH_TRAN
            if "<COMMAND>AUTHORIZE" in sent:
                save_receipt(recv)
                try:
                    with open("receipt.json", "rb") as f:
                        self.webhook.publish(f.read())
                except Exception:
                    pass
                xml = env_xml("SESSION", "FINISH_TRAN", self.session,
                              bool(int(self.train_combo.currentText())), self.mac_key)
                self._send(xml)
                return

            # FINISH_TRAN → done
            if "<COMMAND>FINISH_TRAN" in sent:
                self._qs_done()
                return

        # Cancel reply
        if "<COMMAND>CANCEL" in sent:
            m = re.search(r"<RESULT_CODE>(\d+)<", recv)
            code = m.group(1) if m else None
            if code not in ("0", "50", "51"):
                self._warn("Cancel failed", f"code {code or '?'}")
            xml = env_xml("SESSION", "FINISH_TRAN", self.session,
                          bool(int(self.train_combo.currentText())), self.mac_key)
            self._send(xml)
            return

        # Register → enable keys
        if "<COMMAND>REGISTER" in sent and "<EVENT>COMPLETED" in recv:
            self.ktk_field.setEnabled(True)
            self.key_btn.setEnabled(True)

        # Exchange keys → decrypt
        if "<COMMAND>EXCHANGE_KEYS" in sent and "<MAC_KEY>" in recv and self._manual_key_exchange:
            m = re.search(r"<MAC_KEY>([^<]+)</MAC_KEY>", recv)
            if m:
                enc = m.group(1)
                try:
                    self.mac_key = des3_decrypt(self.ktk_field.text().strip(), enc)
                    save_mac(self.mac_key)
                    self.mac_view.setText(self.mac_key)
                    for b in (self.status_btn, self.eod_btn, self.qs_btn,
                              self.free_send_btn, *self.free_cmds):
                        b.setEnabled(True)
                except Exception as exc:
                    self._crit("Decrypt Error", str(exc))
                finally:
                    self._manual_key_exchange = False


if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
