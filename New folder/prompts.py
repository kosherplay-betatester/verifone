#!/usr/bin/env python3
# prompts.py
"""
Transparent always-on-top overlay for short Hebrew status messages.

Changes (v2.0)
--------------
• New visual design – white text on a pleasant blue frame:
      · background  #3498db
      · 4-pixel darker outline #2980b9
• API unchanged:   show_prompt(text, duration_ms=None)   |   hide_prompt()
• Legacy object `prompt.show()` / `prompt.hide()` still works.
"""

from __future__ import annotations

import sys
from typing import Optional

from PyQt5.QtCore    import Qt, QTimer, QPoint
from PyQt5.QtWidgets import QApplication, QWidget, QLabel


# ───────────────────────────────────────────────────────────
# Internal overlay widget (singleton)
# ───────────────────────────────────────────────────────────
class _Overlay(QWidget):
    """Frameless centred overlay with rounded blue frame & white text."""

    def __init__(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        super().__init__(parent=None, flags=flags)

        # Transparent window & click-through
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # Single centred label
        self._lbl = QLabel("", self, alignment=Qt.AlignCenter)
        self._lbl.setStyleSheet(
            """
            QLabel {
                background: #3498db;          /* blue fill              */
                border: 4px solid #2980b9;    /* darker blue outline    */
                color: #ffffff;               /* white text             */
                font-family: "Segoe UI";
                font-size: 24pt;
                font-weight: 600;
                padding: 18px 32px;
                border-radius: 12px;
            }
            """
        )

        # Auto-hide timer (single-shot)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.hide)

    # ---------- public --------------------------------------------------
    def show_message(self, text: str, ms: int | None = None) -> None:
        """Display *text*; auto-hide after *ms* (if provided)."""
        self._timer.stop()

        self._lbl.setText(text)
        self._lbl.adjustSize()
        self.resize(self._lbl.size())
        self._centre()
        super().show()

        if ms:
            self._timer.start(int(ms))

    # ---------- helpers -------------------------------------------------
    def _centre(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(QPoint(
            geo.center().x() - self.width()  // 2,
            geo.center().y() - self.height() // 2,
        ))


# ───────────────────────────────────────────────────────────
# Singleton management
# ───────────────────────────────────────────────────────────
_overlay: Optional[_Overlay] = None


def _instance() -> _Overlay:
    global _overlay
    if _overlay is None:
        _overlay = _Overlay()
    return _overlay


# ───────────────────────────────────────────────────────────
# Public helper functions
# ───────────────────────────────────────────────────────────
def show_prompt(text: str, duration_ms: int | None = None) -> None:
    """Show *text* overlay; hide after *duration_ms* (if supplied)."""
    _instance().show_message(text, duration_ms)


def hide_prompt() -> None:
    """Hide the overlay immediately."""
    if _overlay is not None:
        _overlay.hide()


# ───────────────────────────────────────────────────────────
# Legacy compatibility  (object with .show / .hide)
# ───────────────────────────────────────────────────────────
class _PromptCompat:
    @staticmethod
    def show(text: str, duration_ms: int | None = None) -> None:
        show_prompt(text, duration_ms)

    @staticmethod
    def hide() -> None:
        hide_prompt()


prompt = _PromptCompat()   # what legacy imports expect

__all__ = ["show_prompt", "hide_prompt", "prompt"]


# ───────────────────────────────────────────────────────────
# Demo (run `python prompts.py` for a quick visual test)
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":  # pragma: no cover
    app = QApplication(sys.argv)
    show_prompt("מתחיל עסקה", 2500)
    QTimer.singleShot(3000, lambda: show_prompt("תהליך התשלום הסתיים", 3000))
    sys.exit(app.exec_())
