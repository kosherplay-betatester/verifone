"""
gui/main_window.py

Main application window:
- Constructs the full Qt GUI
- Manages application state (session, keys, threads, FSM)
- Routes button clicks to device commands (REGISTER, EXCHANGE_KEYS, STATUS, Quick-Sale, Free-Call)
- Handles network I/O on background threads
- Drives the Quick-Sale transaction FSM (START_TRAN → DISCOVERY → AUTHORIZE → FINISH_TRAN)
- Auto-cancels on RESULT_CODE 2
- Saves receipts in Hebrew after successful payment
- Hosts a local webhook server for /pay?amount=… callbacks
"""

import re
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QLabel, QLineEdit, QComboBox, QPushButton,
    QTextEdit, QVBoxLayout, QHBoxLayout, QSplitter, QMessageBox,
    QDialog, QDoubleSpinBox
)
from PyQt5.QtCore    import Qt, QTimer, pyqtSlot
from PyQt5.QtGui     import QTextCursor, QTextCharFormat, QColor

from config    import load_settings, load_mac, save_mac
from utils     import rand_session, to_minor, des3_decrypt
from xml_sign  import template_xml, env_xml, sign_xml
from network   import SockThread, WebhookServer
from dialogs   import CreditTermDlg, DiscoverDlg
from receipt   import save_receipt

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 – Quick Sale + Webhook + Free Call")
        self.resize(1120, 850)

        # ───── Application State ─────
        self.session            = ""           # Current SESSION_ID
        self.mac_key            = load_mac()   # Decrypted MAC key (persisted)
        self.threads            = []           # Active SockThread instances
        self.qs_active          = False        # Quick-Sale in progress?
        self.qs_defaults        = {}           # DISCOVERY parameters
        self.qs_start_body      = ""           # Custom START_TRAN body
        self._cancel_timer      = None         # QTimer for auto-cancel
        self._awaiting_cancel   = False        # Whether cancel is pending
        # ─ Only save mac.txt when user clicks Exchange Keys ─
        self._manual_key_exchange = False

        # Load or create settings.txt (with placeholders if first run)
        settings = load_settings()

        # ───── Build GUI ─────
        central = QWidget()
        layout  = QVBoxLayout(central)

        # Connection row: Device IP, Port, Training mode
        conn_row = QHBoxLayout()
        self.ip_field    = QLineEdit(settings["ip"])
        self.port_field  = QLineEdit(settings["port"])
        self.train_combo = QComboBox()
        self.train_combo.addItems(["0","1"])
        self.train_combo.setCurrentIndex(1 if settings.get("training", False) else 0)
        conn_row.addWidget(QLabel("IP:"));       conn_row.addWidget(self.ip_field)
        conn_row.addWidget(QLabel("Port:"));     conn_row.addWidget(self.port_field)
        conn_row.addWidget(QLabel("Training"));  conn_row.addWidget(self.train_combo)
        layout.addLayout(conn_row)

        # Registration row: Chain, Store, Lane, Alt-ID, POS Type
        reg_row = QHBoxLayout()
        self.chain_field = QLineEdit(settings["chain"])
        self.store_field = QLineEdit(settings["store"])
        self.lane_field  = QLineEdit(settings["lane"])
        self.alt_field   = QLineEdit(settings["alt"])
        self.pos_combo   = QComboBox()
        self.pos_combo.addItems(["ATTENDED","UNATTENDED"])
        self.pos_combo.setCurrentText(settings.get("pos_type","ATTENDED"))
        for lbl, w in [
            ("Chain", self.chain_field),
            ("Store", self.store_field),
            ("Lane",  self.lane_field),
            ("Alt-ID",self.alt_field)
        ]:
            reg_row.addWidget(QLabel(lbl)); reg_row.addWidget(w)
        reg_row.addWidget(QLabel("POS Type")); reg_row.addWidget(self.pos_combo)
        self.reg_btn = QPushButton("Register")
        self.reg_btn.clicked.connect(self.cmd_register)
        reg_row.addWidget(self.reg_btn)
        layout.addLayout(reg_row)

        # Key exchange row: KTK input + Exchange Keys button
        key_row = QHBoxLayout()
        self.ktk_field = QLineEdit(settings.get("ktk",""))
        self.ktk_field.setEnabled(False)
        self.key_btn   = QPushButton("Exchange Keys")
        self.key_btn.setEnabled(False)
        self.key_btn.clicked.connect(self.cmd_keys)
        key_row.addWidget(QLabel("KTK (16)")); key_row.addWidget(self.ktk_field)
        key_row.addWidget(self.key_btn)
        layout.addLayout(key_row)

        # MAC viewer row: shows decrypted MAC key
        mac_row = QHBoxLayout()
        self.mac_view = QLineEdit(self.mac_key)
        self.mac_view.setReadOnly(True)
        mac_row.addWidget(QLabel("MAC Key")); mac_row.addWidget(self.mac_view)
        layout.addLayout(mac_row)

        # Quick-Sale & Admin commands row
        action_row = QHBoxLayout()
        action_row.addWidget(QLabel("Amount ₪"))
        self.qs_amount = QDoubleSpinBox(
            decimals=2, maximum=999999, value=settings.get("quick_amount",0.0)
        )
        action_row.addWidget(self.qs_amount)
        self.qs_btn = QPushButton("Quick Sale")
        self.qs_btn.setEnabled(bool(self.mac_key))
        self.qs_btn.clicked.connect(
            lambda: self.quick_sale(self.qs_amount.value(), '01')
        )
        action_row.addWidget(self.qs_btn)
        action_row.addStretch()
        action_row.addWidget(QLabel("Admin CMD"))
        self.admin_edit = QLineEdit()
        self.admin_send = QPushButton("Send")
        self.admin_send.clicked.connect(self.admin_cmd)
        action_row.addWidget(self.admin_edit); action_row.addWidget(self.admin_send)
        layout.addLayout(action_row)

        # Status button
        self.status_btn = QPushButton("Status")
        self.status_btn.setEnabled(bool(self.mac_key))
        self.status_btn.clicked.connect(self.cmd_status)
        layout.addWidget(self.status_btn)

        # Logs: sent XML / received XML
        self.sent_log = QTextEdit(readOnly=True)
        self.recv_log = QTextEdit(readOnly=True)
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.sent_log); splitter.addWidget(self.recv_log)
        layout.addWidget(splitter, stretch=1)

        # Search and clear controls
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search"))
        self.search_edit = QLineEdit()
        self.search_btn  = QPushButton("Find")
        self.search_btn.clicked.connect(self.search_logs)
        self.search_edit.returnPressed.connect(self.search_logs)
        search_row.addWidget(self.search_edit); search_row.addWidget(self.search_btn)
        layout.addLayout(search_row)
        clear_btn = QPushButton("Clear logs")
        clear_btn.clicked.connect(
            lambda: (self.sent_log.clear(), self.recv_log.clear())
        )
        layout.addWidget(clear_btn)

        # Free-Call XML sandbox
        sandbox_row = QVBoxLayout()
        sandbox_row.addWidget(QLabel("<b>Free Call XML Sandbox</b>"))
        self.free_xml_edit = QTextEdit()
        sandbox_row.addWidget(self.free_xml_edit, stretch=1)
        btn_row = QHBoxLayout()
        self.free_send_btn = QPushButton("Send Free Call")
        self.free_send_btn.setEnabled(bool(self.mac_key))
        self.free_send_btn.clicked.connect(self.free_send)
        btn_row.addWidget(self.free_send_btn)
        # Buttons for each free-call command
        self.free_cmds = []
        for fg, cmd, label in [
            ('SESSION','START_TRAN','Start Tran'),
            ('PAYMENT','DISCOVERY','Discover'),
            ('PAYMENT','AUTHORIZE','Authorize'),
            ('SESSION','FINISH_TRAN','Finish Tran')
        ]:
            b = QPushButton(label)
            b.setEnabled(bool(self.mac_key))
            b.clicked.connect(lambda _,g=fg,c=cmd: self.fill_template(g,c))
            btn_row.addWidget(b)
            self.free_cmds.append(b)
        sandbox_row.addLayout(btn_row)
        layout.addLayout(sandbox_row, stretch=2)

        self.setCentralWidget(central)

        # ───── Webhook Server Setup ─────
        wh = settings.get("webhook_host", "localhost") or "localhost"
        try:
            wp = int(settings.get("webhook_port", 8080))
        except (ValueError, TypeError):
            wp = 8080
        self.webhook = WebhookServer(wh, wp, self._from_webhook)
        self.webhook.start()

    # ───────────────────────────────────────────────────────
    # 1) Mark manual key exchanges
    # ───────────────────────────────────────────────────────

    def cmd_keys(self):
        """
        Send ADMIN EXCHANGE_KEYS using user-entered KTK.
        Only when user clicks this do we allow saving the new MAC.
        """
        self._manual_key_exchange = True
        ktk = self.ktk_field.text().strip()
        xml = env_xml(
            fg="ADMIN", cmd="EXCHANGE_KEYS",
            session=self.session,
            training=bool(int(self.train_combo.currentText())),
            mac_key=self.mac_key,
            body=f"<KTK>{ktk}</KTK>"
        )
        self._send(xml)

    # ───────────────────────────────────────────────────────
    # Free-Call Helpers
    # ───────────────────────────────────────────────────────

    def fill_template(self, fg: str, cmd: str):
        xml = template_xml(
            fg=fg,
            cmd=cmd,
            session=self.session,
            training=bool(int(self.train_combo.currentText())),
            mac_key=self.mac_key
        )
        self.free_xml_edit.setPlainText(xml)

    def free_send(self):
        raw = self.free_xml_edit.toPlainText().strip()
        if not raw:
            return QMessageBox.information(self, "Free Call", "XML area is empty.")
        try:
            signed = self._sign_xml(raw)
        except Exception as e:
            return QMessageBox.warning(self, "Free Call", str(e))

        self.free_xml_edit.setPlainText(signed)
        self._send(signed)

    # ───────────────────────────────────────────────────────
    # Low-Level Send
    # ───────────────────────────────────────────────────────

    def _send(self, xml: str):
        try:
            port = int(self.port_field.text())
        except ValueError:
            return QMessageBox.critical(self, "Port", "Invalid port number")
        thread = SockThread(self.ip_field.text().strip(), port, xml)
        thread.result.connect(self._handle_response)
        thread.finished.connect(lambda: self.threads.remove(thread))
        self.threads.append(thread)
        thread.start()

    # ───────────────────────────────────────────────────────
    # Admin / Register / Status Commands
    # ───────────────────────────────────────────────────────

    def cmd_register(self):
        """
        Send ADMIN REGISTER to begin key exchange flow.
        """
        self.session = rand_session()
        body = (
            f"<CHAIN>{self.chain_field.text()}</CHAIN>"
            f"<STORE>{self.store_field.text()}</STORE>"
            f"<LANE>{self.lane_field.text()}</LANE>"
            f"<TERMINAL_ID>{self.alt_field.text()}</TERMINAL_ID>"
            f"<POS_TYPE>{self.pos_combo.currentText()}</POS_TYPE>"
        )
        xml = env_xml(
            fg="ADMIN", cmd="REGISTER",
            session=self.session,
            training=bool(int(self.train_combo.currentText())),
            mac_key=self.mac_key,
            body=body
        )
        self._send(xml)

    def cmd_status(self):
        """
        Send ADMIN STATUS to query device health.
        """
        xml = env_xml(
            fg="ADMIN", cmd="STATUS",
            session=self.session,
            training=bool(int(self.train_combo.currentText())),
            mac_key=self.mac_key
        )
        self._send(xml)

    def admin_cmd(self):
        """
        Send arbitrary ADMIN <cmd> typed by the user.
        """
        cmd = self.admin_edit.text().strip().upper()
        if not cmd:
            return
        xml = env_xml(
            fg="ADMIN", cmd=cmd,
            session=self.session,
            training=bool(int(self.train_combo.currentText())),
            mac_key=self.mac_key
        )
        self._send(xml)

    # ───────────────────────────────────────────────────────
    # Quick-Sale Flow
    # ───────────────────────────────────────────────────────

    def quick_sale(self, amount: float, tcode: str='01'):
        if self.qs_active:
            return QMessageBox.warning(self, "Quick Sale", "Already running.")
        if not self.session:
            self.session = rand_session()

        self.qs_defaults = {
            'timeout':       '60',
            'restrict_token':'0',
            'manual':        False,
            'manual_reason':'',
            'tran_type':     tcode,
            'amount':        to_minor(amount),
            'currency':      '376',
            'cash':          None,
            'fx':            False,
            'fx_to':         '',
            'fx_amt':        None,
            'use_token':     False,
            'token_val':     '',
            'service_type':  '',
            'ctls':          True,
            'allow_cancel':  True,
            'unattended':    False,
            'operation':     '04'
        }

        self.qs_start_body = (
            "<INVOICE>100000</INVOICE>"
            f"<POS_TYPE>{self.pos_combo.currentText()}</POS_TYPE>"
        )
        self.qs_active = True

        xml = env_xml(
            fg="SESSION", cmd="START_TRAN",
            session=self.session,
            training=bool(int(self.train_combo.currentText())),
            mac_key=self.mac_key,
            body=self.qs_start_body
        )
        self._send(xml)

    @pyqtSlot(float, str)
    def _from_webhook(self, amount: float, tcode: str):
        self.quick_sale(amount, tcode)

    # ───────────────────────────────────────────────────────
    # Log Searching
    # ───────────────────────────────────────────────────────

    def search_logs(self):
        term = self.search_edit.text()
        for pane in (self.sent_log, self.recv_log):
            pane.setExtraSelections([])
            if not term:
                continue
            doc = pane.document()
            cursor = QTextCursor(doc)
            selections = []
            while True:
                cursor = doc.find(term, cursor)
                if cursor.isNull():
                    break
                sel = QTextEdit.ExtraSelection()
                sel.cursor = cursor
                fmt = QTextCharFormat()
                fmt.setBackground(QColor("#ffff66"))
                sel.format = fmt
                selections.append(sel)
            pane.setExtraSelections(selections)

    # ───────────────────────────────────────────────────────
    # Auto-cancel Helpers
    # ───────────────────────────────────────────────────────

    def _schedule_cancel(self):
        if self._awaiting_cancel:
            return
        self._awaiting_cancel = True
        self._cancel_timer = QTimer(self)
        self._cancel_timer.setSingleShot(True)
        self._cancel_timer.timeout.connect(self._send_cancel)
        self._cancel_timer.start(2000)

    def _send_cancel(self):
        self._cancel_timer = None
        xml = env_xml(
            fg="GENERAL", cmd="CANCEL",
            session=self.session,
            training=bool(int(self.train_combo.currentText())),
            mac_key=self.mac_key
        )
        self._send(xml)

    # ───────────────────────────────────────────────────────
    # Central Response Handler (with guarded save_mac)
    # ───────────────────────────────────────────────────────

    def _handle_response(self, sent: str, recv: str):
        # Append to UI logs
        self.sent_log.append(sent)
        self.recv_log.append(recv)

        # Auto-cancel on RESULT_CODE 2
        if '<RESULT_CODE>2<' in recv and '<COMMAND>CANCEL' not in sent:
            self._schedule_cancel()
            return

        # Quick-Sale FSM…
        if self.qs_active:
            # START_TRAN response
            if '<COMMAND>START_TRAN' in sent:
                if '<RESULT_CODE>0<' in recv:
                    body = self._disc_body(self.qs_defaults)
                    xml  = env_xml(
                        fg="PAYMENT", cmd="DISCOVERY",
                        session=self.session,
                        training=bool(int(self.train_combo.currentText())),
                        mac_key=self.mac_key,
                        body=body
                    )
                    self._send(xml)
                else:
                    self.qs_active = False
                return

            # DISCOVERY response
            if '<COMMAND>DISCOVERY' in sent:
                if '<RESULT_CODE>0<' not in recv:
                    self.qs_active = False
                    return

                flags = {
                    k: bool(re.search(
                        fr'<TERMS_{k.upper()}>1</TERMS_{k.upper()}>', recv
                    ))
                    for k in ('regular','special','immediate','credit','installments')
                }
                def get_int(tag, default):
                    m = re.search(fr'<{tag}>(\d+)', recv)
                    return int(m.group(1)) if m else default

                mn = max(2, get_int('CREDIT_MIN_PAYMENTS', 2))
                mx = max(2, get_int('CREDIT_MAX_PAYMENTS', 36))
                dlg = CreditTermDlg(flags, mn, mx, self.qs_amount.value(), self)
                if dlg.exec_() != QDialog.Accepted:
                    self.qs_active = False
                    QMessageBox.information(self, "Quick Sale", "בוטל")
                    return

                ct, pay, first, nxt = dlg.data()
                body = self._auth_body(self.qs_defaults, ct, pay, first, nxt)
                xml  = env_xml(
                    fg="PAYMENT", cmd="AUTHORIZE",
                    session=self.session,
                    training=bool(int(self.train_combo.currentText())),
                    mac_key=self.mac_key,
                    body=body
                )
                self._send(xml)
                return

            # AUTHORIZE response
            if '<COMMAND>AUTHORIZE' in sent:
                if '<RESULT_CODE>0<' in recv:
                    save_receipt(recv)
                    xml = env_xml(
                        fg="SESSION", cmd="FINISH_TRAN",
                        session=self.session,
                        training=bool(int(self.train_combo.currentText())),
                        mac_key=self.mac_key
                    )
                    self._send(xml)
                return

            # FINISH_TRAN response
            if '<COMMAND>FINISH_TRAN' in sent:
                self.qs_active = False
                return

        # Handle CANCEL response
        if '<COMMAND>CANCEL' in sent:
            m = re.search(r'<RESULT_CODE>(\d+)<', recv)
            code = m.group(1) if m else None
            if code not in ('0','50','51'):
                QMessageBox.warning(self, "Cancel failed", f"code {code or '?'}")
            self._awaiting_cancel = False
            self.qs_active       = False
            xml = env_xml(
                fg="SESSION", cmd="FINISH_TRAN",
                session=self.session,
                training=bool(int(self.train_combo.currentText())),
                mac_key=self.mac_key
            )
            self._send(xml)
            return

        # After REGISTER completes, enable KTK entry
        if '<COMMAND>REGISTER' in sent and '<EVENT>COMPLETED' in recv:
            self.ktk_field.setEnabled(True)
            self.key_btn.setEnabled(True)

        # After EXCHANGE_KEYS, decrypt & only save if manual
        if '<COMMAND>EXCHANGE_KEYS' in sent and '<MAC_KEY>' in recv and self._manual_key_exchange:
            m = re.search(r'<MAC_KEY>([^<]+)</MAC_KEY>', recv)
            if m:
                try:
                    enc = m.group(1)
                    self.mac_key = des3_decrypt(self.ktk_field.text().strip(), enc)
                    save_mac(self.mac_key)
                    self.mac_view.setText(self.mac_key)
                    for btn in (self.status_btn, self.qs_btn, self.free_send_btn, *self.free_cmds):
                        btn.setEnabled(True)
                except Exception as exc:
                    QMessageBox.critical(self, "Decrypt Error", str(exc))
                finally:
                    # Clear the flag so future retries won't overwrite
                    self._manual_key_exchange = False

    # ───────────────────────────────────────────────────────
    # Helpers to build DISCOVERY / AUTHORIZE bodies
    # ───────────────────────────────────────────────────────

    def _disc_body(self, opts: dict) -> str:
        x = (
            f"<TRANSACTION_DETAILS>"
            f"<RESTRICT_TOKEN>{opts['restrict_token']}</RESTRICT_TOKEN>"
            f"<MANUAL>{int(opts['manual'])}</MANUAL>"
            f"<CTLS>{int(opts['ctls'])}</CTLS>"
            f"<ALLOW_CANCEL>{int(opts['allow_cancel'])}</ALLOW_CANCEL>"
            f"<UNATTENDED>{int(opts['unattended'])}</UNATTENDED>"
            f"<TRAN_TYPE>{opts['tran_type']}</TRAN_TYPE>"
            f"<MTI>100</MTI>"
            f"<ENTRY_MODE>04</ENTRY_MODE>"
            f"<TRANSACTION_AMOUNT>{opts['amount']}</TRANSACTION_AMOUNT>"
            f"<ORIGINAL_CURRENCY>{opts['currency']}</ORIGINAL_CURRENCY>"
        )
        if opts['manual'] and opts['manual_reason']:
            x += f"<MANUAL_REASON>{opts['manual_reason']}</MANUAL_REASON>"
        if opts['cash'] is not None:
            x += f"<CASH_AMOUNT>{opts['cash']}</CASH_AMOUNT>"
        if opts['fx']:
            x += (
                f"<CONVERTED_AMOUNT>{opts['fx_amt']}</CONVERTED_AMOUNT>"
                f"<CONVERTED_CURRENCY>{opts['fx_to']}</CONVERTED_CURRENCY>"
            )
        if opts['use_token'] and opts['token_val']:
            x += f"<CARD_TOKEN>{opts['token_val']}</CARD_TOKEN>"
        if opts['service_type']:
            x += f"<SERVICE_TYPE>{opts['service_type']}</SERVICE_TYPE>"
        x += f"<OPERATION>{opts['operation']}</OPERATION></TRANSACTION_DETAILS>"
        return f"<TIMEOUT>{opts['timeout']}</TIMEOUT>" + x

    def _auth_body(self, base: dict, ct: str, pay: int, first: float, nxt: float) -> str:
        x   = self._disc_body(base)
        inj = f"<CREDIT_TERMS>{ct}</CREDIT_TERMS>"
        if pay:
            inj += f"<PAYMENTS_NUMBER>{pay:02d}</PAYMENTS_NUMBER>"
        if first:
            inj += f"<FIRST_PAYMENT_AMOUNT>{to_minor(first)}</FIRST_PAYMENT_AMOUNT>"
        if nxt:
            inj += f"<NEXT_PAYMENT_AMOUNT>{to_minor(nxt)}</NEXT_PAYMENT_AMOUNT>"
        return x.replace("</TRANSACTION_DETAILS>", inj + "</TRANSACTION_DETAILS>")
