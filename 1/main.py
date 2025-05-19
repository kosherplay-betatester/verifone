#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# main.py — Verifone P400 connector (Quick-Sale only) with settings persistence
# Always FINISH_TRAN after AUTHORIZE, regardless of result code.

import sys
import os
import re
import json
from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QDoubleSpinBox, QCheckBox, QDialog,
    QHBoxLayout, QVBoxLayout, QSplitter, QMessageBox
)
from utils import rand_session, to_minor, des3_decrypt
from network import SockThread, WebhookServer
from xml_builder import XMLBuilder
from dialogs import CreditTermDlg
from receipt_writer import save_receipt

SETTINGS_PATH = "settings.txt"

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 – Quick Sale + Webhook")
        self.resize(1020, 820)

        # ─── State ───
        self.session = ""
        self.ktk = ""
        self.mac_key = ""
        self.threads = []
        self.qs_active = False
        self.qs_defaults = {}
        self.qs_start_body = ""
        self._awaiting_cancel = False
        self.builder = XMLBuilder(mac_key="", training=False)

        # ─── Build UI ───
        central = QWidget()
        main_layout = QVBoxLayout(central)

        # Connection & training
        conn_layout = QHBoxLayout()
        self.ip           = QLineEdit("192.168.1.202")
        self.port         = QLineEdit("5015")
        self.training_chk = QCheckBox("Training mode")
        for lbl, w in (("IP:", self.ip), ("Port:", self.port)):
            conn_layout.addWidget(QLabel(lbl)); conn_layout.addWidget(w)
        conn_layout.addWidget(self.training_chk)
        main_layout.addLayout(conn_layout)

        # Register
        reg_layout = QHBoxLayout()
        self.chain    = QLineEdit("39999")
        self.store    = QLineEdit("0123")
        self.lane     = QLineEdit("074")
        self.alt      = QLineEdit("012345678")
        self.pos_type = QComboBox(); self.pos_type.addItems(["ATTENDED","UNATTENDED"])
        for lbl, w in (("Chain",self.chain),("Store",self.store),
                       ("Lane",self.lane),("Alt-ID",self.alt)):
            reg_layout.addWidget(QLabel(lbl)); reg_layout.addWidget(w)
        reg_layout.addWidget(QLabel("POS Type")); reg_layout.addWidget(self.pos_type)
        self.reg_btn = QPushButton("Register", clicked=self.cmd_register)
        reg_layout.addWidget(self.reg_btn)
        main_layout.addLayout(reg_layout)

        # Key exchange
        key_layout = QHBoxLayout()
        self.ktk_edit = QLineEdit(); self.ktk_edit.setEnabled(False)
        key_layout.addWidget(QLabel("KTK (16)")); key_layout.addWidget(self.ktk_edit)
        self.key_btn  = QPushButton("Exchange Keys", clicked=self.cmd_keys)
        self.key_btn.setEnabled(False)
        key_layout.addWidget(self.key_btn)
        main_layout.addLayout(key_layout)

        # MAC display
        mac_layout = QHBoxLayout()
        self.mac_view = QLineEdit(); self.mac_view.setReadOnly(True)
        mac_layout.addWidget(QLabel("MAC Key")); mac_layout.addWidget(self.mac_view)
        main_layout.addLayout(mac_layout)

        # Quick-Sale controls
        qs_layout = QHBoxLayout()
        qs_layout.addWidget(QLabel("Amount ₪"))
        self.qs_amt = QDoubleSpinBox()
        self.qs_amt.setDecimals(2); self.qs_amt.setMaximum(999999); self.qs_amt.setValue(10.00)
        qs_layout.addWidget(self.qs_amt)
        self.quick_btn = QPushButton("Quick Sale", clicked=lambda: self.quick_sale(self.qs_amt.value(),'01'))
        self.quick_btn.setEnabled(False)
        qs_layout.addWidget(self.quick_btn)
        main_layout.addLayout(qs_layout)

        # Admin & Status
        admin_layout = QHBoxLayout()
        admin_layout.addWidget(QLabel("Admin"))
        self.cmd_in   = QLineEdit()
        self.cmd_send = QPushButton("Send", clicked=self.admin_cmd)
        admin_layout.addWidget(self.cmd_in); admin_layout.addWidget(self.cmd_send)
        self.status_btn = QPushButton("Status", clicked=self.cmd_status)
        self.status_btn.setEnabled(False)
        admin_layout.addWidget(self.status_btn)
        main_layout.addLayout(admin_layout)

        # Logs
        self.sent = QTextEdit(readOnly=True)
        self.recv = QTextEdit(readOnly=True)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.sent); splitter.addWidget(self.recv)
        main_layout.addWidget(splitter)

        # Search / Clear / Save settings
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel("Search"))
        self.search_edit = QLineEdit()
        self.search_btn  = QPushButton("Find", clicked=self.search_logs)
        self.search_edit.returnPressed.connect(self.search_logs)
        bottom.addWidget(self.search_edit); bottom.addWidget(self.search_btn)
        clear_btn = QPushButton("Clear logs", clicked=lambda: (self.sent.clear(), self.recv.clear()))
        bottom.addWidget(clear_btn)
        save_btn  = QPushButton("Save Settings", clicked=self.save_settings)
        bottom.addWidget(save_btn)
        main_layout.addLayout(bottom)

        self.setCentralWidget(central)

        # ─── Init ───
        self.load_settings()
        self._load_mac()
        self.webhook = WebhookServer(parent=self)
        self.webhook.receivedPayment.connect(self._from_webhook)
        self.webhook.start()

    # ─── Settings persistence ───
    def save_settings(self):
        cfg = {
            "ip":        self.ip.text(),
            "port":      self.port.text(),
            "training":  self.training_chk.isChecked(),
            "chain":     self.chain.text(),
            "store":     self.store.text(),
            "lane":      self.lane.text(),
            "alt":       self.alt.text(),
            "pos_type":  self.pos_type.currentText(),
            "qs_amount": self.qs_amt.value(),
            "ktk":       self.ktk_edit.text(),
        }
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            QMessageBox.information(self, "Settings", "Saved to settings.txt")
        except Exception as e:
            QMessageBox.critical(self, "Settings", f"Failed to save:\n{e}")

    def load_settings(self):
        if not os.path.exists(SETTINGS_PATH):
            return
        try:
            cfg = json.load(open(SETTINGS_PATH, encoding="utf-8"))
            self.ip.setText(cfg.get("ip", self.ip.text()))
            self.port.setText(cfg.get("port", self.port.text()))
            self.training_chk.setChecked(cfg.get("training", False))
            self.chain.setText(cfg.get("chain", self.chain.text()))
            self.store.setText(cfg.get("store", self.store.text()))
            self.lane.setText(cfg.get("lane", self.lane.text()))
            self.alt.setText(cfg.get("alt", self.alt.text()))
            self.pos_type.setCurrentText(cfg.get("pos_type", self.pos_type.currentText()))
            self.qs_amt.setValue(float(cfg.get("qs_amount", self.qs_amt.value())))
            self.ktk_edit.setText(cfg.get("ktk", self.ktk_edit.text()))
            self.builder.training = self.training_chk.isChecked()
        except Exception as e:
            QMessageBox.warning(self, "Settings", f"Could not load settings.txt:\n{e}")

    # ─── Auto-save on exit + cleanup ───
    def closeEvent(self, ev):
        self.save_settings()
        self.webhook.terminate()
        for t in list(self.threads):
            t.quit(); t.wait()
        ev.accept()

    # ─── MAC persistence ───
    def _load_mac(self):
        if os.path.exists("mac.txt"):
            self.mac_key = open("mac.txt", encoding="utf-8").read().strip()
            if self.mac_key:
                self.builder.mac_key = self.mac_key
                self.mac_view.setText(self.mac_key)
                for b in (self.status_btn, self.quick_btn):
                    b.setEnabled(True)

    def _save_mac(self):
        with open("mac.txt", "w", encoding="utf-8") as f:
            f.write(self.mac_key)

    # ─── Send helper ───
    def _send(self, xml: str):
        try:
            port = int(self.port.text())
        except ValueError:
            QMessageBox.critical(self, "Port", "Invalid port")
            return
        t = SockThread(self.ip.text().strip(), port, xml)
        t.result.connect(self._handle)
        t.finished.connect(lambda: self.threads.remove(t))
        self.threads.append(t)
        t.start()

    # ─── Button slots ───
    def cmd_register(self):
        self.session = rand_session()
        body = (
            f"<CHAIN>{self.chain.text()}</CHAIN>"
            f"<STORE>{self.store.text()}</STORE>"
            f"<LANE>{self.lane.text()}</LANE>"
            f"<TERMINAL_ID>{self.alt.text()}</TERMINAL_ID>"
            f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>"
        )
        self.builder.training = self.training_chk.isChecked()
        xml = self.builder.envelope("ADMIN", "REGISTER", self.session, body)
        self._send(xml)

    def cmd_keys(self):
        self.ktk = self.ktk_edit.text().strip()
        self.builder.training = self.training_chk.isChecked()
        xml = self.builder.envelope("ADMIN", "EXCHANGE_KEYS", self.session, f"<KTK>{self.ktk}</KTK>")
        self._send(xml)

    def cmd_status(self):
        self.builder.training = self.training_chk.isChecked()
        xml = self.builder.envelope("ADMIN", "STATUS", self.session)
        self._send(xml)

    def quick_sale(self, amount: float, tcode: str = "01"):
        if self.qs_active:
            QMessageBox.warning(self, "Quick Sale", "Already running.")
            return
        if not self.session:
            self.session = rand_session()
        self.builder.training = self.training_chk.isChecked()
        self.qs_start_body = (
            f"<INVOICE>100000</INVOICE>"
            f"<POS_TYPE>{self.pos_type.currentText()}</POS_TYPE>"
        )
        self.qs_defaults = {
            'timeout': '60',
            'restrict_token': '0',
            'manual': False,
            'ctls': True,
            'allow_cancel': True,
            'unattended': False,
            'tran_type': tcode,
            'amount': to_minor(amount),
            'currency': '376'
        }
        xml = self.builder.envelope("SESSION", "START_TRAN", self.session, self.qs_start_body)
        self.qs_active = True
        self._send(xml)

    @pyqtSlot(float, str)
    def _from_webhook(self, amt: float, tcode: str):
        self.quick_sale(amt, tcode)

    def admin_cmd(self):
        cmd = self.cmd_in.text().strip().upper()
        if not cmd:
            return
        xml = self.builder.envelope("ADMIN", cmd, self.session)
        self._send(xml)

    def search_logs(self):
        term = self.search_edit.text()
        for pane in (self.sent, self.recv):
            pane.setExtraSelections([])
            if not term:
                continue
            doc = pane.document()
            cur = doc.find(term)
            sels = []
            while not cur.isNull():
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cur
                fmt = sel.format; fmt.setBackground(Qt.yellow)
                sel.format = fmt
                sels.append(sel)
                cur = doc.find(term, cur)
            pane.setExtraSelections(sels)

    # ─── Auto-cancel ───
    def _schedule_cancel(self):
        if self._awaiting_cancel:
            return
        self._awaiting_cancel = True
        timer = QTimer(self); timer.setSingleShot(True)
        timer.timeout.connect(self._send_cancel)
        timer.start(2000)

    def _send_cancel(self):
        self._awaiting_cancel = False
        xml = self.builder.envelope("GENERAL", "CANCEL", self.session)
        self._send(xml)

    # ─── Response handler ───
    def _handle(self, sent: str, recv: str):
        self.sent.append(sent)
        self.recv.append(recv)

        # auto-cancel
        if '<RESULT_CODE>2<' in recv and '<COMMAND>CANCEL' not in sent:
            self._schedule_cancel()
            return

        # Quick-Sale FSM
        if self.qs_active:
            # after START_TRAN → DISCOVERY
            if '<COMMAND>START_TRAN' in sent:
                if '<RESULT_CODE>0<' in recv:
                    xml = self.builder.discovery(
                        session=self.session,
                        timeout=int(self.qs_defaults['timeout']),
                        restrict_token=int(self.qs_defaults['restrict_token']),
                        manual=self.qs_defaults['manual'],
                        ctls=self.qs_defaults['ctls'],
                        allow_cancel=self.qs_defaults['allow_cancel'],
                        unattended=self.qs_defaults['unattended'],
                        tran_type=self.qs_defaults['tran_type'],
                        mti="100",
                        entry_mode="04",
                        amount=self.qs_defaults['amount'],
                        currency=self.qs_defaults['currency']
                    )
                    self._send(xml)
                else:
                    self.qs_active = False
                return

            # after DISCOVERY → terms dialog
            if '<COMMAND>DISCOVERY' in sent:
                if '<RESULT_CODE>0<' not in recv:
                    self.qs_active = False
                    return
                flags = {
                    f: bool(re.search(fr'<TERMS_{f.upper()}>1', recv))
                    for f in ('regular','special','immediate','credit','installments')
                }
                min_p = int(re.search(r'<CREDIT_MIN_PAYMENTS>(\d+)', recv).group(1)) if re.search(r'<CREDIT_MIN_PAYMENTS>', recv) else 2
                max_p = int(re.search(r'<CREDIT_MAX_PAYMENTS>(\d+)', recv).group(1)) if re.search(r'<CREDIT_MAX_PAYMENTS>', recv) else 36
                dlg = CreditTermDlg(flags, min_p, max_p, float(self.qs_defaults['amount'])/100, self)
                if dlg.exec_() != QDialog.Accepted:
                    self.qs_active = False
                    QMessageBox.information(self, "Quick Sale", "בוטל")
                    return
                ct, pay, first, nxt = dlg.data()
                xml = self.builder.authorize(
                    session=self.session,
                    operation=4,
                    tran_type=self.qs_defaults['tran_type'],
                    mti="100",
                    amount=self.qs_defaults['amount'],
                    currency=self.qs_defaults['currency'],
                    credit_terms=int(ct),
                    payments_number=int(pay)
                )
                self._send(xml)
                return

            # after AUTHORIZE → always finish
            if '<COMMAND>AUTHORIZE' in sent:
                if '<RESULT_CODE>0<' in recv:
                    save_receipt(recv, self)
                else:
                    QMessageBox.warning(self, "Authorize", "Non-zero result, closing session")
                xml = self.builder.finish_transaction(self.session, show_screen=False)
                self._send(xml)
                self.qs_active = False
                return

        # register/key exchange responses
        if '<COMMAND>REGISTER' in sent and '<EVENT>COMPLETED' in recv:
            self.ktk_edit.setEnabled(True); self.key_btn.setEnabled(True)
        if '<COMMAND>EXCHANGE_KEYS' in sent and '<MAC_KEY>' in recv:
            m = re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>', recv)
            if m:
                try:
                    self.mac_key = des3_decrypt(self.ktk, m.group(1))
                    self.builder.mac_key = self.mac_key
                    self.mac_view.setText(self.mac_key)
                    self._save_mac()
                    for b in (self.status_btn, self.quick_btn):
                        b.setEnabled(True)
                except Exception as e:
                    QMessageBox.critical(self, "Decrypt", str(e))

if __name__ == "__main__":
    # High DPI Must Be Set Before QApplication Creation
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
