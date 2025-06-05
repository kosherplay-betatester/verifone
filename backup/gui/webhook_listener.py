#!/usr/bin/env python3
"""
gui/webhook_listener.py
-----------------------

• Shows “Webhook: Active”.
• Hides on minimise / close; never appears on the task-bar (Qt.Tool).
• Lives in the system tray with menu:  Open window | Settings | Exit.
• Opening either window **or exiting the app** prompts for the
  default password:  kosherplay2015
"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout,
    QSystemTrayIcon, QMenu, QAction, QApplication, QStyle,
    QInputDialog, QLineEdit, QMessageBox
)
from PyQt5.QtCore import Qt, QEvent, QTimer
from PyQt5.QtGui  import QIcon


DEFAULT_PASSWORD = "kosherplay2015"


class WebhookListenerWindow(QMainWindow):
    """Foreground window + background tray icon with password gating."""

    # ───────────────────────────────────────────────────────
    # Initialisation
    # ───────────────────────────────────────────────────────
    def __init__(self, admin_window):
        """
        Parameters
        ----------
        admin_window : gui.main_window.MainWindow
            The full admin UI (already initialised and running its WebhookServer).
        """
        super().__init__()
        self.admin_window = admin_window
        self._password    = DEFAULT_PASSWORD

        # Window (off task-bar)
        self.setWindowFlags(self.windowFlags() | Qt.Tool)
        self.setWindowTitle("Verifone P400 – Webhook Listener")
        self.resize(400, 150)

        container = QWidget(); layout = QVBoxLayout(container)
        layout.addWidget(QLabel("Webhook: Active", alignment=Qt.AlignCenter))

        btn = QPushButton("Settings")
        btn.clicked.connect(self._open_settings_secured)
        layout.addWidget(btn)

        self.setCentralWidget(container)

        # Keep app alive when last window closes
        QApplication.instance().setQuitOnLastWindowClosed(False)

        # System-tray icon
        icon = QIcon.fromTheme("network-server")
        if icon.isNull():                                    # Windows fallback
            icon = self.style().standardIcon(QStyle.SP_ComputerIcon)

        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("Verifone P400 – Webhook Listener")

        menu = QMenu()
        menu.addAction(QAction("Open window", self, triggered=self._restore_secured))
        menu.addAction(QAction("Settings",    self, triggered=self._open_settings_secured))
        menu.addSeparator()
        menu.addAction(QAction("Exit",        self, triggered=self._quit_secured))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    # ───────────────────────────────────────────────────────
    # Password-protected wrappers
    # ───────────────────────────────────────────────────────
    def _restore_secured(self):
        if self._auth():
            self._restore()

    def _open_settings_secured(self):
        if self._auth():
            self._open_settings()

    def _quit_secured(self):
        if self._auth():
            self._quit()

    # ───────────────────────────────────────────────────────
    # Authentication dialog
    # ───────────────────────────────────────────────────────
    def _auth(self) -> bool:
        pw, ok = QInputDialog.getText(
            self, "Authentication", "Enter password:",
            QLineEdit.Password
        )
        if not ok:
            return False
        if pw == self._password:
            return True
        QMessageBox.warning(self, "Authentication", "Incorrect password.")
        return False

    # ───────────────────────────────────────────────────────
    # Helpers (no password check)
    # ───────────────────────────────────────────────────────
    def _open_settings(self):
        self.admin_window.show(); self.admin_window.raise_(); self.admin_window.activateWindow()

    def _restore(self):
        self.show(); self.raise_(); self.activateWindow()

    def _quit(self):
        self.tray.hide()
        QApplication.quit()

    # ───────────────────────────────────────────────────────
    # Window events
    # ───────────────────────────────────────────────────────
    def changeEvent(self, event: QEvent):
        if event.type() == QEvent.WindowStateChange and self.isMinimized():
            QTimer.singleShot(0, self.hide)
        super().changeEvent(event)

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Webhook Listener",
            "Running in background — double-click tray icon to reopen.",
            QSystemTrayIcon.Information, 3000
        )

    # ───────────────────────────────────────────────────────
    # Tray icon callbacks
    # ───────────────────────────────────────────────────────
    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:   # left-click / double-click
            self._restore_secured()
