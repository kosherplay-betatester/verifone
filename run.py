#!/usr/bin/env python3
"""
Entry point for the Verifone P400 application.

1) Creates the full admin MainWindow (which starts its WebhookServer in the background).
2) Creates and shows a minimal WebhookListenerWindow that:
   - Displays “Webhook: Active”
   - Provides a “Settings” button to open the admin UI.
"""

import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore    import Qt

# Import your existing full-featured admin UI
from gui.main_window import MainWindow
# Import the new minimal listener window
from gui.webhook_listener import WebhookListenerWindow

if __name__ == "__main__":
    # Enable high-DPI scaling for modern displays
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)

    # Create the Qt application
    app = QApplication(sys.argv)

    # 1) Instantiate (but do not show) the full admin window.
    #    Its __init__ loads settings and starts the WebhookServer.
    admin_win = MainWindow()

    # 2) Instantiate and show the minimal listener window.
    listener = WebhookListenerWindow(admin_win)
    listener.show()

    # 3) Run the event loop
    sys.exit(app.exec_())
