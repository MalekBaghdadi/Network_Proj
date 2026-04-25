# Author: Nakhoul Nehra
# Description: PyQt control panel for the SecureWatch Proxy.
#              Provides a simple admin interface for starting/stopping the proxy,
#              viewing logs, editing rules, testing requests, and checking cache stats.

import os
import sys
import json
import time
import threading
import urllib.request
import urllib.error

from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPainter, QPen, QBrush
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QPushButton,
    QTextEdit,
    QPlainTextEdit,
    QLineEdit,
    QComboBox,
    QListWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QStackedWidget,
    QMessageBox,
    QFrame,
    QProgressBar,
    QSizePolicy,
    QStatusBar,
)

import proxy
import cache
from logger import LOG_FILE


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RULES_FILE = os.path.join(BASE_DIR, "rules.json")
PROXY_URL = "http://127.0.0.1:8888"


PRESET_SITES = [
    {
        "name": "CERN - First Website",
        "url": "http://info.cern.ch/hypertext/WWW/TheProject.html",
        "domain": "info.cern.ch",
    },
    {
        "name": "Kurose & Ross Networking Site",
        "url": "http://gaia.cs.umass.edu/kurose_ross/",
        "domain": "gaia.cs.umass.edu",
    },
    {
        "name": "NASA",
        "url": "https://www.nasa.gov/",
        "domain": "www.nasa.gov",
    },
    {
        "name": "IETF",
        "url": "https://www.ietf.org/",
        "domain": "www.ietf.org",
    },
    {
        "name": "RFC Editor",
        "url": "https://www.rfc-editor.org/",
        "domain": "www.rfc-editor.org",
    },
    {
        "name": "ICANN",
        "url": "https://www.icann.org/",
        "domain": "www.icann.org",
    },
    {
        "name": "NeverSSL - Plain HTTP Testing",
        "url": "http://neverssl.com/",
        "domain": "neverssl.com",
    },
    {
        "name": "HTTPBin GET Test",
        "url": "http://httpbin.org/get",
        "domain": "httpbin.org",
    },
    {
        "name": "HTTPBin POST Test",
        "url": "http://httpbin.org/post",
        "domain": "httpbin.org",
    },
    {
        "name": "HTTPBin 403 Test",
        "url": "http://httpbin.org/status/403",
        "domain": "httpbin.org",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
#  Fun widget 1: animated pulsing status dot
# ─────────────────────────────────────────────────────────────────────────────
class PulseDot(QWidget):
    """
    Small circle that smoothly pulses between two greens when running,
    and stays dim gray when stopped.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._running = False
        self._alpha = 255
        self._direction = -5

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)

    def set_running(self, running):
        if running == self._running:
            return
        self._running = running
        if running:
            self._timer.start(25)
        else:
            self._timer.stop()
            self._alpha = 120
            self.update()

    def _tick(self):
        self._alpha += self._direction
        if self._alpha <= 70:
            self._alpha = 70
            self._direction = 5
        elif self._alpha >= 255:
            self._alpha = 255
            self._direction = -5
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._running:
            # outer glow ring
            glow = QColor(22, 163, 74, max(0, self._alpha - 140))
            p.setBrush(QBrush(glow))
            p.setPen(Qt.NoPen)
            p.drawEllipse(0, 0, 14, 14)
            # solid core
            core = QColor(22, 163, 74, self._alpha)
            p.setBrush(QBrush(core))
            p.drawEllipse(2, 2, 10, 10)
        else:
            color = QColor(148, 163, 184, 180)
            p.setBrush(QBrush(color))
            p.setPen(Qt.NoPen)
            p.drawEllipse(2, 2, 10, 10)


# ─────────────────────────────────────────────────────────────────────────────
#  Fun widget 2: live uptime counter
# ─────────────────────────────────────────────────────────────────────────────
class UptimeCounter(QLabel):
    """Counts elapsed time since the proxy was started. Ticks every second."""
    def __init__(self, parent=None):
        super().__init__("—", parent)
        self.setObjectName("CardValue")
        self._start_time = None

        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

    def start(self):
        self._start_time = time.time()

    def stop(self):
        self._start_time = None
        self.setText("—")

    def _tick(self):
        if self._start_time is None:
            return
        elapsed = int(time.time() - self._start_time)
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        self.setText(f"{h:02d}h {m:02d}m {s:02d}s")


# ─────────────────────────────────────────────────────────────────────────────
#  Fun widget 3: live clock in the sidebar
# ─────────────────────────────────────────────────────────────────────────────
class LiveClock(QLabel):
    """Displays a live HH:MM:SS clock, ticking every second."""
    def __init__(self, parent=None):
        import datetime
        super().__init__(parent)
        self.setObjectName("LiveClock")
        self.setAlignment(Qt.AlignCenter)
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def _tick(self):
        import datetime
        self.setText(datetime.datetime.now().strftime("%H:%M:%S"))


# ─────────────────────────────────────────────────────────────────────────────
#  Stat card
# ─────────────────────────────────────────────────────────────────────────────
class Card(QFrame):
    def __init__(self, title, value="—"):
        super().__init__()
        self.setObjectName("Card")

        self.title_label = QLabel(title)
        self.title_label.setObjectName("CardTitle")

        self.value_label = QLabel(value)
        self.value_label.setObjectName("CardValue")

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(6)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        self.setLayout(layout)

    def set_value(self, value):
        self.value_label.setText(str(value))


# ─────────────────────────────────────────────────────────────────────────────
#  Status card — embeds PulseDot
# ─────────────────────────────────────────────────────────────────────────────
class StatusCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Card")

        title = QLabel("PROXY STATUS")
        title.setObjectName("CardTitle")

        dot_row = QHBoxLayout()
        dot_row.setSpacing(9)
        dot_row.setContentsMargins(0, 0, 0, 0)

        self.dot = PulseDot()
        self.value_label = QLabel("Stopped")
        self.value_label.setObjectName("CardValue")
        self.value_label.setStyleSheet(
            "font-family: 'Consolas', monospace; font-size: 22px; "
            "font-weight: bold; color: #64748b; background: transparent;"
        )

        dot_row.addWidget(self.dot)
        dot_row.addWidget(self.value_label)
        dot_row.addStretch()

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addLayout(dot_row)
        self.setLayout(layout)

    def set_running(self, running):
        self.dot.set_running(running)
        self.value_label.setText("Running" if running else "Stopped")
        color = "#16a34a" if running else "#64748b"
        self.value_label.setStyleSheet(
            f"font-family: 'Consolas', monospace; font-size: 22px; "
            f"font-weight: bold; color: {color}; background: transparent;"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Cache hit card — embeds thin progress bar
# ─────────────────────────────────────────────────────────────────────────────
class CacheHitCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Card")

        title = QLabel("CACHE HIT RATE")
        title.setObjectName("CardTitle")

        self.value_label = QLabel("0.0%")
        self.value_label.setObjectName("CardValue")

        self.bar = QProgressBar()
        self.bar.setObjectName("HitBar")
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(self.value_label)
        layout.addWidget(self.bar)
        self.setLayout(layout)

    def set_rate(self, rate_fraction):
        pct = rate_fraction * 100
        self.value_label.setText(f"{pct:.1f}%")
        self.bar.setValue(int(pct))


# ─────────────────────────────────────────────────────────────────────────────
#  Uptime card — embeds UptimeCounter
# ─────────────────────────────────────────────────────────────────────────────
class UptimeCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Card")

        title = QLabel("SESSION UPTIME")
        title.setObjectName("CardTitle")

        self.counter = UptimeCounter()

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(6)
        layout.addWidget(title)
        layout.addWidget(self.counter)
        self.setLayout(layout)


# ─────────────────────────────────────────────────────────────────────────────
#  Main window
# ─────────────────────────────────────────────────────────────────────────────
class ControlPanel(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("SecureWatch Proxy Control Panel")
        self.resize(1280, 800)

        self.proxy_thread = None
        self.request_worker = None

        self.apply_style()
        self.build_ui()

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(2000)

        self.load_rules()
        self.refresh_all()

    # ── Stylesheet ────────────────────────────────────────────────────────────
    def apply_style(self):
        self.setStyleSheet("""
            /* ── Base ──────────────────────────────────────────────────── */
            QMainWindow {
                background-color: #edf2f9;
            }

            QWidget {
                background-color: #edf2f9;
                font-family: Verdana, Geneva, 'Segoe UI', sans-serif;
                font-size: 13px;
                color: #1a2e4a;
            }

            /* ── Page titles ────────────────────────────────────────────── */
            QLabel#Title {
                font-family: Georgia, 'Times New Roman', serif;
                font-size: 26px;
                font-weight: bold;
                color: #1a2e4a;
                background-color: transparent;
            }

            QLabel#Subtitle {
                font-size: 12px;
                color: #5a7090;
                background-color: transparent;
            }

            QLabel#SmallNote {
                font-size: 11px;
                color: #7a90a8;
                background-color: transparent;
            }

            /* ── Sidebar ────────────────────────────────────────────────── */
            QWidget#SidebarBox {
                background-color: #162d5c;
            }

            QLabel#SidebarBrand {
                font-family: Georgia, serif;
                font-size: 17px;
                font-weight: bold;
                color: #e8f0fc;
                background-color: transparent;
            }

            QLabel#SidebarSub {
                font-size: 10px;
                color: #6a8ab8;
                letter-spacing: 0.8px;
                background-color: transparent;
            }

            QFrame#SidebarDivider {
                background-color: #1e3a6e;
                border: none;
                min-height: 1px;
                max-height: 1px;
            }

            QListWidget#Sidebar {
                background-color: transparent;
                border: none;
                padding: 6px 0;
                outline: none;
            }

            QListWidget#Sidebar::item {
                padding: 11px 20px;
                color: #6a8ab8;
                border-left: 3px solid transparent;
                font-size: 13px;
                font-family: Verdana, sans-serif;
            }

            QListWidget#Sidebar::item:selected {
                background-color: #1e3d7a;
                color: #e8f4ff;
                border-left: 3px solid #5aaaf8;
            }

            QListWidget#Sidebar::item:hover:!selected {
                background-color: #1c3568;
                color: #a8c8f0;
            }

            /* ── Clock ──────────────────────────────────────────────────── */
            QLabel#LiveClock {
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 20px;
                font-weight: bold;
                color: #5aaaf8;
                background-color: transparent;
                letter-spacing: 2px;
            }

            QLabel#ClockLabel {
                font-size: 10px;
                color: #3d5a88;
                letter-spacing: 1.5px;
                background-color: transparent;
            }

            /* ── Stat cards ─────────────────────────────────────────────── */
            QFrame#Card {
                background-color: #ffffff;
                border: 1px solid #c8d8ee;
                border-top: 3px solid #1e4db7;
                border-radius: 5px;
            }

            QLabel#CardTitle {
                font-size: 10px;
                font-weight: bold;
                color: #5a7090;
                letter-spacing: 1.2px;
                background-color: transparent;
            }

            QLabel#CardValue {
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 22px;
                font-weight: bold;
                color: #1a2e4a;
                background-color: transparent;
            }

            /* ── Cache hit bar ──────────────────────────────────────────── */
            QProgressBar#HitBar {
                background-color: #ddeaf8;
                border: none;
                border-radius: 2px;
            }

            QProgressBar#HitBar::chunk {
                background-color: #1e4db7;
                border-radius: 2px;
            }

            /* ── Buttons ────────────────────────────────────────────────── */
            QPushButton {
                background-color: #1e4db7;
                color: #ffffff;
                border: none;
                border-bottom: 2px solid #163a8a;
                border-radius: 4px;
                padding: 8px 18px;
                font-weight: bold;
                font-size: 12px;
                font-family: Verdana, sans-serif;
            }

            QPushButton:hover {
                background-color: #2558cc;
            }

            QPushButton:pressed {
                background-color: #163a8a;
                border-bottom-width: 1px;
                padding-top: 9px;
            }

            QPushButton#QuietButton {
                background-color: #e8f0fc;
                color: #2858b8;
                border: 1px solid #b8ccee;
                border-bottom: 2px solid #a0badf;
            }

            QPushButton#QuietButton:hover {
                background-color: #d8e6f8;
            }

            QPushButton#QuietButton:pressed {
                background-color: #c8d8f0;
                border-bottom-width: 1px;
            }

            QPushButton#DangerButton {
                background-color: #dc2626;
                border-bottom: 2px solid #991b1b;
            }

            QPushButton#DangerButton:hover {
                background-color: #ef4444;
            }

            /* ── Inputs ─────────────────────────────────────────────────── */
            QLineEdit, QTextEdit, QPlainTextEdit {
                background-color: #ffffff;
                color: #1a2e4a;
                border: 1px solid #b8ccee;
                border-radius: 4px;
                padding: 7px 10px;
                selection-background-color: #1e4db7;
                selection-color: #ffffff;
            }

            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
                border-color: #3a6ad8;
            }

            /* ── ComboBox ───────────────────────────────────────────────── */
            QComboBox {
                background-color: #ffffff;
                color: #1a2e4a;
                border: 1px solid #b8ccee;
                border-radius: 4px;
                padding: 7px 34px 7px 10px;
            }

            QComboBox:focus {
                border-color: #3a6ad8;
            }

            QComboBox::drop-down {
                width: 28px;
                border-left: 1px solid #b8ccee;
                background-color: #e8f0fc;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
            }

            QComboBox::down-arrow {
                width: 0;
                height: 0;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #3a6ad8;
            }

            QComboBox QAbstractItemView {
                background-color: #ffffff;
                color: #1a2e4a;
                border: 1px solid #b8ccee;
                selection-background-color: #1e4db7;
                selection-color: #ffffff;
                outline: none;
            }

            QComboBox QAbstractItemView::item {
                padding: 8px 12px;
                min-height: 24px;
            }

            QComboBox QAbstractItemView::item:hover {
                background-color: #ddeaf8;
            }

            /* ── List widgets ───────────────────────────────────────────── */
            QListWidget {
                background-color: #ffffff;
                color: #1a2e4a;
                border: 1px solid #b8ccee;
                border-radius: 4px;
                outline: none;
            }

            QListWidget::item {
                padding: 7px 10px;
                border-bottom: 1px solid #ecf2fb;
            }

            QListWidget::item:selected {
                background-color: #ddeaf8;
                color: #1a2e4a;
                border-left: 3px solid #1e4db7;
            }

            QListWidget::item:hover:!selected {
                background-color: #f4f8fd;
            }

            /* ── Table ──────────────────────────────────────────────────── */
            QTableWidget {
                background-color: #ffffff;
                alternate-background-color: #f4f8fd;
                color: #1a2e4a;
                gridline-color: #e0eaf6;
                border: 1px solid #b8ccee;
            }

            QHeaderView {
                background-color: #edf3fb;
                border: none;
            }

            QHeaderView::section {
                background-color: #edf3fb;
                color: #3a5a80;
                padding: 9px 12px;
                border: none;
                border-bottom: 2px solid #b8ccee;
                border-right: 1px solid #d0e0f0;
                font-size: 10px;
                font-weight: bold;
                letter-spacing: 1px;
            }

            QTableWidget QTableCornerButton::section {
                background-color: #edf3fb;
                border: none;
                border-bottom: 2px solid #b8ccee;
            }

            QTableWidget::item {
                padding: 7px 12px;
            }

            QTableWidget::item:selected {
                background-color: #ddeaf8;
                color: #1a2e4a;
            }

            /* ── Scrollbars ─────────────────────────────────────────────── */
            QScrollBar:vertical {
                background-color: #f0f5fc;
                width: 8px;
                border: none;
            }

            QScrollBar::handle:vertical {
                background-color: #b8ccee;
                border-radius: 4px;
                min-height: 24px;
            }

            QScrollBar::handle:vertical:hover {
                background-color: #6a9ad8;
            }

            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                height: 0; background: none;
            }

            QScrollBar:horizontal {
                background-color: #f0f5fc;
                height: 8px;
                border: none;
            }

            QScrollBar::handle:horizontal {
                background-color: #b8ccee;
                border-radius: 4px;
            }

            QScrollBar::handle:horizontal:hover {
                background-color: #6a9ad8;
            }

            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal,
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                width: 0; background: none;
            }

            /* ── Message boxes ──────────────────────────────────────────── */
            QMessageBox {
                background-color: #ffffff;
            }

            QMessageBox QLabel {
                color: #1a2e4a;
                font-size: 13px;
                min-width: 360px;
                padding: 8px 4px;
                background-color: transparent;
            }

            QMessageBox QPushButton {
                min-width: 90px;
                min-height: 32px;
                padding: 7px 22px;
            }

            /* ── Status bar ─────────────────────────────────────────────── */
            QStatusBar {
                background-color: #162d5c;
                color: #6a8ab8;
                font-size: 11px;
                border-top: 1px solid #1e3a6e;
            }

            QStatusBar QLabel {
                color: #6a8ab8;
                background-color: transparent;
                padding: 0 10px;
                font-size: 11px;
                font-family: 'Consolas', monospace;
            }

            /* ── Generic labels ─────────────────────────────────────────── */
            QLabel {
                background-color: transparent;
                color: #1a2e4a;
            }
        """)

    # ── UI layout ─────────────────────────────────────────────────────────────
    def build_ui(self):
        root = QWidget()
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        root.setLayout(main_layout)

        # ── Sidebar ──────────────────────────────────────────────────────────
        sidebar_wrapper = QVBoxLayout()
        sidebar_wrapper.setContentsMargins(0, 0, 0, 0)
        sidebar_wrapper.setSpacing(0)

        # Brand
        brand_widget = QWidget()
        brand_widget.setObjectName("SidebarBox")
        brand_layout = QVBoxLayout()
        brand_layout.setContentsMargins(20, 22, 20, 18)
        brand_layout.setSpacing(3)
        brand_label = QLabel("SecureWatch")
        brand_label.setObjectName("SidebarBrand")
        sub_label = QLabel("PROXY CONTROL PANEL")
        sub_label.setObjectName("SidebarSub")
        brand_layout.addWidget(brand_label)
        brand_layout.addWidget(sub_label)
        brand_widget.setLayout(brand_layout)

        div1 = QFrame()
        div1.setObjectName("SidebarDivider")
        div1.setFixedHeight(1)

        # Nav
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        # Row order here matches the page insertion order in self.pages.
        self.sidebar.addItems([
            "  ◈  Dashboard",
            "  ⊡  Request Lab",
            "  ≡  Rules",
            "  ▤  Logs",
            "  ◫  Cache",
        ])
        self.sidebar.setCurrentRow(0)
        self.sidebar.currentRowChanged.connect(self.change_page)

        nav_widget = QWidget()
        nav_widget.setObjectName("SidebarBox")
        nav_layout = QVBoxLayout()
        nav_layout.setContentsMargins(0, 8, 0, 8)
        nav_layout.setSpacing(0)
        nav_layout.addWidget(self.sidebar)
        nav_widget.setLayout(nav_layout)

        div2 = QFrame()
        div2.setObjectName("SidebarDivider")
        div2.setFixedHeight(1)

        # Clock
        clock_widget = QWidget()
        clock_widget.setObjectName("SidebarBox")
        clock_layout = QVBoxLayout()
        clock_layout.setContentsMargins(20, 14, 20, 20)
        clock_layout.setSpacing(3)
        clock_label = QLabel("LOCAL TIME")
        clock_label.setObjectName("ClockLabel")
        clock_label.setAlignment(Qt.AlignCenter)
        self.clock = LiveClock()
        clock_layout.addWidget(clock_label)
        clock_layout.addWidget(self.clock)
        clock_widget.setLayout(clock_layout)

        sidebar_wrapper.addWidget(brand_widget)
        sidebar_wrapper.addWidget(div1)
        sidebar_wrapper.addWidget(nav_widget, 1)
        sidebar_wrapper.addWidget(div2)
        sidebar_wrapper.addWidget(clock_widget)

        sidebar_box = QWidget()
        sidebar_box.setObjectName("SidebarBox")
        sidebar_box.setLayout(sidebar_wrapper)
        sidebar_box.setFixedWidth(210)

        # ── Pages ────────────────────────────────────────────────────────────
        self.pages = QStackedWidget()
        # Keep this order aligned with sidebar indices to simplify page switching.
        self.pages.addWidget(self.build_dashboard_page())
        self.pages.addWidget(self.build_request_page())
        self.pages.addWidget(self.build_rules_page())
        self.pages.addWidget(self.build_logs_page())
        self.pages.addWidget(self.build_cache_page())

        main_layout.addWidget(sidebar_box)
        main_layout.addWidget(self.pages)

        self.setCentralWidget(root)

        # ── Status bar ───────────────────────────────────────────────────────
        sb = QStatusBar()
        self.setStatusBar(sb)
        self._sb_state = QLabel("●  PROXY STOPPED")
        self._sb_port  = QLabel("PORT  8888")
        self._sb_ver   = QLabel("SecureWatch  v1.0")
        sb.addWidget(self._sb_state)
        sb.addWidget(self._sb_port)
        sb.addPermanentWidget(self._sb_ver)

    # ── Section title helper ──────────────────────────────────────────────────
    def section_title(self, title, subtitle):
        box = QVBoxLayout()
        box.setSpacing(3)
        box.setContentsMargins(0, 0, 0, 10)
        tl = QLabel(title)
        tl.setObjectName("Title")
        sl = QLabel(subtitle)
        sl.setObjectName("Subtitle")
        box.addWidget(tl)
        box.addWidget(sl)
        return box

    # ── Pages ─────────────────────────────────────────────────────────────────
    def build_dashboard_page(self):
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(32, 26, 32, 26)
        layout.setSpacing(18)

        layout.addLayout(self.section_title(
            "Dashboard",
            "Start the proxy, monitor live status, and view the most important numbers."
        ))

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.start_button = QPushButton("▶  Start Proxy")
        self.start_button.clicked.connect(self.start_proxy)

        self.stop_button = QPushButton("■  Stop Proxy")
        self.stop_button.setObjectName("QuietButton")
        self.stop_button.clicked.connect(self.stop_proxy)

        btn_row.addWidget(self.start_button)
        btn_row.addWidget(self.stop_button)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        grid = QGridLayout()
        grid.setSpacing(12)

        self.status_card       = StatusCard()
        self.port_card         = Card("PROXY PORT", "8888")
        self.connections_card  = Card("ACTIVE CONNECTIONS", "0")
        self.mode_card         = Card("RULES MODE")
        self.cache_hit_card    = CacheHitCard()
        self.cache_entries_card = Card("CACHE ENTRIES", "0")
        self.uptime_card       = UptimeCard()

        grid.addWidget(self.status_card,        0, 0)
        grid.addWidget(self.port_card,          0, 1)
        grid.addWidget(self.connections_card,   0, 2)
        grid.addWidget(self.mode_card,          1, 0)
        grid.addWidget(self.cache_hit_card,     1, 1)
        grid.addWidget(self.cache_entries_card, 1, 2)
        grid.addWidget(self.uptime_card,        2, 0, 1, 3)

        layout.addLayout(grid)

        note = QLabel(
            "Demo flow:  Start Proxy  →  Request Lab  →  Send request  →  Logs  →  Rules  →  Block/Allow  →  Test again"
        )
        note.setObjectName("SmallNote")
        layout.addWidget(note)

        layout.addStretch()
        page.setLayout(layout)
        return page

    def build_request_page(self):
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(32, 26, 32, 26)
        layout.setSpacing(14)

        layout.addLayout(self.section_title(
            "Request Lab",
            "Send controlled requests through the proxy without typing curl commands manually."
        ))

        form = QGridLayout()
        form.setSpacing(10)
        form.setColumnMinimumWidth(0, 110)

        self.method_combo = QComboBox()
        self.method_combo.addItems(["GET", "POST"])

        self.preset_combo = QComboBox()
        # Store full site metadata as item data so we can fill URL/domain quickly.
        for site in PRESET_SITES:
            self.preset_combo.addItem(site["name"], site)
        self.preset_combo.currentIndexChanged.connect(self.fill_selected_url)

        self.url_input = QLineEdit()
        self.url_input.setText(PRESET_SITES[0]["url"])

        self.body_input = QTextEdit()
        self.body_input.setPlaceholderText("POST body, optional. Example: name=test")
        self.body_input.setFixedHeight(90)

        self.send_button = QPushButton("⇒  Send Through Proxy")
        self.send_button.clicked.connect(self.send_test_request)

        form.addWidget(QLabel("Method"),      0, 0)
        form.addWidget(self.method_combo,     0, 1)
        form.addWidget(QLabel("Preset Site"), 1, 0)
        form.addWidget(self.preset_combo,     1, 1)
        form.addWidget(QLabel("Request URL"), 2, 0)
        form.addWidget(self.url_input,        2, 1)
        form.addWidget(QLabel("Body"),        3, 0)
        form.addWidget(self.body_input,       3, 1)
        form.addWidget(self.send_button,      4, 1)

        self.response_output = QPlainTextEdit()
        self.response_output.setReadOnly(True)
        self.response_output.setPlaceholderText("The response preview will appear here.")

        layout.addLayout(form)
        layout.addWidget(QLabel("Result"))
        layout.addWidget(self.response_output)
        page.setLayout(layout)
        return page

    def build_rules_page(self):
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(32, 26, 32, 26)
        layout.setSpacing(14)

        layout.addLayout(self.section_title(
            "Rules",
            "Edit blacklist and whitelist rules. Choose a preset site or type a custom domain."
        ))

        top = QHBoxLayout()
        top.setSpacing(8)

        self.rules_mode_combo = QComboBox()
        self.rules_mode_combo.addItems(["blacklist", "whitelist"])

        save_btn = QPushButton("Save Rules")
        save_btn.clicked.connect(self.save_rules)

        reload_btn = QPushButton("Reload")
        reload_btn.setObjectName("QuietButton")
        reload_btn.clicked.connect(self.load_rules)

        top.addWidget(QLabel("Mode"))
        top.addWidget(self.rules_mode_combo)
        top.addWidget(save_btn)
        top.addWidget(reload_btn)
        top.addStretch()
        layout.addLayout(top)

        preset_row = QHBoxLayout()
        preset_row.setSpacing(8)
        self.rules_preset_combo = QComboBox()
        for site in PRESET_SITES:
            self.rules_preset_combo.addItem(f"{site['name']}  ({site['domain']})", site)

        add_bl = QPushButton("Add to Blacklist")
        add_bl.clicked.connect(self.add_preset_to_blocked)
        add_wl = QPushButton("Add to Whitelist")
        add_wl.clicked.connect(self.add_preset_to_allowed)

        preset_row.addWidget(QLabel("Preset"))
        preset_row.addWidget(self.rules_preset_combo)
        preset_row.addWidget(add_bl)
        preset_row.addWidget(add_wl)
        layout.addLayout(preset_row)

        lists = QHBoxLayout()
        lists.setSpacing(16)

        def make_list_box(label_text, list_attr_name, input_attr_name, placeholder):
            # Shared builder for blocked/allowed columns to keep both sides identical.
            box = QVBoxLayout()
            box.setSpacing(6)
            box.addWidget(QLabel(label_text))
            lst = QListWidget()
            setattr(self, list_attr_name, lst)
            inp = QLineEdit()
            inp.setPlaceholderText(placeholder)
            setattr(self, input_attr_name, inp)
            btn_row = QHBoxLayout()
            btn_row.setSpacing(6)
            add_btn = QPushButton("Add Domain")
            add_btn.clicked.connect(lambda: self.add_rule_item(inp, lst))
            rm_btn = QPushButton("Remove Selected")
            rm_btn.setObjectName("QuietButton")
            rm_btn.clicked.connect(lambda: self.remove_selected(lst))
            btn_row.addWidget(add_btn)
            btn_row.addWidget(rm_btn)
            box.addWidget(lst)
            box.addWidget(inp)
            box.addLayout(btn_row)
            return box

        lists.addLayout(make_list_box(
            "Blocked Domains / IPs", "blocked_list", "blocked_input",
            "Type domain, e.g. ads.example.com"
        ))
        lists.addLayout(make_list_box(
            "Allowed Domains / IPs", "allowed_list", "allowed_input",
            "Type domain, e.g. info.cern.ch"
        ))
        layout.addLayout(lists)

        hint = QLabel(
            "Tip: In blacklist mode, blocked domains are rejected. "
            "In whitelist mode, only allowed domains can pass."
        )
        hint.setObjectName("SmallNote")
        layout.addWidget(hint)
        page.setLayout(layout)
        return page

    def build_logs_page(self):
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(32, 26, 32, 26)
        layout.setSpacing(14)

        layout.addLayout(self.section_title(
            "Logs",
            "View proxy requests, responses, cache hits, blocked requests, and errors."
        ))

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        refresh_btn = QPushButton("Refresh Logs")
        refresh_btn.clicked.connect(self.load_logs)
        clear_btn = QPushButton("Clear Log File")
        clear_btn.setObjectName("QuietButton")
        clear_btn.clicked.connect(self.clear_logs)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()

        self.logs_output = QPlainTextEdit()
        self.logs_output.setReadOnly(True)

        layout.addLayout(btn_row)
        layout.addWidget(self.logs_output)
        page.setLayout(layout)
        return page

    def build_cache_page(self):
        page = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(32, 26, 32, 26)
        layout.setSpacing(14)

        layout.addLayout(self.section_title(
            "Cache",
            "View cache performance and clear cached responses during testing."
        ))

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        refresh_btn = QPushButton("Refresh Cache")
        refresh_btn.clicked.connect(self.load_cache)
        purge_btn = QPushButton("Purge All Cache")
        purge_btn.setObjectName("DangerButton")
        purge_btn.clicked.connect(self.purge_cache)
        btn_row.addWidget(refresh_btn)
        btn_row.addWidget(purge_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.cache_table = QTableWidget()
        self.cache_table.setColumnCount(4)
        self.cache_table.setHorizontalHeaderLabels(["URL", "Size Bytes", "TTL Remaining", "Expired"])
        self.cache_table.horizontalHeader().setStretchLastSection(True)
        self.cache_table.setShowGrid(True)
        self.cache_table.setAlternatingRowColors(True)
        self.cache_table.verticalHeader().setVisible(False)

        layout.addWidget(self.cache_table)
        page.setLayout(layout)
        return page

    # ── Navigation ────────────────────────────────────────────────────────────
    def change_page(self, index):
        # Sidebar index maps directly to the stacked-widget page index.
        self.pages.setCurrentIndex(index)

    # ── Proxy control ─────────────────────────────────────────────────────────
    def start_proxy(self):
        if proxy.is_running():
            QMessageBox.information(self, "Proxy", "Proxy is already running.")
            return
        # Run the proxy server in a daemon thread so the GUI stays responsive.
        self.proxy_thread = threading.Thread(target=proxy.start_server)
        self.proxy_thread.daemon = True
        self.proxy_thread.start()
        self.uptime_card.counter.start()
        QMessageBox.information(self, "Proxy", "Proxy started on 127.0.0.1:8888.")

    def stop_proxy(self):
        if not proxy.is_running():
            QMessageBox.information(self, "Proxy", "Proxy is not running.")
            return
        proxy.stop_server()
        self.uptime_card.counter.stop()
        QMessageBox.information(self, "Proxy", "Proxy stopped.")

    def fill_selected_url(self):
        site = self.preset_combo.currentData()
        if site:
            self.url_input.setText(site["url"])

    def send_test_request(self):
        method = self.method_combo.currentText()
        url = self.url_input.text().strip()
        body = self.body_input.toPlainText().strip()

        if not url:
            QMessageBox.warning(self, "Missing URL", "Please enter a URL.")
            return
        if not proxy.is_running():
            QMessageBox.warning(self, "Proxy Not Running", "Start the proxy before sending a request.")
            return

        # Use a worker thread to avoid freezing the UI while waiting for network I/O.
        self.response_output.setPlainText("Sending request through proxy...")
        self.request_worker = RequestWorker(method, url, body)
        self.request_worker.finished.connect(self.response_output.setPlainText)
        self.request_worker.start()

    # ── Rules ─────────────────────────────────────────────────────────────────
    def load_rules(self):
        self.blocked_list.clear()
        self.allowed_list.clear()

        # Create a default rules file on first run to keep the UI usable out of the box.
        if not os.path.exists(RULES_FILE):
            with open(RULES_FILE, "w", encoding="utf-8") as f:
                json.dump({"mode": "blacklist", "blocked": [], "allowed": []}, f, indent=2)

        try:
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                rules = json.load(f)
            self.rules_mode_combo.setCurrentText(rules.get("mode", "blacklist"))
            for item in rules.get("blocked", []):
                self.blocked_list.addItem(str(item))
            for item in rules.get("allowed", []):
                self.allowed_list.addItem(str(item))
        except Exception as e:
            QMessageBox.critical(self, "Rules Error", f"Could not load rules.json:\n{e}")

    def save_rules(self):
        # Read current widget state and persist it exactly as JSON config.
        rules = {
            "mode": self.rules_mode_combo.currentText(),
            "blocked": self.list_items(self.blocked_list),
            "allowed": self.list_items(self.allowed_list),
        }
        try:
            with open(RULES_FILE, "w", encoding="utf-8") as f:
                json.dump(rules, f, indent=2)
            QMessageBox.information(self, "Rules Saved", "rules.json was saved successfully.")
        except Exception as e:
            QMessageBox.critical(self, "Rules Error", f"Could not save rules.json:\n{e}")

    def add_preset_to_blocked(self):
        site = self.rules_preset_combo.currentData()
        if site:
            self.add_domain_to_list(site["domain"], self.blocked_list)

    def add_preset_to_allowed(self):
        site = self.rules_preset_combo.currentData()
        if site:
            self.add_domain_to_list(site["domain"], self.allowed_list)

    def add_domain_to_list(self, domain, list_widget):
        domain = domain.strip()
        if not domain:
            return
        # Prevent duplicate entries so rules stay predictable and easy to scan.
        if domain in self.list_items(list_widget):
            QMessageBox.information(self, "Duplicate Rule", "This domain already exists in the list.")
            return
        list_widget.addItem(domain)

    def list_items(self, list_widget):
        # Return normalized non-empty values only (trimmed strings).
        return [
            list_widget.item(i).text().strip()
            for i in range(list_widget.count())
            if list_widget.item(i).text().strip()
        ]

    def add_rule_item(self, input_box, list_widget):
        value = input_box.text().strip()
        if not value:
            return
        if value in self.list_items(list_widget):
            QMessageBox.information(self, "Duplicate Rule", "This rule already exists.")
            return
        list_widget.addItem(value)
        input_box.clear()

    def remove_selected(self, list_widget):
        # Remove all selected rows in one pass (supports multi-select).
        for item in list_widget.selectedItems():
            list_widget.takeItem(list_widget.row(item))

    # ── Logs ──────────────────────────────────────────────────────────────────
    def load_logs(self):
        try:
            if not os.path.exists(LOG_FILE):
                self.logs_output.setPlainText("proxy.log does not exist yet.")
                return
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            # Show the tail only; full logs can grow quickly during testing.
            self.logs_output.setPlainText("".join(lines[-300:]))
            self.logs_output.verticalScrollBar().setValue(
                self.logs_output.verticalScrollBar().maximum()
            )
        except Exception as e:
            self.logs_output.setPlainText(f"Could not read log file:\n{e}")

    def clear_logs(self):
        if QMessageBox.question(self, "Clear Logs", "Are you sure you want to clear proxy.log?") != QMessageBox.Yes:
            return
        try:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.write("")
            self.load_logs()
        except Exception as e:
            QMessageBox.critical(self, "Log Error", f"Could not clear logs:\n{e}")

    # ── Cache ─────────────────────────────────────────────────────────────────
    def load_cache(self):
        try:
            entries = cache.list_entries()
            self.cache_table.setRowCount(len(entries))
            # Keep the table in sync with the latest snapshot returned by cache.py.
            for row, entry in enumerate(entries):
                self.cache_table.setItem(row, 0, QTableWidgetItem(str(entry.get("url", ""))))
                self.cache_table.setItem(row, 1, QTableWidgetItem(str(entry.get("size_bytes", 0))))
                self.cache_table.setItem(row, 2, QTableWidgetItem(str(entry.get("ttl_remaining", 0))))
                self.cache_table.setItem(row, 3, QTableWidgetItem(str(entry.get("expired", False))))
            self.cache_table.resizeColumnsToContents()
        except Exception as e:
            QMessageBox.critical(self, "Cache Error", f"Could not load cache:\n{e}")

    def purge_cache(self):
        if QMessageBox.question(self, "Purge Cache", "Remove all cached responses?") != QMessageBox.Yes:
            return
        removed = cache.purge_all()
        self.load_cache()
        QMessageBox.information(self, "Cache Purged", f"Removed {removed} cache entries.")

    # ── Periodic refresh ──────────────────────────────────────────────────────
    def refresh_dashboard(self):
        # Proxy runtime state drives both cards and status-bar indicators.
        running = proxy.is_running()
        self.status_card.set_running(running)
        self.connections_card.set_value(proxy.active_connections)

        if running:
            self._sb_state.setText("●  PROXY RUNNING")
            self._sb_state.setStyleSheet(
                "color: #50d890; background: transparent; "
                "padding: 0 10px; font-size: 11px; font-family: Consolas;"
            )
        else:
            self._sb_state.setText("●  PROXY STOPPED")
            self._sb_state.setStyleSheet(
                "color: #6a8ab8; background: transparent; "
                "padding: 0 10px; font-size: 11px; font-family: Consolas;"
            )

        try:
            # Rules mode is read from disk so UI reflects external edits too.
            with open(RULES_FILE, "r", encoding="utf-8") as f:
                rules = json.load(f)
            self.mode_card.set_value(rules.get("mode", "blacklist"))
        except Exception:
            self.mode_card.set_value("—")

        try:
            # Cache stats are best-effort; on failure we fall back to safe defaults.
            stats = cache.stats()
            self.cache_hit_card.set_rate(stats.get("hit_rate", 0))
            self.cache_entries_card.set_value(stats.get("entries", 0))
        except Exception:
            self.cache_hit_card.set_rate(0)
            self.cache_entries_card.set_value("—")

    def refresh_all(self):
        # Dashboard is always refreshed; heavier pages refresh only when visible.
        self.refresh_dashboard()
        if self.pages.currentIndex() == 3:
            self.load_logs()
        if self.pages.currentIndex() == 4:
            self.load_cache()


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Verdana", 10))
    window = ControlPanel()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()