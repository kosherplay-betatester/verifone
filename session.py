import sys
import socket
import random
from datetime import datetime
from base64 import b64encode
from Crypto.Hash import SHA256
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QDialog, QWidget,
    QLabel, QLineEdit, QComboBox,
    QDoubleSpinBox, QSpinBox, QPushButton,
    QTextEdit, QGridLayout, QHBoxLayout,
    QVBoxLayout, QInputDialog, QMessageBox
)


def generate_session_id() -> str:
    return ''.join(random.choice('0123456789') for _ in range(16))


def calc_mac(xml_str: str, mac_key: str) -> str:
    h = SHA256.new()
    h.update((xml_str + mac_key).encode('utf-8'))
    return b64encode(h.digest()).decode('utf-8')


def send_transaction(ip: str, port: int, xml: str) -> str:
    try:
        with socket.create_connection((ip, port), timeout=5) as sock:
            sock.sendall(xml.encode('utf-8'))
            return sock.recv(8192).decode('utf-8', errors='replace')
    except Exception as e:
        return f"Error: {e}"


class AddLineForm(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.setWindowTitle("AddLineForm")
        self.resize(600, 300)

        # UI Grid
        layout = QGridLayout(self)
        layout.addWidget(QLabel("Currency:"), 0, 0)
        self.currency = QComboBox(); self.currency.addItems(["ILS", "USD"])
        layout.addWidget(self.currency, 0, 1)

        layout.addWidget(QLabel("Running Trans Amount:"), 1, 0)
        self.running_trans = QDoubleSpinBox(); self.running_trans.setDecimals(2); self.running_trans.setMaximum(1e9)
        layout.addWidget(self.running_trans, 1, 1)

        layout.addWidget(QLabel("Running Sub Total:"), 0, 2)
        self.running_sub = QDoubleSpinBox(); self.running_sub.setDecimals(2); self.running_sub.setMaximum(1e9)
        layout.addWidget(self.running_sub, 0, 3)

        layout.addWidget(QLabel("Running Tax Amount:"), 1, 2)
        self.running_tax = QDoubleSpinBox(); self.running_tax.setDecimals(2); self.running_tax.setMaximum(1e9)
        layout.addWidget(self.running_tax, 1, 3)

        layout.addWidget(QLabel("Number of lines in cache:"), 2, 0)
        self.lines_cache = QSpinBox(); self.lines_cache.setEnabled(False)
        layout.addWidget(self.lines_cache, 2, 1)
        self.empty_btn = QPushButton("Empty lines"); self.empty_btn.clicked.connect(self.clear_cache)
        layout.addWidget(self.empty_btn, 2, 3)

        # Action buttons
        btn_layout = QHBoxLayout()
        self.add_merch = QPushButton("Add a merchandise item"); self.add_merch.clicked.connect(self.add_merchandise)
        self.add_offer = QPushButton("Add an offer item"); self.add_offer.clicked.connect(self.add_offer_item)
        self.add_all = QPushButton("Add all lines items"); self.add_all.clicked.connect(self.add_all_items)
        self.replace_all = QPushButton("Replace all Lines"); self.replace_all.clicked.connect(self.replace_all_lines)
        for btn in (self.add_merch, self.add_offer, self.add_all, self.replace_all):
            btn_layout.addWidget(btn)
        layout.addLayout(btn_layout, 3, 0, 1, 4)

        self.lines = []  # cache of items

    def clear_cache(self):
        self.lines.clear()
        self.lines_cache.setValue(0)

    def add_merchandise(self):
        if not self.parent._load_mac_key() or not self.parent.session_id:
            QMessageBox.warning(self, "Warning", "Start a session first.")
            return
        item_id, ok1 = QInputDialog.getText(self, "Merchandise", "Line Item ID:")
        if not ok1: return
        price, ok2 = QInputDialog.getDouble(self, "Merchandise", "Unit Price:", decimals=2)
        if not ok2: return
        qty, ok3 = QInputDialog.getInt(self, "Merchandise", "Quantity:", min=1)
        if not ok3: return
        order, ok4 = QInputDialog.getInt(self, "Merchandise", "Order:", min=1)
        if not ok4: return
        entry = {
            'TYPE': 'ITEM',
            'LINE_ITEM_ID': item_id,
            'UNIT_PRICE': f"{price:.2f}",
            'QUANTITY': str(qty),
            'ORDER': str(order)
        }
        self.lines.append(entry)
        self.lines_cache.setValue(len(self.lines))

    def add_offer_item(self):
        if not self.parent._load_mac_key() or not self.parent.session_id:
            QMessageBox.warning(self, "Warning", "Start a session first.")
            return
        offer_id, ok1 = QInputDialog.getText(self, "Offer", "Offer ID:")
        if not ok1: return
        amount, ok2 = QInputDialog.getDouble(self, "Offer", "Offer Amount:", decimals=2)
        if not ok2: return
        entry = {
            'TYPE': 'OFFER',
            'LINE_ITEM_ID': offer_id,
            'UNIT_PRICE': f"{amount:.2f}",
            'QUANTITY': '1',
            'ORDER': '1'
        }
        self.lines.append(entry)
        self.lines_cache.setValue(len(self.lines))

    def add_all_items(self):
        if not self.lines:
            QMessageBox.information(self, "Info", "No lines to add.")
            return
        body = f"<RUNNING_TRANS_AMOUNT>{self.running_trans.value():.2f}</RUNNING_TRANS_AMOUNT>"
        for entry in self.lines:
            body += "<LINE_ITEMS><MERCHANDISE>"
            for k, v in entry.items():
                body += f"<{k}>{v}</{k}>"
            body += "</MERCHANDISE></LINE_ITEMS>"
        xml = self.parent._prepare_xml('ADD_LINE_ITEM', body)
        self.parent._send_and_log(xml)

    def replace_all_lines(self):
        if not self.lines:
            QMessageBox.information(self, "Info", "No lines to replace.")
            return
        body = ''
        for entry in self.lines:
            body += "<LINE_ITEMS><MERCHANDISE>"
            for k, v in entry.items():
                body += f"<{k}>{v}</{k}>"
            body += "</MERCHANDISE></LINE_ITEMS>"
        xml = self.parent._prepare_xml('REPLACE_ALL_LINE_ITEMS', body)
        self.parent._send_and_log(xml)


class SessionCallsApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400 Session Calls")
        self.resize(600, 500)

        # Connection inputs
        top_layout = QHBoxLayout()
        top_layout.addWidget(QLabel("IP:"))
        self.ip_input = QLineEdit("192.168.1.202")
        top_layout.addWidget(self.ip_input)
        top_layout.addWidget(QLabel("Port:"))
        self.port_input = QLineEdit("5015")
        top_layout.addWidget(self.port_input)

        # Buttons
        self.start_btn = QPushButton("Start Transaction")
        self.start_btn.clicked.connect(self.start_transaction)
        self.add_item_btn = QPushButton("Add Line Item")
        self.add_item_btn.clicked.connect(self.open_add_line_form)
        self.finalize_btn = QPushButton("Basket Finalized")
        self.finalize_btn.clicked.connect(self.basket_finalized)
        self.summary_btn = QPushButton("Basket Summary")
        self.summary_btn.clicked.connect(self.basket_summary)
        self.finish_btn = QPushButton("Finish Transaction")
        self.finish_btn.clicked.connect(self.finish_transaction)

        # Log view
        self.log = QTextEdit(); self.log.setReadOnly(True)

        # Main layout
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.addLayout(top_layout)
        for btn in (self.start_btn, self.add_item_btn,
                    self.finalize_btn, self.summary_btn,
                    self.finish_btn):
            main_layout.addWidget(btn)
        main_layout.addWidget(self.log)
        self.setCentralWidget(main_widget)

        # State
        self.session_id = ''
        self.mac_key = ''

    def _load_mac_key(self):
        if not self.mac_key:
            try:
                with open('mac.txt') as f:
                    self.mac_key = f.read().strip()
            except FileNotFoundError:
                QMessageBox.critical(self, "Error", "mac.txt not found. Exchange keys first.")
                return False
        return True

    def _prepare_xml(self, command, body):
        txn_time = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
        xml = (
            f"<TRANSACTION>"
            f"<FUNCTION_GROUP>SESSION</FUNCTION_GROUP>"
            f"<COMMAND>{command}</COMMAND>"
            f"<SESSION_ID>{self.session_id}</SESSION_ID>"
            f"<TRAINING_MODE>0</TRAINING_MODE>"
            f"<TRANSACTION_TIME>{txn_time}</TRANSACTION_TIME>"
            f"<MAC></MAC>"
            f"{body}"
            f"</TRANSACTION>"
        )
        mac = calc_mac(xml, self.mac_key)
        return xml.replace('<MAC></MAC>', f'<MAC>{mac}</MAC>')

    def _send_and_log(self, xml):
        ip = self.ip_input.text().strip()
        try:
            port = int(self.port_input.text().strip())
        except ValueError:
            QMessageBox.critical(self, "Error", "Invalid port.")
            return
        self.log.append(f"Sent:\n{xml}\n")
        response = send_transaction(ip, port, xml)
        self.log.append(f"Received:\n{response}\n")

    def start_transaction(self):
        if not self._load_mac_key(): return
        self.session_id = generate_session_id()
        invoice, ok = QInputDialog.getText(self, "Invoice", "Invoice (optional):")
        cashier, ok2 = QInputDialog.getText(self, "Cashier ID", "Cashier ID (optional):")
        body = ''
        if invoice:
            body += f"<INVOICE>{invoice[:6]}</INVOICE>"
        if cashier:
            body += f"<CASHIER_ID>{cashier}</CASHIER_ID>"
        body += f"<POS_IP>{self.ip_input.text()}</POS_IP>"
        body += f"<POS_PORT>{self.port_input.text()}</POS_PORT>"
        xml = self._prepare_xml('START_TRAN', body)
        self._send_and_log(xml)

    def open_add_line_form(self):
        if not self._load_mac_key() or not self.session_id:
            QMessageBox.warning(self, "Warning", "Start a session first.")
            return
        dlg = AddLineForm(self)
        dlg.exec_()

    def basket_finalized(self):
        if not self._load_mac_key() or not self.session_id:
            QMessageBox.warning(self, "Warning", "Start a session first.")
            return
        amount, ok = QInputDialog.getDouble(self, "Finalize Basket", "Final Amount:", decimals=2)
        if not ok: return
        xml = self._prepare_xml('BASKET_FINALIZED', f"<FINAL_AMOUNT>{amount:.2f}</FINAL_AMOUNT>")
        self._send_and_log(xml)

    def basket_summary(self):
        if not self._load_mac_key() or not self.session_id:
            QMessageBox.warning(self, "Warning", "Start a session first.")
            return
        count, ok = QInputDialog.getInt(self, "Basket Summary", "Items Count:")
        if not ok: return
        total, ok2 = QInputDialog.getDouble(self, "Basket Summary", "Total Amount:", decimals=2)
        if not ok2: return
        xml = self._prepare_xml('BASKET_SUMMARY', f"<ITEMS_COUNT>{count}</ITEM  TOTAL_AMOUNT>{total:.2f}</TOTAL_AMOUNT>")
        self._send_and_log(xml)

    def finish_transaction(self):
        if not self._load_mac_key() or not self.session_id:
            QMessageBox.warning(self, "Warning", "Start a session first.")
            return
        xml = self._prepare_xml('FINISH_TRANSACTION', '')
        self._send_and_log(xml)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    window = SessionCallsApp()
    window.show()
    sys.exit(app.exec_())