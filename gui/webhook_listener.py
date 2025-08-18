#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui/webhook_listener.py
-----------------------

• Shows “Webhook: Active”.
• Hides on minimise / close; never appears on the task-bar (Qt.Tool).
• Lives in the system tray with menu:  Open window | Settings | Reload icon | Exit.
• Opening either window **or exiting the app** prompts for the
  default password:  kosherplay2015
• NEW: Loads a multi-resolution tray/window icon from separate PNG files
       located in this module's folder (…/gui):
           tray_16.png, tray_24.png, tray_32.png, tray_48.png,
           tray_64.png, tray_128.png, tray_256.png
"""

import os
import sys

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QLabel, QPushButton, QVBoxLayout,
    QSystemTrayIcon, QMenu, QAction, QApplication, QStyle,
    QInputDialog, QLineEdit, QMessageBox
)
from PyQt5.QtCore import Qt, QEvent, QTimer, QSize
from PyQt5.QtGui  import QIcon


DEFAULT_PASSWORD = "kosherplay2015"


# ───────────────────────────────────────────────────────────
# Icon helpers – load per-size PNGs from the gui folder
# ───────────────────────────────────────────────────────────
def _gui_dir() -> str:
    """Absolute path to the gui/ directory (this file's folder)."""
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except Exception:
        # Fallback: current working directory
        return os.getcwd()


def _fallback_icon() -> QIcon:
    """Return a reasonable fallback platform icon."""
    ico = QIcon.fromTheme("network-server")
    if not ico.isNull():
        return ico
    return QApplication.style().standardIcon(QStyle.SP_ComputerIcon)


def _load_app_icon() -> QIcon:
    """
    Build a QIcon from per-size PNGs in gui/ named: tray_<size>.png
    (any subset is fine). Includes common steps for HiDPI.
    """
    sizes = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]
    base  = _gui_dir()

    icon = QIcon()
    added = False
    for sz in sizes:
        path = os.path.join(base, f"tray_{sz}.png")
        if os.path.exists(path):
            icon.addFile(path, QSize(sz, sz))
            added = True

    if added:
        return icon
    return _fallback_icon()


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

        # System-tray + window icon from gui/tray_<size>.png files
        icon = _load_app_icon()
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setIcon(icon)
        self.tray.setToolTip(self._make_tooltip())

        # Tray menu
        menu = QMenu()
        menu.addAction(QAction("Open window", self, triggered=self._restore_secured))
        menu.addAction(QAction("Settings",    self, triggered=self._open_settings_secured))
        menu.addSeparator()
        menu.addAction(QAction("Reload icon", self, triggered=self._reload_icon))
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

    def _make_tooltip(self) -> str:
        """Build a useful tray tooltip (includes webhook host:port when available)."""
        try:
            host = getattr(self.admin_window.webhook, "host", "localhost")
            port = getattr(self.admin_window.webhook, "port", 8080)
            return f"Verifone P400 – Webhook Listener\nWebhook: http://{host}:{port}"
        except Exception:
            return "Verifone P400 – Webhook Listener"

    def _reload_icon(self):
        """Reload icon from the gui/ folder (after replacing tray_*.png files)."""
        icon = _load_app_icon()
        self.tray.setIcon(icon)
        self.setWindowIcon(icon)
        self.tray.setToolTip(self._make_tooltip())

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
