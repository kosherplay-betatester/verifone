#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# prompts.py  –  FULL FILE  (v2.3 • spinner persists until hide_spinner)

"""
Blue overlay for Hebrew status messages + round spinner.

Rules
-----
• show_spinner(text=None)   → מציג גלגל עגול + טקסט (ברירת־מחדל “מעבד תשלום…”)
• show_prompt(text, ms=None)
      · משנה טקסט בלבד
      · **אם הספינר מוצג – מתעלמת מ-ms**  ➜ הספינר והטקסט נשארים עד hide_spinner()
      · אם אין ספינר – ms משמש כ-auto-hide.
• hide_spinner()            → מכבה ספינר; אם המסך הוצג עם ms קודם לכן,
                              מפעיל מחדש את טיימר ההסתרה (4 s ברירת־מחדל).
• hide_prompt()             → מסתיר לחלוטין את האובייקט.
• prompt.show / prompt.hide → תאימות לאחור (show = show_prompt, hide = hide_prompt)
"""

from __future__ import annotations
import sys
from typing import Optional

from PyQt5.QtCore    import Qt, QTimer, QPoint, QLineF
from PyQt5.QtGui     import QColor, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout


# ───────────────────────────────────────────────────────────
# Round spinner widget
# ───────────────────────────────────────────────────────────
class _Wheel(QWidget):
    """12-segment white wheel, rotates with QTimer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(48, 48)
        self._angle = 0
        self._timer = QTimer(self, timeout=self._tick)
        self._timer.start(80)            # ~12.5 FPS

    # —— helpers ——
    def _tick(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def start(self):
        if not self._timer.isActive():
            self._timer.start(80)

    def stop(self):
        self._timer.stop()

    # —— paint ——
    def paintEvent(self, _evt):          # noqa: D401
        r_in, r_out = 13, 23             # inner / outer radius
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(self._angle)

        for i in range(12):
            alpha = int(255 * (i + 1) / 12)
            painter.setPen(QPen(QColor(255, 255, 255, alpha), 3,
                                Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QLineF(r_in, 0, r_out, 0))
            painter.rotate(30)

        painter.end()


# ───────────────────────────────────────────────────────────
# Overlay container (singleton)
# ───────────────────────────────────────────────────────────
class _Overlay(QWidget):
    """Frameless centred overlay with blue frame, text & optional spinner."""

    _FALLBACK_HIDE_MS = 4000

    def __init__(self) -> None:
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        super().__init__(parent=None, flags=flags)

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # --- layout ---
        lay = QVBoxLayout(self); lay.setSpacing(14); lay.setContentsMargins(0, 0, 0, 0)

        self._lbl = QLabel("", self, alignment=Qt.AlignCenter)
        self._lbl.setStyleSheet(
            """
            QLabel {
                background: #3498db;
                border: 4px solid #2980b9;
                color: #ffffff;
                font-family: "Segoe UI";
                font-size: 24pt;
                font-weight: 600;
                padding: 20px 36px;
                border-radius: 12px;
            }
            """
        )
        lay.addWidget(self._lbl, alignment=Qt.AlignCenter)

        self._spinner = _Wheel(self); self._spinner.setVisible(False)
        lay.addWidget(self._spinner, alignment=Qt.AlignCenter)

        # auto-hide timer (only active when spinner hidden)
        self._timer = QTimer(self, singleShot=True)
        self._timer.timeout.connect(self.hide)

    # ---------- public --------------------------------------------------
    def show_message(self, text: str, ms: int | None):
        """Update label, keep spinner state."""
        self._timer.stop()
        self._lbl.setText(text)
        self._reflow()
        super().show()

        # Only start timer if spinner **not** visible
        if ms and not self._spinner.isVisible():
            self._timer.start(int(ms))

    def show_spinner(self, text: str | None):
        """Show spinner + optional text; never starts auto-hide timer."""
        self._timer.stop()
        if text is not None:
            self._lbl.setText(text)
        elif not self._lbl.text():
            self._lbl.setText("מעבד תשלום…")
        self._spinner.setVisible(True)
        self._spinner.start()
        self._reflow()
        super().show()

    def hide_spinner(self):
        """Stop spinner and allow overlay to auto-hide shortly."""
        if not self._spinner.isVisible():
            return
        self._spinner.setVisible(False)
        self._spinner.stop()
        self._reflow()
        # start fallback timer so success/failed text disappears on its own
        self._timer.start(self._FALLBACK_HIDE_MS)

    # ---------- helpers -------------------------------------------------
    def _reflow(self):
        self.layout().activate()
        self.resize(self.sizeHint())
        self._centre()

    def _centre(self):
        scr = self.screen() or QApplication.primaryScreen()
        geo = scr.availableGeometry()
        self.move(QPoint(
            geo.center().x() - self.width()  // 2,
            geo.center().y() - self.height() // 2,
        ))


# ───────────────────────────────────────────────────────────
# Singleton access
# ───────────────────────────────────────────────────────────
_overlay: Optional[_Overlay] = None
def _inst() -> _Overlay:
    global _overlay
    if _overlay is None:
        _overlay = _Overlay()
    return _overlay


# ───────────────────────────────────────────────────────────
# Public façade
# ───────────────────────────────────────────────────────────
def show_prompt(text: str, duration_ms: int | None = None):
    _inst().show_message(text, duration_ms)

def show_spinner(text: str | None = None):
    _inst().show_spinner(text)

def hide_spinner():
    _inst().hide_spinner()

def hide_prompt():
    if _overlay is not None:
        _overlay.hide()


# ───────────────────────────────────────────────────────────
# Legacy compatibility
# ───────────────────────────────────────────────────────────
class _PromptCompat:
    @staticmethod
    def show(text: str, duration_ms: int | None = None):
        show_prompt(text, duration_ms)

    @staticmethod
    def hide():
        hide_prompt()

prompt = _PromptCompat()
__all__ = ["show_prompt", "hide_prompt",
           "show_spinner", "hide_spinner", "prompt"]


# ───────────────────────────────────────────────────────────
# Demo (python prompts.py)
# ───────────────────────────────────────────────────────────
if __name__ == "__main__":      # pragma: no cover
    app = QApplication(sys.argv)
    show_spinner()                              # spinner appears
    QTimer.singleShot(3000, lambda: show_prompt("ממתין לאישור"))
    QTimer.singleShot(6000, lambda: show_prompt("בוצע!", None) or hide_spinner())
    sys.exit(app.exec_())
