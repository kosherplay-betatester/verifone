#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# gui/main_window.py  –  FULL FILE  (v2.3 • added GET_TRAN_DETAILS webhook support)

"""
Main admin window for Verifone-P400 desktop app
==============================================

Changes in v2.3
---------------
• Added `cmd_get_trans_details` method to send REPORT/GET_TRAN_DETAILS for
  the last known TRANS_ID via webhook.
• Updated WebhookServer instantiation to accept the new GET_TRAN_DETAILS endpoint.
• Made KTK field editable immediately and enabled the “Exchange Keys” button
  after a successful Register.

Earlier v2.2
------------
• Removed the automatic EOD scheduler and its time‑picker.
  EOD can now be triggered only manually via the “EOD” button.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from typing import Final

from PyQt5.QtCore    import Qt
from PyQt5.QtGui     import QTextCursor, QTextCharFormat, QColor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QVBoxLayout, QHBoxLayout, QSplitter,
    QDoubleSpinBox
)

# Project-local helpers
from config     import load_settings, load_mac, save_settings
from network    import SockThread, WebhookServer
from utils      import rand_session, des3_decrypt
from xml_sign   import env_xml, sign_xml, template_xml
from pay_process import QuickSaleMixin            # ⬅ payment FSM lives here


class MainWindow(QuickSaleMixin, QMainWindow):  # mixin *first* in MRO
    """Admin GUI (everything except the payment FSM)."""

    def __init__(self):
        super().__init__()  # QMainWindow ctor

        self.setWindowTitle("Verifone P400 – Quick Sale / Webhook / Admin")
        self.resize(1120, 860)

        # ───── runtime ─────
        self.session              = ""
        self.mac_key              = load_mac()
        self.threads: list[SockThread] = []

        # QuickSaleMixin flags
        self.qs_active            = False
        self.qs_queue: list[tuple[float, str]] = []
        self._current_amount      = 0.0
        self.qs_defaults: dict    = {}
        self.qs_start_body        = ""
        self._awaiting_cancel     = False
        self._cancel_timer        = None
        self._manual_key_exchange = False
        self._qs_phase            = ""
        self._trans_id            = ""

        cfg = load_settings()

        # ───── UI ─────
        root = QWidget()
        layout = QVBoxLayout(root)

        # Connection row
        row = QHBoxLayout()
        self.ip_field   = QLineEdit(cfg["ip"])
        self.port_field = QLineEdit(cfg["port"])
        self.train_combo = QComboBox()
        self.train_combo.addItems(["0", "1"])
        self.train_combo.setCurrentIndex(1 if cfg.get("training") else 0)
        for lbl, w in [("IP", self.ip_field),
                       ("Port", self.port_field),
                       ("Training", self.train_combo)]:
            row.addWidget(QLabel(lbl))
            row.addWidget(w)
        layout.addLayout(row)

        # Registration row
        row = QHBoxLayout()
        self.chain_field  = QLineEdit(cfg["chain"])
        self.store_field  = QLineEdit(cfg["store"])
        self.lane_field   = QLineEdit(cfg["lane"])
        self.alt_field    = QLineEdit(cfg["alt"])
        self.termid_field = QLineEdit(cfg.get("shva_term_id", ""))
        self.pos_combo    = QComboBox()
        self.pos_combo.addItems(["ATTENDED", "UNATTENDED"])
        self.pos_combo.setCurrentText(cfg.get("pos_type", "ATTENDED"))
        for lbl, w in [("Chain", self.chain_field),
                       ("Store", self.store_field),
                       ("Lane", self.lane_field),
                       ("Alt‑ID", self.alt_field),
                       ("SHVA ID", self.termid_field)]:
            row.addWidget(QLabel(lbl))
            row.addWidget(w)
        row.addWidget(QLabel("POS Type"))
        row.addWidget(self.pos_combo)
        self.reg_btn = QPushButton("Register")
        self.reg_btn.clicked.connect(self.cmd_register)
        row.addWidget(self.reg_btn)
        layout.addLayout(row)

        # Save settings
        row = QHBoxLayout()
        row.addStretch(1)
        self.save_btn = QPushButton("Save Settings")
        self.save_btn.clicked.connect(self._save_settings)
        row.addWidget(self.save_btn)
        layout.addLayout(row)

        # Key‑exchange row
        row = QHBoxLayout()
        # Make KTK editable immediately
        self.ktk_field = QLineEdit(cfg.get("ktk", ""))
        self.ktk_field.setEnabled(True)
        self.key_btn = QPushButton("Exchange Keys")
        self.key_btn.setEnabled(False)
        self.key_btn.clicked.connect(self.cmd_keys)
        row.addWidget(QLabel("KTK (16)"))
        row.addWidget(self.ktk_field)
        row.addWidget(self.key_btn)
        layout.addLayout(row)

        # MAC view row
        row = QHBoxLayout()
        self.mac_view = QLineEdit(self.mac_key)
        self.mac_view.setReadOnly(True)
        row.addWidget(QLabel("MAC Key"))
        row.addWidget(self.mac_view)
        layout.addLayout(row)

        # Quick‑sale row
        row = QHBoxLayout()
        row.addWidget(QLabel("Amount ₪"))
        self.qs_amount = QDoubleSpinBox(decimals=2, maximum=999999,
                                        value=cfg.get("quick_amount", 0.0))
        row.addWidget(self.qs_amount)
        self.qs_btn = QPushButton("Quick Sale")
        self.qs_btn.setEnabled(bool(self.mac_key))
        self.qs_btn.clicked.connect(
            lambda: self.quick_sale(self.qs_amount.value(), "01")
        )
        row.addWidget(self.qs_btn)
        row.addStretch(1)
        row.addWidget(QLabel("Admin CMD"))
        self.admin_edit = QLineEdit()
        self.admin_send = QPushButton("Send")
        self.admin_send.clicked.connect(self.admin_cmd)
        row.addWidget(self.admin_edit)
        row.addWidget(self.admin_send)
        layout.addLayout(row)

        # Status / EOD row
        row = QHBoxLayout()
        self.status_btn = QPushButton("Status")
        self.status_btn.setEnabled(bool(self.mac_key))
        self.status_btn.clicked.connect(self.cmd_status)
        row.addWidget(self.status_btn)
        self.eod_btn = QPushButton("EOD")
        self.eod_btn.setEnabled(bool(self.mac_key))
        self.eod_btn.clicked.connect(self.cmd_eod)
        row.addWidget(self.eod_btn)
        layout.addLayout(row)

        # Logs
        self.sent_log = QTextEdit(readOnly=True)
        self.recv_log = QTextEdit(readOnly=True)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.sent_log)
        split.addWidget(self.recv_log)
        layout.addWidget(split, stretch=1)

        # Search
        row = QHBoxLayout()
        row.addWidget(QLabel("Search"))
        self.search_edit = QLineEdit()
        self.search_btn  = QPushButton("Find")
        self.search_btn.clicked.connect(self.search_logs)
        self.search_edit.returnPressed.connect(self.search_logs)
        row.addWidget(self.search_edit)
        row.addWidget(self.search_btn)
        layout.addLayout(row)
        clr = QPushButton("Clear logs")
        clr.clicked.connect(lambda: (self.sent_log.clear(), self.recv_log.clear()))
        layout.addWidget(clr)

        # Free‑call XML sandbox
        box = QVBoxLayout()
        box.addWidget(QLabel("<b>Free‑Call XML Sandbox</b>"))
        self.free_xml_edit = QTextEdit()
        box.addWidget(self.free_xml_edit, stretch=1)
        bar = QHBoxLayout()
        self.free_send_btn = QPushButton("Send Free‑Call")
        self.free_send_btn.setEnabled(bool(self.mac_key))
        self.free_send_btn.clicked.connect(self.free_send)
        bar.addWidget(self.free_send_btn)
        self.free_cmds: list[QPushButton] = []
        for fg, cmd, label in [
            ("SESSION", "START_TRAN",  "Start Tran"),
            ("PAYMENT", "DISCOVERY",   "Discover"),
            ("PAYMENT", "AUTHORIZE",   "Authorize"),
            ("SESSION", "FINISH_TRAN", "Finish Tran"),
        ]:
            b = QPushButton(label)
            b.setEnabled(bool(self.mac_key))
            b.clicked.connect(lambda _, g=fg, c=cmd: self.fill_template(g, c))
            bar.addWidget(b)
            self.free_cmds.append(b)
        box.addLayout(bar)
        layout.addLayout(box, stretch=2)

        self.setCentralWidget(root)

        # ───── Webhook server ─────
        host = cfg.get("webhook_host", "localhost") or "localhost"
        port = int(cfg.get("webhook_port", 8080))
        # pass both callbacks: quick‑sale and GET_TRAN_DETAILS
        self.webhook = WebhookServer(
            host, port,
            self._from_webhook,
            self.cmd_get_trans_details
        )
        self.webhook.start()

    # ───────────────────────────────────────────────────────────
    # Settings helpers
    # ───────────────────────────────────────────────────────────
    def _save_settings(self):
        try:
            data = {
                "ip":           self.ip_field.text().strip(),
                "port":         self.port_field.text().strip(),
                "training":     self.train_combo.currentText() == "1",
                "chain":        self.chain_field.text().strip(),
                "store":        self.store_field.text().strip(),
                "lane":         self.lane_field.text().strip(),
                "alt":          self.alt_field.text().strip(),
                "pos_type":     self.pos_combo.currentText(),
                "ktk":          self.ktk_field.text().strip(),
                "quick_amount": self.qs_amount.value(),
                "webhook_host": "localhost",
                "webhook_port": 8080,
                "shva_term_id": self.termid_field.text().strip(),
            }
            save_settings(data)
            self._info("Settings", "Saved successfully.")
        except Exception as exc:
            self._crit("Settings", f"Save failed: {exc}")

    # ───────────────────────────────────────────────────────────
    # Log‑pane helpers (black text)
    # ───────────────────────────────────────────────────────────
    def _info(self, title: str, text: str):
        self.sent_log.append(
            f'<span style="color:#000000;">[INFO] {title}: {text}</span>'
        )
        self.sent_log.moveCursor(QTextCursor.End)

    def _warn(self, title: str, text: str):
        self.recv_log.append(
            f'<span style="color:#000000;">[WARN] {title}: {text}</span>'
        )
        self.recv_log.moveCursor(QTextCursor.End)

    def _crit(self, title: str, text: str):
        self.recv_log.append(
            f'<span style="color:#000000;">[ERROR] {title}: {text}</span>'
        )
        self.recv_log.moveCursor(QTextCursor.End)

    # ───────────────────────────────────────────────────────────
    # Admin commands
    # ───────────────────────────────────────────────────────────
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
        # now allow entering a new KTK and exchanging keys
        self.ktk_field.setEnabled(True)
        self.key_btn.setEnabled(True)

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

    def cmd_get_trans_details(self):
        """
        Send REPORT/GET_TRAN_DETAILS for the last known TRANS_ID,
        then publish its structured receipt via the webhook.
        """
        if not self._trans_id:
            self._crit("GetDetails", "No last transaction available")
            return
        body = f"<TRANS_ID>{self._trans_id}</TRANS_ID>"
        xml = env_xml(
            "REPORT", "GET_TRAN_DETAILS", self.session,
            bool(int(self.train_combo.currentText())), self.mac_key,
            body
        )
        self._send(xml)

    def admin_cmd(self):
        cmd = self.admin_edit.text().strip().upper()
        if not cmd:
            return
        xml = env_xml("ADMIN", cmd, self.session,
                      bool(int(self.train_combo.currentText())), self.mac_key)
        self._send(xml)

    # ───────────────────────────────────────────────────────────
    # TCP‑send helper
    # ───────────────────────────────────────────────────────────
    def _send(self, xml: str):
        try:
            port = int(self.port_field.text())
        except ValueError:
            return self._crit("Port", "Invalid port")
        thr = SockThread(self.ip_field.text().strip(), port, xml)
        thr.result.connect(self._handle_response)
        thr.finished.connect(lambda: self.threads.remove(thr))
        self.threads.append(thr)
        thr.start()

    # ───────────────────────────────────────────────────────────
    # Free‑call sandbox
    # ───────────────────────────────────────────────────────────
    def fill_template(self, fg: str, cmd: str):
        xml = template_xml(fg, cmd, self.session,
                           bool(int(self.train_combo.currentText())), self.mac_key)
        self.free_xml_edit.setPlainText(xml)

    def free_send(self):
        raw = self.free_xml_edit.toPlainText().strip()
        if not raw:
            return self._info("Free‑Call", "XML area is empty.")
        try:
            signed = (
                raw if re.search(r"<MAC>.*?</MAC>", raw, re.S)
                else sign_xml(raw, self.mac_key)
            )
        except Exception as exc:
            return self._warn("Free‑Call", str(exc))
        self.free_xml_edit.setPlainText(signed)
        self._send(signed)

    # ───────────────────────────────────────────────────────────
    # Log search
    # ───────────────────────────────────────────────────────────
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


if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
