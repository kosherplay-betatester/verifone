import sys
import socket
import re
import random
import json
from pathlib import Path
from datetime import datetime, timezone
from base64 import b64decode, b64encode
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QComboBox,
    QPushButton, QTextEdit, QHBoxLayout, QVBoxLayout, QSplitter, QMessageBox,
    QGridLayout, QGroupBox, QSpinBox, QDoubleSpinBox, QCheckBox
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from Crypto.Cipher import DES3
from Crypto.Hash import SHA256

# Config
CONFIG_PATH = Path("config.json")

def load_config():
    defaults = {
        "ip": "192.168.1.202",
        "port": "5015",
        "chain_id": "39999",
        "store_id": "0123",
        "lane_id": "074",
        "alt_term_id": "012345678",
        "pos_type": "ATTENDED",
        "pos_types": ["ATTENDED","UNATTENDED"],
        "version_1x": False
    }
    if CONFIG_PATH.exists():
        try: defaults.update(json.loads(CONFIG_PATH.read_text()))
        except: pass
    else: CONFIG_PATH.write_text(json.dumps(defaults,indent=4))
    return defaults

config = load_config()

def save_config(data):
    try:
        CONFIG_PATH.write_text(json.dumps(data,indent=4))
        QMessageBox.information(None,"Saved","Config saved to config.json")
    except Exception as e:
        QMessageBox.critical(None,"Error",str(e))

# Crypto helpers

def generate_session_id():
    return ''.join(random.choice('0123456789') for _ in range(16))

def des3_decrypt(ktk,mac_b64):
    data=b64decode(mac_b64)
    kb=ktk.encode()
    if len(kb)!=16: raise ValueError("KTK must be 16 bytes")
    k24=kb+kb[:8]
    k24=DES3.adjust_key_parity(k24)
    cipher=DES3.new(k24,DES3.MODE_ECB)
    dec=cipher.decrypt(data)
    return dec.rstrip(b"\x00").decode(errors='ignore')

def calc_mac(xml,mackey):
    h=SHA256.new()
    h.update((xml+mackey).encode())
    return b64encode(h.digest()).decode()

# Thread
class CommandThread(QThread):
    result=pyqtSignal(str,str)
    def __init__(self,ip,port,msg):
        super().__init__();self.ip=ip;self.port=port;self.msg=msg
    def run(self):
        try:
            s=socket.socket();s.settimeout(5);s.connect((self.ip,self.port));s.send(self.msg.encode())
            recv=b''
            while True:
                try:chunk=s.recv(4096)
                    if not chunk:break
                    recv+=chunk
                except socket.timeout:break
            s.close();out=recv.decode(errors='replace')
        except Exception as e:out=f"Error: {e}"
        self.result.emit(self.msg,out)

# GUI
class VerifoneApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Verifone P400")
        self.showMaximized()
        self.cfg=config.copy()
        self.session_id='';self.ktk='';self.mac_key=''
        self.threads=[]
        # layout
        left=QWidget();lv=QVBoxLayout(left)
        # conn
        cl=QHBoxLayout();
        self.ver1=QCheckBox("1.x")
        self.ver1.setChecked(self.cfg['version_1x']);cl.addWidget(self.ver1)
        cl.addWidget(QLabel("IP:"));self.ip=QLineEdit(self.cfg['ip']);cl.addWidget(self.ip)
        cl.addWidget(QLabel("Port:"));self.port=QLineEdit(self.cfg['port']);cl.addWidget(self.port)
        cl.addStretch();lv.addLayout(cl)
        # reg terminal
        rg=QGroupBox("Register Terminal");rl=QGridLayout()
        self.chain=QLineEdit(self.cfg['chain_id']);self.store=QLineEdit(self.cfg['store_id'])
        self.lane=QLineEdit(self.cfg['lane_id']);self.alt=QLineEdit(self.cfg['alt_term_id'])
        self.ptype=QComboBox();self.ptype.addItems(self.cfg['pos_types']);
        idx=self.ptype.findText(self.cfg['pos_type']);
        self.ptype.setCurrentIndex(idx)
        rl.addWidget(QLabel("Chain"),0,0);rl.addWidget(self.chain,0,1)
        rl.addWidget(QLabel("Store"),1,0);rl.addWidget(self.store,1,1)
        rl.addWidget(QLabel("Lane"),2,0);rl.addWidget(self.lane,2,1)
        rl.addWidget(QLabel("Alt ID"),3,0);rl.addWidget(self.alt,3,1)
        rl.addWidget(QLabel("POS Type"),4,0);rl.addWidget(self.ptype,4,1)
        self.btn_reg_term=QPushButton("Register Terminal");self.btn_reg_term.clicked.connect(self.reg_term)
        rl.addWidget(self.btn_reg_term,5,0,1,2)
        rg.setLayout(rl);lv.addWidget(rg)
        # exchange keys
        ex=QHBoxLayout();
        ex.addWidget(QLabel("KTK:"));self.ktk_in=QLineEdit();self.ktk_in.setEnabled(False);ex.addWidget(self.ktk_in)
        self.btn_ex=QPushButton("Exchange Keys");self.btn_ex.setEnabled(False);self.btn_ex.clicked.connect(self.ex_key);ex.addWidget(self.btn_ex)
        ex.addStretch();lv.addLayout(ex)
        # mac display
        ml=QHBoxLayout();ml.addWidget(QLabel("MAC Key:"));
        self.mac_out=QLineEdit();self.mac_out.setReadOnly(True);ml.addWidget(self.mac_out);ml.addStretch();lv.addLayout(ml)
        # register device (uses mac)
        self.btn_reg_dev=QPushButton("Register Device");self.btn_reg_dev.setEnabled(False);
        self.btn_reg_dev.clicked.connect(self.reg_dev);lv.addWidget(self.btn_reg_dev)
        # status
        self.btn_status=QPushButton("Status");self.btn_status.setEnabled(False);
        self.btn_status.clicked.connect(self.status);lv.addWidget(self.btn_status)
        # logs
        self.sent=QTextEdit();self.sent.setReadOnly(True)
        self.recv=QTextEdit();self.recv.setReadOnly(True)
        logs=QSplitter(Qt.Vertical);logs.addWidget(QLabel("Sent"));logs.addWidget(self.sent);
        logs.addWidget(QLabel("Recv"));logs.addWidget(self.recv)
        # main splitter
        ms=QSplitter(Qt.Horizontal);ms.addWidget(left);ms.addWidget(logs)
        ms.setStretchFactor(1,2)
        self.setCentralWidget(ms)
    # send helper
    def send(self,xml,need_mac=False):
        if need_mac:
            xml_no=xml.replace("</MAC>","")
            m=calc_mac(xml_no,self.mac_key)
            xml=xml.replace("<MAC></MAC>",f"<MAC>{m}</MAC>")
        try: p=int(self.port.text())
        except: QMessageBox.critical(self,"Port","Invalid");return
        t=CommandThread(self.ip.text(),p,xml)
        t.result.connect(self.handle)
        t.finished.connect(lambda:self.threads.remove(t))
        self.threads.append(t);t.start()
    def handle(self,sent,recv):
        self.sent.append(sent);self.recv.append(recv)
        if "<COMMAND>REGISTER</COMMAND>" in sent and "<EVENT>COMPLETED" in recv:
            self.ktk_in.setEnabled(True);self.btn_ex.setEnabled(True)
        if "<COMMAND>EXCHANGE_KEYS" in sent and "<MAC_KEY>" in recv:
            k=re.search(r"<MAC_KEY>([^<]+)",recv).group(1)
            self.mac_key=des3_decrypt(self.ktk_in.text(),k)
            self.mac_out.setText(self.mac_key)
            # enable device reg/status
            self.btn_reg_dev.setEnabled(True);self.btn_status.setEnabled(True)
    # actions
    def reg_term(self):
        self.session_id=generate_session_id()
        xml=(f"<TRANSACTION><FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
             f"<COMMAND>REGISTER</COMMAND><SESSION_ID>{self.session_id}</SESSION_ID>"
             f"<TRAINING_MODE>0</TRAINING_MODE><CHAIN>{self.chain.text()}</CHAIN>"
             f"<STORE>{self.store.text()}</STORE><LANE>{self.lane.text()}</LANE>"
             f"<TERMINAL_ID>{self.alt.text()}</TERMINAL_ID>"
             f"<POS_TYPE>{self.ptype.currentText()}</POS_TYPE></TRANSACTION>")
        self.send(xml)
    def ex_key(self):
        xml=(f"<TRANSACTION><FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
             f"<COMMAND>EXCHANGE_KEYS</COMMAND><SESSION_ID>{self.session_id}</SESSION_ID>"
             f"<TRAINING_MODE>0</TRAINING_MODE><KTK>{self.ktk_in.text()}</KTK></TRANSACTION>")
        self.send(xml)
    def reg_dev(self):
        xml=(f"<TRANSACTION><FUNCTION_GROUP>DEVICE</FUNCTION_GROUP>"
             f"<COMMAND>REGISTER</COMMAND><SESSION_ID>{self.session_id}</SESSION_ID>"
             f"<TRAINING_MODE>0</TRAINING_MODE><MAC></MAC></TRANSACTION>")
        self.send(xml,need_mac=True)
    def status(self):
        tm=datetime.now(timezone.utc).strftime("%m.%d.%Y %H:%M:%S UTC")
        xml=(f"<TRANSACTION><FUNCTION_GROUP>ADMIN</FUNCTION_GROUP>"
             f"<COMMAND>STATUS</COMMAND><SESSION_ID>{self.session_id}</SESSION_ID>"
             f"<TRAINING_MODE>0</TRAINING_MODE><TRANSACTION_TIME>{tm}</TRANSACTION_TIME><MAC></MAC></TRANSACTION>")
        self.send(xml,need_mac=True)
    def clear_logs(self): self.sent.clear();self.recv.clear()
    def closeEvent(self,e):
        for t in self.threads: t.quit();t.wait()
        e.accept()

if __name__=="__main__":
    app=QApplication(sys.argv)
    w=VerifoneApp();sys.exit(app.exec_())
