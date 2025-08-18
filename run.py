#!/usr/bin/env python3
"""
Entry point for the Verifone P400 application.

• Enforces single-instance with QLockFile
• Creates the full admin MainWindow (starts WebhookServer in the background)
• Shows the minimal WebhookListenerWindow (“Webhook: Active”) with a Settings button
"""

import os
import sys
import atexit
import tempfile

from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore    import Qt, QLockFile

# Import your existing full-featured admin UI
from gui.main_window import MainWindow
# Import the minimal listener window
from gui.webhook_listener import WebhookListenerWindow


# ───────────────────────────────────────────────────────────
# Single-instance guard (QLockFile)
# ───────────────────────────────────────────────────────────
APP_LOCK = None  # type: QLockFile

def _acquire_single_instance_lock() -> bool:
    """
    Try to acquire a process-wide lock. Returns True if this process owns the lock.
    If another instance holds it, returns False.
    """
    global APP_LOCK
    lock_path = os.path.join(tempfile.gettempdir(), "verifone_p400_single.lock")
    APP_LOCK = QLockFile(lock_path)

    # First attempt
    if APP_LOCK.tryLock(0):
        return True

    # If the previous instance crashed, remove a stale lock and retry once.
    try:
        APP_LOCK.removeStaleLockFile()
    except Exception:
        pass

    return APP_LOCK.tryLock(0)


def _release_single_instance_lock():
    global APP_LOCK
    if APP_LOCK is not None and APP_LOCK.isLocked():
        APP_LOCK.unlock()
    APP_LOCK = None


if __name__ == "__main__":
    # High-DPI scaling for modern displays
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)

    # Create the Qt application (needed to show the info box on Windows)
    app = QApplication(sys.argv)

    if not _acquire_single_instance_lock():
        # Another instance is already running — inform the user and exit quietly
        QMessageBox.information(
            None,
            "Already Running",
            "האפליקציה כבר פועלת.\n"
            "ניתן לפתוח את החלון דרך הסמל במגש המערכת (System Tray)."
        )
        sys.exit(0)

    # Ensure the lock is released on clean exit
    atexit.register(_release_single_instance_lock)

    # 1) Instantiate (but do not show) the full admin window.
    #    Its __init__ loads settings and starts the WebhookServer.
    admin_win = MainWindow()

    # 2) Instantiate and show the minimal listener window.
    listener = WebhookListenerWindow(admin_win)
    listener.show()

    # 3) Run the event loop
    rc = app.exec_()

    # Explicitly release the lock before exit (also covered by atexit)
    _release_single_instance_lock()
    sys.exit(rc)
