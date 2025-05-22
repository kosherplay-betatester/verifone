#!/usr/bin/env python3
"""
A minimal GUI window that:

- Indicates the local webhook server is running (“Webhook: Active”).
- Offers a “Settings” button to open the full admin UI (MainWindow).
"""

from PyQt5.QtWidgets import QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout
from PyQt5.QtCore    import Qt

class WebhookListenerWindow(QMainWindow):
    def __init__(self, admin_window):
        """
        :param admin_window: an instance of gui.main_window.MainWindow
                             (already initialized and running its WebhookServer).
        """
        super().__init__()
        self.admin_window = admin_window

        # Window configuration
        self.setWindowTitle("Verifone P400 – Webhook Listener")
        self.resize(400, 150)

        # Central widget & layout
        container = QWidget()
        layout    = QVBoxLayout(container)

        # Status label
        status = QLabel("Webhook: Active", alignment=Qt.AlignCenter)
        layout.addWidget(status)

        # Settings button
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.open_settings)
        layout.addWidget(settings_btn)

        self.setCentralWidget(container)

    def open_settings(self):
        """
        Show (or bring to front) the full admin settings & test window.
        """
        self.admin_window.show()
        self.admin_window.raise_()
        self.admin_window.activateWindow()
