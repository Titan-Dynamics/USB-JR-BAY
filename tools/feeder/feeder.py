#!/usr/bin/env python3
# ELRS Calibration GUI + Telemetry (PyQt5)
# pip install pyqt5 pygame pyserial

import sys
import json
import time
import re
import threading
import csv
import os
import tempfile
import shutil
from datetime import datetime
from PyQt5 import QtWidgets, QtCore
from PyQt5.QtGui import QIcon, QPalette, QColor, QPixmap, QPainter, QPolygon, QPen, QBrush
from PyQt5.QtCore import QPoint, Qt, QSettings

# Import from refactored modules
from crsf_protocol import *
from device_parameters import DeviceParameterParser, DeviceInfo
from serial_interface import SerialThread
from joystick_handler import JoystickHandler
from channel_ui import ChannelRow, SRC_CHOICES
from config_manager import ConfigManager, get_available_ports, DEFAULT_BAUD, CHANNELS, DEFAULT_CFG
from version import VERSION, GIT_SHA

# Map known field names to units for numeric display in the UI
UNIT_MAP = {
    'max power': 'mW',
    'fan thresh': 'mW',
}

# Units for telemetry link stats displayed in the UI
TELEMETRY_UNIT_MAP = {
    '1RSS': 'dBm',
    '2RSS': 'dBm',
    'TRSS': 'dBm',
    'LQ': '%',
    'TLQ': '%',
    'RSNR': 'dB',
    'TSNR': 'dB',
    'TPWR': 'mW',
}

SEND_HZ = 60

def tpwr_to_mw(crsfpower):
    return {1: "10", 2: "25", 3: "100", 4: "500", 5: "1000",
            6: "2000", 7: "250", 8: "50"}.get(crsfpower, "Unknown")


class NoWheelComboBox(QtWidgets.QComboBox):
    """QComboBox that ignores mouse wheel events."""

    def wheelEvent(self, event):
        """Ignore wheel events to prevent accidental value changes."""
        event.ignore()


class NoWheelSpinBox(QtWidgets.QSpinBox):
    """QSpinBox that ignores mouse wheel events."""

    def wheelEvent(self, event):
        """Ignore wheel events to prevent accidental value changes."""
        event.ignore()


class NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """QDoubleSpinBox that ignores mouse wheel events."""

    def wheelEvent(self, event):
        """Ignore wheel events to prevent accidental value changes."""
        event.ignore()


class JoystickVisualizer(QtWidgets.QWidget):
    """Visual joystick position indicator"""
    def __init__(self, h_channel=1, v_channel=2):
        super().__init__()
        self.h_value = 1500  # Center position
        self.v_value = 1500  # Center position
        self.h_mapped = False  # Whether horizontal channel is mapped
        self.v_mapped = False  # Whether vertical channel is mapped
        self.h_channel = h_channel  # Horizontal channel number (for bottom label)
        self.v_channel = v_channel  # Vertical channel number (for left label)
        self.setMinimumSize(150, 170)  # Extra height for bottom label
        self.setMaximumSize(200, 220)  # Extra height for bottom label

    def set_values(self, h_val, v_val, h_mapped=True, v_mapped=True):
        """Update joystick position (1000-2000 range)

        Args:
            h_val: Horizontal value (1000-2000)
            v_val: Vertical value (1000-2000)
            h_mapped: Whether horizontal channel is mapped
            v_mapped: Whether vertical channel is mapped
        """
        self.h_value = h_val
        self.v_value = v_val
        self.h_mapped = h_mapped
        self.v_mapped = v_mapped
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()
        size = min(width, height) - 35
        offset_x = (width - size) // 2 + 8
        offset_y = (height - size) // 2

        # Dead zone indicator
        painter.setPen(QPen(QColor("#555555"), 2))
        painter.setBrush(QBrush(QColor("#2b2b2b")))
        painter.drawRoundedRect(offset_x, offset_y, size, size, 8, 8)

        painter.setPen(QPen(QColor("#666666"), 1))
        center_x = offset_x + size // 2
        center_y = offset_y + size // 2
        crosshair_extent = size // 2 - 16  # 16px from edges
        painter.drawLine(center_x - crosshair_extent, center_y, center_x + crosshair_extent, center_y)
        painter.drawLine(center_x, center_y - crosshair_extent, center_x, center_y + crosshair_extent)

        if self.h_mapped or self.v_mapped:
            # Invert vertical axis (lower value = higher on screen)
            center_stick_x = offset_x + size // 2
            center_stick_y = offset_y + size // 2

            # Use mapped values, center for unmapped axes
            stick_x = offset_x + int((self.h_value - 1000) / 1000.0 * size) if self.h_mapped else center_stick_x
            stick_y = offset_y + int((2000 - self.v_value) / 1000.0 * size) if self.v_mapped else center_stick_y

            stick_x = max(offset_x, min(offset_x + size, stick_x))
            stick_y = max(offset_y, min(offset_y + size, stick_y))

            painter.setPen(QPen(QColor("#1e88e5"), 2))
            painter.setBrush(QBrush(QColor("#1e88e5")))
            painter.drawEllipse(stick_x - 8, stick_y - 8, 16, 16)

        painter.setPen(QPen(QColor("#e0e0e0")))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        painter.drawText(0, offset_y, offset_x - 5, size, Qt.AlignRight | Qt.AlignVCenter, f"{self.v_channel}")
        painter.drawText(offset_x, offset_y + size + 5, size, 20, Qt.AlignHCenter | Qt.AlignTop, f"{self.h_channel}")


class Main(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"USB JR Bay - v{VERSION} ({GIT_SHA})")
        # Get the icon path - works both when running as script and as PyInstaller bundle
        icon_path = self._get_icon_path()
        if icon_path and os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        self.resize(1360, 900)

        # Set dark title bar on Windows 10/11
        self._set_dark_title_bar()

        # App-wide settings (separate from calibration data)
        self.settings = QSettings("USBJRBay", "USB_JR_Bay")

        self.cfg = DEFAULT_CFG.copy()
        self._load_cfg()

        # CSV logging setup
        self.csv_filename = None
        self.csv_buffer = []  # Buffer for CSV rows (max 600 lines)
        self.csv_last_write_time = 0.0  # Track last write time for 10Hz throttling
        self.csv_start_time = None  # Track when logging started for filename
        # Fieldnames: timestamp, channels 1-16, then link stats
        self.csv_fieldnames = ['timestamp'] + [f'CH{i+1}' for i in range(CHANNELS)] + ['1RSS', '2RSS', 'LQ', 'RSNR', 'RFMD', 'TPWR', 'TRSS', 'TLQ', 'TSNR']

        layout = QtWidgets.QVBoxLayout(self)

        # Mapping state (for joystick-to-channel learn)
        self.mapping_row = None
        self.mapping_baseline = ([], [])
        self.mapping_started_at = 0.0

        # Serial thread
        self.serThread = SerialThread(self.cfg["serial_port"], DEFAULT_BAUD)
        self.thread = threading.Thread(target=self.serThread.run, daemon=True)
        self.thread.start()
        self.serThread.telemetry.connect(self.onTel)
        self.serThread.debug.connect(self.onDebug)
        # Device discovery events
        self.serThread.device_discovered.connect(self._on_device_discovered)
        self.serThread.device_parameters_loaded.connect(self._on_device_parameters_loaded)
        self.serThread.device_parameters_progress.connect(self._on_device_parameters_progress)
        self.serThread.device_parameter_field_updated.connect(self._on_device_parameter_field_updated)
        self.serThread.connection_status.connect(self.onConnectionStatus)
        self.serThread.channels_update.connect(self.onChannels)
        self.serThread.sync_update.connect(self.onSync)

        # Module status (will be updated in window title)
        self._module_status = "No module detected"

        # Joystick (auto-scanning) - using JoystickHandler instead of Joy
        self.joy = JoystickHandler()
        self.joy.status.connect(self.onDebug)
        self.joy.status.connect(self.onJoyStatus)

        # Main content: channels on left, visualizers on right
        content_layout = QtWidgets.QHBoxLayout()

        # Left side: All 16 channels in single column (scrollable)
        self.rows = []
        channels_layout = QtWidgets.QVBoxLayout()
        channels_layout.setContentsMargins(0, 0, 8, 0)
        channels_layout.setSpacing(4)
        for i in range(CHANNELS):
            row = ChannelRow(i, self.cfg["channels"][i] if i < len(self.cfg["channels"]) else DEFAULT_CFG["channels"][0])
            row.changed.connect(self.save_cfg)
            row.mapRequested.connect(self.begin_mapping)
            # Pipe row debug output into the app debug log
            # Only connect debug logging for CH2 (index 1) to reduce noise
            try:
                if row.idx == 1:
                    row.debug.connect(self.onDebug)
            except Exception:
                pass

            # Wrap each row in a QGroupBox with channel label as title (like link stats)
            channel_box = QtWidgets.QGroupBox(f"CH{i+1}")
            box_layout = QtWidgets.QVBoxLayout(channel_box)
            box_layout.setContentsMargins(4, 0, 4, 4)
            box_layout.addWidget(row)

            self.rows.append(row)
            channels_layout.addWidget(channel_box)

        ch_container = QtWidgets.QWidget()
        ch_container.setLayout(channels_layout)

        ch_scroll = QtWidgets.QScrollArea()
        ch_scroll.setWidgetResizable(True)
        ch_scroll.setWidget(ch_container)
        ch_scroll.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        content_layout.addWidget(ch_scroll, 2)  # Give channels more space

        right_panel = QtWidgets.QVBoxLayout()
        right_panel.setContentsMargins(4, 0, 4, 0)
        right_panel.setSpacing(2)

        WIDGET_HEIGHT = 26
        display_row = QtWidgets.QHBoxLayout()
        display_row.setContentsMargins(0, 0, 0, 4)
        self.display_mode = NoWheelComboBox()
        self.display_mode.setFixedHeight(WIDGET_HEIGHT)
        self.display_mode.addItems(["Channels", "Mode 1 Sticks + Channels", "Mode 2 Sticks + Channels"])
        self.display_mode.currentTextChanged.connect(self._on_display_mode_changed)
        display_row.addWidget(self.display_mode, 1)
        right_panel.addLayout(display_row)

        self.viz_layout = QtWidgets.QHBoxLayout()
        self.viz_layout.setSpacing(4)
        self.viz_layout.setContentsMargins(0, 0, 0, 0)

        # Mode 1: Left stick = Rudder/Elevator, Right stick = Aileron/Throttle
        self.viz1_mode1 = JoystickVisualizer(h_channel=4, v_channel=2)
        self.viz2_mode1 = JoystickVisualizer(h_channel=1, v_channel=3)

        # Mode 2: Left stick = Rudder/Throttle, Right stick = Aileron/Elevator
        self.viz1_mode2 = JoystickVisualizer(h_channel=4, v_channel=3)
        self.viz2_mode2 = JoystickVisualizer(h_channel=1, v_channel=2)

        # Default to Mode 2 (adjusted when config loads)
        self.viz1 = self.viz1_mode2
        self.viz2 = self.viz2_mode2
        self.current_mode = "Mode 2"

        self.viz_layout.addWidget(self.viz1)
        self.viz_layout.addWidget(self.viz2)
        self.viz_widget = QtWidgets.QWidget()
        self.viz_widget.setLayout(self.viz_layout)
        right_panel.addWidget(self.viz_widget)
        self.viz_widget.setVisible(False)

        # Channel bars (5-16, or 1-16 in Channels mode)
        bars_widget = QtWidgets.QWidget()
        bars_layout = QtWidgets.QVBoxLayout(bars_widget)
        bars_layout.setSpacing(2)
        bars_layout.setContentsMargins(0, 0, 0, 0)
        self.channel_bars = []
        self.all_channel_bars = []
        self.bar_widgets = []
        for i in range(CHANNELS):
            bar_container = QtWidgets.QWidget()
            bar_layout = QtWidgets.QHBoxLayout(bar_container)
            bar_layout.setSpacing(4)
            bar_layout.setContentsMargins(0, 0, 0, 0)
            label = QtWidgets.QLabel(f"{i+1}")
            label.setMinimumWidth(20)
            bar = QtWidgets.QProgressBar()
            bar.setRange(1000, 2000)
            bar.setValue(1500)
            bar.setTextVisible(True)
            bar.setMaximumHeight(18)
            bar_layout.addWidget(label)
            bar_layout.addWidget(bar)
            bars_layout.addWidget(bar_container)
            self.all_channel_bars.append(bar)
            self.bar_widgets.append(bar_container)
            if i >= 4:
                self.channel_bars.append(bar)
            else:
                bar_container.setVisible(False)

        bars_scroll = QtWidgets.QScrollArea()
        bars_scroll.setWidgetResizable(True)
        bars_scroll.setWidget(bars_widget)
        bars_scroll.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        self.bars_scroll = bars_scroll
        right_panel.addWidget(bars_scroll)

        right_container = QtWidgets.QWidget()
        right_container.setLayout(right_panel)
        right_container.setMaximumWidth(350)
        content_layout.addWidget(right_container, 1)

        saved_mode = self.cfg.get("display_mode", "Channels")
        # Case-insensitive search for matching display mode
        index = -1
        for i in range(self.display_mode.count()):
            if self.display_mode.itemText(i).lower() == saved_mode.lower():
                index = i
                break
        if index >= 0:
            self.display_mode.setCurrentIndex(index)
            if index == 0:
                self._on_display_mode_changed(self.display_mode.itemText(index))
        else:
            self._on_display_mode_changed("Channels")

        # Telemetry
        tel = QtWidgets.QHBoxLayout()
        self.telLabels = {}
        for key in ["1RSS","2RSS","RSNR","TRSS","TSNR","LQ","TLQ","RFMD","TPWR"]:
            box = QtWidgets.QGroupBox(key)
            lab = QtWidgets.QLabel("--")
            lab.setAlignment(QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter)
            f = lab.font(); f.setPointSize(14); lab.setFont(f)
            v = QtWidgets.QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 10)
            v.addWidget(lab, alignment=QtCore.Qt.AlignCenter)
            tel.addWidget(box)
            self.telLabels[key] = lab

        # Initially set link stats labels to grey
        for lab in self.telLabels.values():
            lab.setStyleSheet("color: #888888;")

        # Collapsible log section
        self.console_container = QtWidgets.QWidget()
        console_layout = QtWidgets.QVBoxLayout(self.console_container)
        console_layout.setContentsMargins(0, 4, 0, 0)  # 4px top margin for gap from link stats
        console_layout.setSpacing(0)

        # Console header (clickable to toggle)
        self.console_header = QtWidgets.QPushButton("Console ▼")
        self.console_header.setFlat(True)
        self.console_header.setStyleSheet("""
            QPushButton {
                text-align: left;
                padding: 5px;
                background-color: #3c3c3c;
                border: 1px solid #555555;
                border-radius: 3px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
        """)
        self.console_header.setCursor(QtCore.Qt.PointingHandCursor)
        self.console_header.clicked.connect(self._toggle_console)
        console_layout.addWidget(self.console_header)

        # Log (fixed height)
        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.console.setFixedHeight(140)

        # Restore console state from app settings
        self.console_expanded = self.settings.value("console_expanded", True, type=bool)
        self.console.setVisible(self.console_expanded)
        self.console_header.setText("Console ▼" if self.console_expanded else "Console ▶")

        console_layout.addWidget(self.console)

        # Tabs for Channels and Configuration
        self.tabs = QtWidgets.QTabWidget()

        # Channels tab
        channels_tab = QtWidgets.QWidget()
        channels_tab_layout = QtWidgets.QVBoxLayout(channels_tab)

        WIDGET_HEIGHT = 26

        self.joyStatusLabel = QtWidgets.QLabel("Scanning for controller...")
        self.joyStatusLabel.setStyleSheet("color: red; font-weight: bold;")

        self.portCombo = NoWheelComboBox()
        self.portCombo.setMinimumWidth(180)
        self.portCombo.setFixedHeight(WIDGET_HEIGHT)
        self._refresh_port_list()
        saved_port = self.cfg["serial_port"]
        for i in range(self.portCombo.count()):
            if self.portCombo.itemData(i) == saved_port:
                self.portCombo.setCurrentIndex(i)
                break
        self.portCombo.currentTextChanged.connect(self._on_port_changed)

        self.refreshPortBtn = QtWidgets.QPushButton("Refresh")
        self.refreshPortBtn.clicked.connect(self._refresh_port_list)
        self.refreshPortBtn.setMaximumWidth(80)
        self.refreshPortBtn.setFixedHeight(WIDGET_HEIGHT)

        self.logging_enabled = QtWidgets.QCheckBox("Logging")
        self.logging_enabled.setFixedHeight(WIDGET_HEIGHT)
        self.logging_enabled.toggled.connect(self._on_logging_toggled)
        self.logging_enabled.setChecked(self.settings.value("logging_enabled", False, type=bool))

        self.jrBayStatusLabel = QtWidgets.QLabel("Disconnected")
        self.jrBayStatusLabel.setStyleSheet("color: red; font-weight: bold;")

        channels_tab_layout.addLayout(content_layout)
        self.tabs.addTab(channels_tab, "Controller")

        config_tab = QtWidgets.QWidget()
        config_layout = QtWidgets.QVBoxLayout(config_tab)
        self.tabs.addTab(config_tab, self._module_status)
        config_tab.setMinimumSize(400, 300)
        self.config_loading = QtWidgets.QProgressBar()
        self.config_loading.setRange(0, 0)
        self.config_loading.setMaximumHeight(12)
        self.config_loading.setVisible(False)
        config_layout.addWidget(self.config_loading)
        self._config_field_widgets = {}
        self._pending_param_writes = {}

        self.tabs.setCurrentIndex(0)
        self.tabs.setTabEnabled(1, False)
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setContentsMargins(0, 4, 0, 4)
        top_bar.addWidget(self.joyStatusLabel)

        divider1 = QtWidgets.QFrame()
        divider1.setFrameShape(QtWidgets.QFrame.VLine)
        divider1.setFrameShadow(QtWidgets.QFrame.Sunken)
        divider1.setLineWidth(2)
        top_bar.addWidget(divider1)

        top_bar.addWidget(QtWidgets.QLabel("ESP32 COM Port:"))
        top_bar.addWidget(self.portCombo)
        top_bar.addWidget(self.refreshPortBtn)

        divider2 = QtWidgets.QFrame()
        divider2.setFrameShape(QtWidgets.QFrame.VLine)
        divider2.setFrameShadow(QtWidgets.QFrame.Sunken)
        divider2.setLineWidth(2)
        top_bar.addWidget(divider2)

        top_bar.addWidget(self.jrBayStatusLabel)
        top_bar.addStretch()
        top_bar.addSpacing(32)
        top_bar.addWidget(self.logging_enabled)

        layout.addLayout(top_bar)

        h_divider_top = QtWidgets.QFrame()
        h_divider_top.setFrameShape(QtWidgets.QFrame.HLine)
        h_divider_top.setFrameShadow(QtWidgets.QFrame.Sunken)
        h_divider_top.setLineWidth(2)
        layout.addWidget(h_divider_top)

        layout.addWidget(self.tabs)
        layout.addLayout(tel)
        layout.addWidget(self.console_container)
        layout.setStretch(2, 1)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(int(1000 / SEND_HZ))

        # Schedule initial connection attempt after GUI is shown
        def attempt_initial_connection():
            self.serThread._connect()
            self.serThread._initial_connect_attempted = True
        QtCore.QTimer.singleShot(500, attempt_initial_connection)

        # TX heartbeat tracking
        self._last_tx_heartbeat = 0.0
        self._tx_connected = False
        # manage cooldowns for resets to avoid repeated resets in tight loops
        self._last_tx_reset_time = 0.0

    def _update_module_status(self):
        """Update module status in tab title"""
        self.tabs.setTabText(1, self._module_status)

    def onTel(self, d):
        for k, v in d.items():
            if k in self.telLabels:
                try:
                    # Special handling for TPWR - convert to mW value
                    if k == 'TPWR' and v is not None:
                        mw_value = tpwr_to_mw(v)
                        unit = TELEMETRY_UNIT_MAP.get(k)
                        if unit:
                            text = f"{mw_value} {unit}"
                        else:
                            text = mw_value
                    else:
                        unit = TELEMETRY_UNIT_MAP.get(k)
                        if unit and v is not None:
                            # display numeric values with units
                            if isinstance(v, float):
                                text = f"{v:.1f} {unit}"
                            else:
                                text = f"{v} {unit}"
                        else:
                            text = str(v)
                except Exception:
                    text = str(v)
                self.telLabels[k].setText(text)


    def onConnectionStatus(self, is_connected):
        """Update status indicator based on actual connection state"""
        if is_connected:
            # Serial port connected
            try:
                self.jrBayStatusLabel.setText("Connected")
                self.jrBayStatusLabel.setStyleSheet("color: white; font-weight: bold;")
                # Disable refresh button while connected to prevent port switching issues
                self.refreshPortBtn.setEnabled(False)
                # Update port combo to show the connected port
                connected_port = self.serThread.port
                if connected_port:
                    # Refresh the port list to include the newly connected port
                    current_text = self.portCombo.currentText()
                    self._refresh_port_list()
                    # Select the connected port in the combo box
                    for i in range(self.portCombo.count()):
                        if self.portCombo.itemData(i) == connected_port:
                            self.portCombo.setCurrentIndex(i)
                            break
            except Exception:
                pass
        else:
            # Serial port disconnected
            try:
                self.jrBayStatusLabel.setText("Disconnected")
                self.jrBayStatusLabel.setStyleSheet("color: red; font-weight: bold;")
                # Re-enable refresh button when disconnected
                self.refreshPortBtn.setEnabled(True)
            except Exception:
                pass
            # When the serial port disconnects the TX is implicitly unreachable
            try:
                self._module_status = "No module detected"
                self._update_module_status()
                # Disable Configuration tab when module disconnects
                self.tabs.setTabEnabled(1, False)
            except Exception:
                pass

    def onDebug(self, s):
        try:
            self.console.appendPlainText(s)
        except Exception:
            pass

    def onJoyStatus(self, s):
        # Show scanning/connected/disconnected messages or name
        try:
            self.joyStatusLabel.setText(str(s))
            # Set color based on status
            status_lower = str(s).lower()
            if "scanning" in status_lower or "no joystick" in status_lower or "disconnected" in status_lower:
                self.joyStatusLabel.setStyleSheet("color: red; font-weight: bold;")
            else:
                # Connected - show joystick name in white
                self.joyStatusLabel.setStyleSheet("color: white; font-weight: bold;")
        except Exception:
            pass

    def onChannels(self, chans):
        """Update GUI channel displays when CRSF RC_CHANNELS frames arrive."""
        try:
            for i, v in enumerate(chans):
                if i < len(self.rows):
                    self.rows[i].bar.setValue(v)
                    self.rows[i].val.setText(str(v))
        except Exception as e:
            self.onDebug(f"onChannels error: {e}")

    def onSync(self, interval_us, offset_us, src):
        """Handle mixer sync (host timing) updates from CRSF frames.
        Sets the GUI send timer interval to approximate the CRSF RC packet interval.
        """
        try:
            if interval_us <= 0:
                return
            # GUI updates and TX heartbeat update
            # Record heartbeat time for this TX source
            try:
                self._last_tx_heartbeat = time.time()
                # mark TX connected and update module status
                if not self._tx_connected:
                    self._tx_connected = True
                    # tx name from device discovery if present
                    tx_name = None
                    try:
                        dev = self.serThread.elrs_devices.get(src, {})
                        tx_name = dev.get('name') if isinstance(dev, dict) else None
                    except Exception:
                        tx_name = None
                    if tx_name:
                        self._module_status = tx_name
                    else:
                        self._module_status = "Module Connected"
                    self._update_module_status()
                    # Enable Configuration tab when module is detected
                    self.tabs.setTabEnabled(1, True)
            except Exception as e:
                self.onDebug(f"onSync heartbeat update error: {e}")
        except Exception as e:
            self.onDebug(f"onSync error: {e}")

    def tick(self):
        axes, btns = self.joy.read()
        joystick_connected = self.joy.j is not None

        # Mapping mode: detect next button press or large axis move
        if self.mapping_row is not None:
            try:
                # Check for timeout first (5 seconds)
                if time.time() - self.mapping_started_at > 5.0:
                    # Timeout - reset button
                    try:
                        self.mapping_row.mapBtn.setText("Map")
                        self.mapping_row.mapBtn.setEnabled(True)
                    except Exception:
                        pass
                    self.onDebug("Mapping timed out; try again.")
                    self.mapping_row = None
                else:
                    base_axes, base_btns = self.mapping_baseline
                    detected = None
                    # Button press has priority
                    if btns and base_btns:
                        for i in range(min(len(btns), len(base_btns))):
                            if base_btns[i] == 0 and btns[i] == 1:
                                detected = ("button", i)
                                break
                    # Axis movement if no button detected
                    if detected is None and axes and base_axes:
                        best_i, best_d = -1, 0.0
                        for i in range(min(len(axes), len(base_axes))):
                            d = abs(axes[i] - base_axes[i])
                            if d > best_d:
                                best_d, best_i = d, i
                        if best_i >= 0 and best_d > 0.35:
                            detected = ("axis", best_i)

                    if detected is not None:
                        src, idx = detected
                        try:
                            self.mapping_row.set_mapping(src, idx)
                            self.onDebug(f"Mapped CH{self.mapping_row.idx+1} to {src}[{idx}]")
                            self.save_cfg()
                        except Exception as e:
                            self.onDebug(f"Mapping save error: {e}")
                        finally:
                            # Always reset button, even if save fails
                            try:
                                self.mapping_row.mapBtn.setText("Map")
                                self.mapping_row.mapBtn.setEnabled(True)
                            except Exception:
                                pass
                            self.mapping_row = None
            except Exception as e:
                # If any error occurs during mapping, reset the button
                self.onDebug(f"Mapping error: {e}")
                try:
                    if self.mapping_row is not None:
                        self.mapping_row.mapBtn.setText("Map")
                        self.mapping_row.mapBtn.setEnabled(True)
                except Exception:
                    pass
                self.mapping_row = None

        ch = [r.compute(axes, btns) for r in self.rows]

        # Enforce toggle groups: only one toggle per group can be on
        ch = self._enforce_toggle_groups(ch)

        # Update joystick visualizers (CH1-4)
        try:
            if len(ch) >= 4:
                # Check which channels are mapped (not "none") from saved config
                ch_mapped = [self.cfg["channels"][i].get("src", "none") != "none" for i in range(4)]

                if self.current_mode == "Mode 1":
                    # Mode 1: Left stick = CH4 horiz, CH2 vert; Right stick = CH1 horiz, CH3 vert
                    self.viz1.set_values(ch[3], ch[1], ch_mapped[3], ch_mapped[1])  # CH4 horizontal, CH2 vertical
                    self.viz2.set_values(ch[0], ch[2], ch_mapped[0], ch_mapped[2])  # CH1 horizontal, CH3 vertical
                else:  # Mode 2
                    # Mode 2: Left stick = CH4 horiz, CH3 vert; Right stick = CH1 horiz, CH2 vert
                    self.viz1.set_values(ch[3], ch[2], ch_mapped[3], ch_mapped[2])  # CH4 horizontal, CH3 vertical
                    self.viz2.set_values(ch[0], ch[1], ch_mapped[0], ch_mapped[1])  # CH1 horizontal, CH2 vertical
        except Exception:
            pass

        # Update progress bars for channels 1-16
        try:
            # Update all channel bars in real-time
            for i, bar in enumerate(self.all_channel_bars):
                if i < len(ch):
                    bar.setValue(ch[i])
        except Exception:
            pass

        # Update the shared channel buffer (decoupled); avoid transmitting when joystick disconnected
        if joystick_connected:
            try:
                self.serThread.send_channels(ch)
            except Exception:
                pass

        # TX heartbeat timeout: if we haven't seen a sync (handset timing) for >2s, mark TX disconnected
        try:
            if self._tx_connected and (time.time() - self._last_tx_heartbeat) > 2.0:
                self._tx_connected = False
                try:
                    self._module_status = "No module detected"
                    self._update_module_status()
                    # Disable Configuration tab when module disconnects
                    self.tabs.setTabEnabled(1, False)
                except Exception:
                    pass
                # Reset discovery state for the TX so that a fresh device ping occurs
                nowt = time.time()
                if nowt - self._last_tx_reset_time > 1.0:
                    try:
                        self.serThread.reset_tx_disconnected()
                    except Exception as e:
                        self.onDebug(f"Error resetting TX discovery: {e}")
                    self._last_tx_reset_time = nowt
        except Exception as e:
            self.onDebug(f"TX heartbeat check failed: {e}")

        # CSV logging at 10Hz (if enabled)
        now = time.time()
        if self.logging_enabled.isChecked() and (now - self.csv_last_write_time) >= 0.1:  # 10Hz = 100ms
            self._log_to_csv(ch)
            self.csv_last_write_time = now

        # Link stats timeout check
        timeout = now - self.serThread.last_link_stats_time > 5.0
        color = "#888888" if timeout else "#e0e0e0"
        for lab in self.telLabels.values():
            lab.setStyleSheet(f"color: {color};")

    def save_cfg(self):
        self.cfg["channels"] = [r.to_cfg() for r in self.rows]
        self._save_cfg_disk()
        self.onDebug("Config saved")

    def _enforce_toggle_groups(self, ch):
        ch = list(ch)  # Make a copy to avoid modifying the original

        # Build a map of group ID to list of (channel_idx, row) tuples
        groups = {}
        for i, row in enumerate(self.rows):
            if row.toggleBox.isChecked():
                # Convert dropdown index to group ID: 0 = None (-1), 1-8 = Groups 1-8 (0-7)
                dropdown_index = row.toggleGroupBox.currentIndex()
                if dropdown_index == 0:
                    # Skip toggles with group "None" - they are independent
                    continue
                group_id = dropdown_index - 1  # Convert to 0-7 for Groups 1-8
                if group_id not in groups:
                    groups[group_id] = []
                groups[group_id].append((i, row))

        # For each group, ensure only one toggle is on at a time
        for group_id, members in groups.items():
            # Find all toggles that are currently on and which was most recently activated
            on_toggles = []
            for idx, row in members:
                if row._btn_toggle_state == 1:
                    on_toggles.append((row._toggle_activated_time, idx, row))

            # If multiple toggles are on, keep only the most recently activated one
            if len(on_toggles) > 1:
                # Sort by activation time (most recent last)
                on_toggles.sort(key=lambda x: x[0])
                # Turn off all but the most recently activated one
                for _, idx, row in on_toggles[:-1]:
                    row._btn_toggle_state = 0
                    ch[idx] = row.minBox.value()

        return ch

    def _save_cfg_disk(self):
        try:
            with open("calib.json", "w") as f:
                json.dump(self.cfg, f, indent=2)
        except Exception as e:
            self.onDebug(f"Save error: {e}")

    def _get_icon_path(self):
        """Get the path to icon.ico, handling both PyInstaller bundle and normal execution"""
        try:
            # PyInstaller creates a temp folder and stores path in _MEIPASS
            base_path = sys._MEIPASS
        except AttributeError:
            # Not running as PyInstaller bundle, use script directory
            base_path = os.path.dirname(os.path.abspath(__file__))

        return os.path.join(base_path, 'icon.ico')

    def _set_dark_title_bar(self):
        """Set dark title bar on Windows 10/11"""
        try:
            import platform
            if platform.system() == "Windows":
                # For Windows 10/11, use DWM API to enable dark title bar
                try:
                    from ctypes import windll, c_int, byref, sizeof
                    HWND = int(self.winId())
                    # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Windows 11) or 19 (Windows 10 older builds)
                    DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                    value = c_int(1)  # 1 = dark mode, 0 = light mode
                    windll.dwmapi.DwmSetWindowAttribute(HWND, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(value), sizeof(value))
                except Exception:
                    # Try the older Windows 10 attribute if the newer one fails
                    try:
                        DWMWA_USE_IMMERSIVE_DARK_MODE = 19
                        value = c_int(1)
                        windll.dwmapi.DwmSetWindowAttribute(HWND, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(value), sizeof(value))
                    except Exception:
                        pass
        except Exception:
            pass

    def _load_cfg(self):
        try:
            with open("calib.json", "r") as f:
                disk = json.load(f)
            self.cfg.update(disk)
            chs = self.cfg.get("channels", [])
            if len(chs) < CHANNELS:
                chs += [DEFAULT_CFG["channels"][0]] * (CHANNELS - len(chs))
            self.cfg["channels"] = chs[:CHANNELS]
        except:
            pass

    def _refresh_port_list(self):
        """Refresh the list of available COM ports"""
        current = self.portCombo.currentData()  # Get actual port data, not display text
        self.portCombo.blockSignals(True)
        self.portCombo.clear()
        ports = get_available_ports()
        for port, desc in ports:
            # Display port with device name, but store port number as data
            if desc:
                display_text = f"{port} - {desc}"
            else:
                display_text = port
            self.portCombo.addItem(display_text, port)
        # If the previous selection still exists, restore it
        if current:
            for i in range(self.portCombo.count()):
                if self.portCombo.itemData(i) == current:
                    self.portCombo.setCurrentIndex(i)
                    self.portCombo.blockSignals(False)
                    return
        # Otherwise select the first available port
        if ports:
            self.portCombo.setCurrentIndex(0)
        self.portCombo.blockSignals(False)

    def _on_display_mode_changed(self, mode):
        """Handle display mode change between Mode 1, Mode 2, and Channels"""
        # Save display mode preference to config as lowercase
        self.cfg["display_mode"] = mode.lower()
        self._save_cfg_disk()

        mode_lower = mode.lower()
        if mode_lower == "channels":
            # Hide joystick visualizers and show all channel bars
            self.viz_widget.setVisible(False)
            # Show channels 1-4 in the bars
            for i in range(4):
                self.bar_widgets[i].setVisible(True)
        elif mode_lower == "mode 1 sticks + channels":
            # Show joystick visualizers and hide first 4 channel bars
            self.viz_widget.setVisible(True)
            # Hide channels 1-4 from the bars
            for i in range(4):
                self.bar_widgets[i].setVisible(False)
            # Swap to Mode 1 visualizers
            if self.current_mode != "Mode 1":
                self.viz_layout.removeWidget(self.viz1)
                self.viz_layout.removeWidget(self.viz2)
                self.viz1.hide()
                self.viz2.hide()
                self.viz1 = self.viz1_mode1
                self.viz2 = self.viz2_mode1
                self.viz_layout.addWidget(self.viz1)
                self.viz_layout.addWidget(self.viz2)
                self.viz1.show()
                self.viz2.show()
                self.current_mode = "Mode 1"
        elif mode_lower == "mode 2 sticks + channels":
            # Show joystick visualizers and hide first 4 channel bars
            self.viz_widget.setVisible(True)
            # Hide channels 1-4 from the bars
            for i in range(4):
                self.bar_widgets[i].setVisible(False)
            # Swap to Mode 2 visualizers
            if self.current_mode != "Mode 2":
                self.viz_layout.removeWidget(self.viz1)
                self.viz_layout.removeWidget(self.viz2)
                self.viz1.hide()
                self.viz2.hide()
                self.viz1 = self.viz1_mode2
                self.viz2 = self.viz2_mode2
                self.viz_layout.addWidget(self.viz1)
                self.viz_layout.addWidget(self.viz2)
                self.viz1.show()
                self.viz2.show()
                self.current_mode = "Mode 2"

    def _on_port_changed(self, port_display):
        """Handle COM port selection change"""
        if not port_display:
            return
        # Get the actual port from the combo box data
        port = self.portCombo.currentData()
        if not port:
            port = port_display  # Fallback if data not set
        self.cfg["serial_port"] = port
        self.serThread.reconnect(port, DEFAULT_BAUD)
        self.save_cfg()

    def _on_param_changed(self, fid, value):
        """Handle parameter change from config tab"""
        if hasattr(self, 'current_device_id'):
            device_id = self.current_device_id
            payload = bytes([device_id, CRSF_ADDRESS_ELRS_LUA, int(fid) & 0xFF, int(value) & 0xFF])
            try:
                msg = f"Param cmd: send parameter write payload: {payload.hex()} (dev={device_id}, fid={fid}, value={value})"
                self.onDebug(msg)
                try:
                    # Also emit on the serial thread debug signal so tests/listeners can capture it
                    self.serThread.debug.emit(msg)
                except Exception:
                    pass
            except Exception:
                pass
            self.serThread._send_crsf_cmd(CRSF_FRAMETYPE_PARAMETER_WRITE, payload)
            # Track pending write so a later device read doesn't override the UI
            try:
                self._pending_param_writes[int(fid)] = (value, time.time())
            except Exception:
                pass
            # If RF Band changed, request a refresh of Packet Rate (sibling) so values/options update
            try:
                dev = self.serThread.elrs_devices.get(device_id, {})
                fields = dev.get('fields', {})
                fld = fields.get(fid, {})
                if fld and isinstance(fld.get('name', ''), str):
                    fname = fld.get('name', '').strip().lower()
                    if fname == 'rf band' or ('rf' in fname and 'band' in fname):
                        # find the packet rate field id, if present
                        for pid, pfield in fields.items():
                            pname = pfield.get('name', '')
                            if isinstance(pname, str) and pname.strip().lower() == 'packet rate':
                                self.serThread.request_parameter_read(device_id, pid)
                                break
            except Exception as e:
                self.onDebug(f"Error scheduling Packet Rate reload: {e}")

    def _toggle_console(self):
        """Toggle console visibility"""
        self.console_expanded = not self.console_expanded
        self.console.setVisible(self.console_expanded)
        # Update arrow indicator
        if self.console_expanded:
            self.console_header.setText("Console ▼")
        else:
            self.console_header.setText("Console ▶")
        # Save state to app settings
        self.settings.setValue("console_expanded", self.console_expanded)

    def _on_logging_toggled(self, checked):
        """Handle logging checkbox toggle"""
        self.settings.setValue("logging_enabled", checked)
        if checked:
            # Capture start time for filename
            self.csv_start_time = datetime.now()
            # Start logging - create CSV file
            self._setup_csv_logging()
        else:
            # Stop logging - flush buffer and close
            self._flush_csv_buffer()
            if self.csv_filename:
                self.onDebug(f"CSV logging stopped: {self.csv_filename}")
                self.csv_filename = None
                self.csv_start_time = None

    def closeEvent(self, e):
        try:
            self.serThread.close()
        except:
            pass
        # Flush and close CSV logging
        try:
            self._flush_csv_buffer()
            if hasattr(self, 'csv_filename') and self.csv_filename:
                self.onDebug(f"CSV logging stopped: {self.csv_filename}")
        except:
            pass
        e.accept()

    # --- Mapping helpers ---
    def begin_mapping(self, row: ChannelRow):
        try:
            # If another mapping is active, cancel it visually
            if self.mapping_row is not None and hasattr(self.mapping_row, "mapBtn"):
                try:
                    self.mapping_row.mapBtn.setText("Map")
                    self.mapping_row.mapBtn.setEnabled(True)
                except Exception:
                    pass

            # Verify joystick is connected before starting mapping
            if self.joy.j is None:
                self.onDebug("No joystick connected. Cannot map channel.")
                return

            # Read baseline joystick state
            axes, btns = self.joy.read()
            if axes is None and btns is None:
                self.onDebug("Cannot read joystick state. Cannot map channel.")
                return

            self.mapping_row = row
            self.mapping_baseline = (axes, btns)
            self.mapping_started_at = time.time()
            try:
                row.mapBtn.setText("...")
                row.mapBtn.setEnabled(False)
            except Exception:
                pass
            self.onDebug(f"Move an axis or press a button to map CH{row.idx+1} …")
        except Exception as e:
            self.onDebug(f"Error starting mapping: {e}")
            # Reset button if anything goes wrong
            try:
                if row and hasattr(row, "mapBtn"):
                    row.mapBtn.setText("Map")
                    row.mapBtn.setEnabled(True)
            except Exception:
                pass
            self.mapping_row = None

    def _setup_csv_logging(self):
        """Initialize CSV logging with link stats and channel outputs"""
        try:
            # Use executable directory when running as PyInstaller bundle
            if getattr(sys, 'frozen', False):
                # Running as PyInstaller bundle - use executable directory
                script_dir = os.path.dirname(sys.executable)
            else:
                # Running as normal Python script
                script_dir = os.path.dirname(os.path.abspath(__file__))

            logs_dir = os.path.join(script_dir, "logs")
            os.makedirs(logs_dir, exist_ok=True)

            # Use start time for filename (captured when logging was enabled)
            timestamp = self.csv_start_time.strftime("%Y%m%d_%H%M%S") if self.csv_start_time else datetime.now().strftime("%Y%m%d_%H%M%S")
            self.csv_filename = os.path.join(logs_dir, f"data_log_{timestamp}.csv")

            # Create CSV file and write header
            with open(self.csv_filename, 'w', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=self.csv_fieldnames)
                writer.writeheader()

            # Clear buffer
            self.csv_buffer = []
            self.onDebug(f"CSV logging started: {self.csv_filename}")
        except Exception as e:
            self.onDebug(f"CSV logging setup error: {e}")
            self.csv_filename = None

    def _log_to_csv(self, channel_values):
        """Add data to CSV buffer (link stats + channels), flush when buffer reaches 600 lines"""
        if self.csv_filename is None:
            return

        try:
            # Create row with timestamp
            row = {'timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]}

            # Add link stats (get latest from telemetry labels)
            link_stats_fields = ['1RSS', '2RSS', 'LQ', 'RSNR', 'RFMD', 'TPWR', 'TRSS', 'TLQ', 'TSNR']
            for field in link_stats_fields:
                try:
                    text = self.telLabels.get(field, QtWidgets.QLabel("--")).text()
                    # Extract numeric value (remove units)
                    if text and text != "--":
                        row[field] = text.split()[0]  # Get first token (the number)
                    else:
                        row[field] = ''
                except Exception:
                    row[field] = ''

            # Add channel values (only for mapped channels)
            for i in range(CHANNELS):
                if i < len(channel_values):
                    # Check if channel is mapped
                    src = self.rows[i].src.currentText() if i < len(self.rows) else "none"
                    if src != "none":
                        row[f'CH{i+1}'] = channel_values[i]
                    else:
                        row[f'CH{i+1}'] = ''
                else:
                    row[f'CH{i+1}'] = ''

            # Add to buffer
            self.csv_buffer.append(row)

            # Flush if buffer reaches 600 lines
            if len(self.csv_buffer) >= 600:
                self._flush_csv_buffer()

        except Exception as e:
            # Avoid spamming the log with CSV errors
            pass

    def _flush_csv_buffer(self):
        """Write buffered CSV data to file"""
        if not self.csv_buffer or self.csv_filename is None:
            return

        try:
            with open(self.csv_filename, 'a', newline='') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=self.csv_fieldnames)
                writer.writerows(self.csv_buffer)
            self.csv_buffer = []
        except Exception as e:
            self.onDebug(f"CSV flush error: {e}")



    def _on_device_discovered(self, src: int, details: dict):
        # Update the UI state when a device is discovered: show device name and mark current device
        name = details.get('name', 'Unknown')
        try:
            # Only set current_device_id for TX modules, not receivers
            if src in (CRSF_ADDRESS_CRSF_TRANSMITTER, CRSF_ADDRESS_TRANSMITTER_LEGACY):
                self.serThread.current_device_id = src
                # Update the module name in tab title
                try:
                    self._module_status = name
                    self._update_module_status()
                except Exception:
                    pass
            self.onDebug(f"Device discovered: {name} addr=0x{src:02X}")
            # If this device has params to load, show loading indicator
            n_params = details.get('n_params', 0)
            loaded = details.get('loaded', False)
            try:
                if n_params and n_params > 0 and not loaded:
                    self.config_loading.setVisible(True)
                else:
                    self.config_loading.setVisible(False)
            except Exception:
                pass
        except Exception:
            pass

    def _on_device_parameters_loaded(self, src: int, details: dict):
        # Populate packet rate dropdown when parameters are loaded
        fields = details.get('fields', {})

        # Only populate config tab after the serial thread indicates the device is fully loaded
        loaded = details.get('loaded', False)
        if not loaded:
            # keep spinner visible, but don't re-populate the config tab until fully loaded
            try:
                self.config_loading.setVisible(True)
            except Exception:
                pass
            return

        # Hide loading indicator and populate the UI once full parameters are available
        try:
            self.config_loading.setVisible(False)
            try:
                if hasattr(self, '_config_refresh_button'):
                    self._config_refresh_button.setEnabled(True)
            except Exception:
                pass
        except Exception:
            pass

        # Populate config tab with all parameters (full reload only after device loaded)
        self._populate_module_settings(fields, src)
        # Clear pending writes that are now reflected by the device
        try:
            # Walk pending writes list and remove if device now reports same value
            dev = self.serThread.elrs_devices.get(src, {})
            dev_fields = dev.get('fields', {})
            to_clear = []
            for pfid, (pval, ts) in list(self._pending_param_writes.items()):
                fld = dev_fields.get(int(pfid))
                if fld is None:
                    continue
                dev_val = fld.get('value', fld.get('status', None))
                if dev_val == pval:
                    to_clear.append(int(pfid))
            for pfid in to_clear:
                try:
                    del self._pending_param_writes[int(pfid)]
                except Exception:
                    pass
        except Exception:
            pass

    def _on_device_parameters_progress(self, src: int, fetched: int, total: int):
        try:
            if total and total > 0:
                self.config_loading.setRange(0, total)
                self.config_loading.setValue(fetched)
                if fetched < total:
                    self.config_loading.setVisible(True)
                else:
                    self.config_loading.setVisible(False)
            else:
                # unknown total: show busy indicator (indeterminate)
                self.config_loading.setRange(0, 0)
                self.config_loading.setVisible(True)
        except Exception as e:
            self.onDebug(f"_on_device_parameters_progress error: {e}")

    def _on_device_parameter_field_updated(self, src: int, fid: int, field: dict):
        """Update the specific widget for a field when the device returns its value.
        Honors pending writes and does not overwrite user's selection while pending.
        """
        try:
            widget = self._config_field_widgets.get(int(fid))
            if widget is None:
                return
            # Check for pending write
            pending = self._pending_param_writes.get(int(fid))
            if pending:
                desired, ts = pending
                dev_value = field.get('value', field.get('status', None))
                if dev_value == desired:
                    try:
                        if isinstance(widget, QtWidgets.QComboBox):
                            ftype = field.get('type', -1)
                            widget.blockSignals(True)
                            # For numeric types (0-8), dev_value is the actual value, need to find index
                            if 0 <= ftype <= 8:
                                # Search combo items to find matching value
                                target_idx = 0
                                for i in range(widget.count()):
                                    item_text = widget.itemText(i)
                                    # Extract numeric value from text (strip unit if present)
                                    try:
                                        numeric_part = ''.join(c for c in item_text if c.isdigit() or c == '-')
                                        if numeric_part and int(numeric_part) == int(dev_value):
                                            target_idx = i
                                            break
                                    except Exception:
                                        pass
                                widget.setCurrentIndex(target_idx)
                            else:
                                # For selection type (9), dev_value is the index
                                widget.setCurrentIndex(int(dev_value))
                            widget.blockSignals(False)
                        elif isinstance(widget, QtWidgets.QSpinBox):
                            try:
                                widget.blockSignals(True)
                                widget.setValue(int(dev_value))
                                widget.blockSignals(False)
                            except Exception:
                                widget.setValue(int(dev_value))
                        elif isinstance(widget, QtWidgets.QLabel) or isinstance(widget, QtWidgets.QPushButton):
                            text_val = field.get('info', field.get('value', ''))
                            widget.setText(str(text_val))
                    except Exception:
                        pass
                    try:
                        del self._pending_param_writes[int(fid)]
                    except Exception:
                        pass
                else:
                    if time.time() - ts > 5.0:
                        try:
                            del self._pending_param_writes[int(fid)]
                        except Exception:
                            pass
                    return
            else:
                dev_value = field.get('value', field.get('status', None))
                try:
                    if isinstance(widget, QtWidgets.QComboBox) and dev_value is not None:
                        ftype = field.get('type', -1)
                        widget.blockSignals(True)
                        # For numeric types (0-8), dev_value is the actual value, need to find index
                        if 0 <= ftype <= 8:
                            # Search combo items to find matching value
                            target_idx = 0
                            for i in range(widget.count()):
                                item_text = widget.itemText(i)
                                # Extract numeric value from text (strip unit if present)
                                try:
                                    numeric_part = ''.join(c for c in item_text if c.isdigit() or c == '-')
                                    if numeric_part and int(numeric_part) == int(dev_value):
                                        target_idx = i
                                        break
                                except Exception:
                                    pass
                            widget.setCurrentIndex(target_idx)
                        else:
                            # For selection type (9), dev_value is the index
                            widget.setCurrentIndex(int(dev_value))
                        widget.blockSignals(False)
                    elif isinstance(widget, QtWidgets.QSpinBox) and dev_value is not None:
                        try:
                            widget.blockSignals(True)
                            widget.setValue(int(dev_value))
                            widget.blockSignals(False)
                        except Exception:
                            widget.setValue(int(dev_value))
                    elif isinstance(widget, QtWidgets.QLabel) or isinstance(widget, QtWidgets.QPushButton):
                        text_val = field.get('info', field.get('value', ''))
                        widget.setText(str(text_val))
                except Exception:
                    pass
        except Exception as e:
            self.onDebug(f"_on_device_parameter_field_updated error: {e}")

    def _populate_module_settings(self, fields, src):
        # Clear existing widgets in module settings tab
        module_settings = self.tabs.widget(1)  # Module Settings tab
        if module_settings.layout():
            layout = module_settings.layout()
            # Clear all widgets from the existing layout
            # some widgets in the module settings tab are permanent (like the loading bar)
            # We'll skip deleting them here and re-insert afterwards
            keep_loading_widget = None
            while layout.count():
                child = layout.takeAt(0)
                if child.widget():
                    w = child.widget()
                    try:
                        # Don't delete the global config_loading widget; keep it and re-add later
                        if hasattr(self, 'config_loading') and w is self.config_loading:
                            # Remove from layout now but save to re-add
                            w.setParent(None)
                            keep_loading_widget = w
                        else:
                            w.setParent(None)
                            w.deleteLater()
                    except Exception:
                        pass
                elif child.layout():
                    # Recursively delete potentially nested layout widgets
                    sub = child.layout()
                    while sub.count():
                        subchild = sub.takeAt(0)
                        if subchild.widget():
                            w = subchild.widget()
                            try:
                                if hasattr(self, 'config_loading') and w is self.config_loading:
                                    w.setParent(None)
                                    keep_loading_widget = w
                                else:
                                    w.setParent(None)
                                    w.deleteLater()
                            except Exception:
                                pass
            # Reinsert the loading widget at top if we preserved it
            if keep_loading_widget is not None:
                try:
                    layout.insertWidget(0, keep_loading_widget)
                except Exception:
                    try:
                        layout.addWidget(keep_loading_widget)
                    except Exception:
                        pass

        # Reuse existing layout if present, otherwise create new
        layout = module_settings.layout()
        if layout is None:
            layout = QtWidgets.QVBoxLayout(module_settings)
            module_settings.setLayout(layout)

        # Reset widget mapping for this repopulation
        try:
            self._config_field_widgets.clear()
        except Exception:
            pass
        # Create scrollable area
        # Remove any existing scroll area if present
        # (look for first QScrollArea child and delete it to avoid duplicates)
        for child in module_settings.findChildren(QtWidgets.QScrollArea):
            try:
                child.setParent(None)
                child.deleteLater()
            except Exception:
                pass

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QtWidgets.QWidget()
        widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        inner_layout = QtWidgets.QVBoxLayout(widget)
        # Add a small toolbar with a Refresh button at the top of the module settings tab
        toolbar = QtWidgets.QHBoxLayout()
        refresh_btn = QtWidgets.QPushButton("Refresh")
        refresh_btn.setMaximumWidth(120)
        toolbar.addWidget(refresh_btn)
        toolbar.addStretch()
        # insert toolbar at top of root layout
        try:
            layout.addLayout(toolbar)
        except Exception:
            pass
        def _refresh_clicked():
            try:
                dev = self.current_device_id if self.current_device_id in self.serThread.elrs_devices else (list(self.serThread.elrs_devices.keys())[0] if self.serThread.elrs_devices else None)
                if dev is not None:
                    fields = self.serThread.elrs_devices[dev].get('fields', {})
                    loaded = self.serThread.elrs_devices[dev].get('loaded', False)
                    # Always show loading indicator when user clicks refresh
                    try:
                        self.config_loading.setVisible(True)
                    except Exception:
                        pass
                    try:
                        if hasattr(self, '_config_refresh_button'):
                            self._config_refresh_button.setEnabled(False)
                    except Exception:
                        pass
                    # Trigger a full device reload regardless of loaded flag
                    try:
                        self.serThread.request_device_reload(dev)
                    except Exception as e:
                        self.onDebug(f"Refresh reload request failed: {e}")
            except Exception as e:
                self.onDebug(f"Refresh error: {e}")
        refresh_btn.clicked.connect(_refresh_clicked)
        # remember the refresh button for toggling
        try:
            self._config_refresh_button = refresh_btn
        except Exception:
            pass
        # Expose refresh function for testing and external calls
        try:
            self._refresh_clicked = _refresh_clicked
        except Exception:
            pass

        # Build map of group layouts for parents (store groupbox and layout)
        group_layouts = {}
        # Precompute which field ids are used as parents so we can avoid adding
        # duplicate QLabel entries when a folder is represented by a QGroupBox.
        parents_set = set()
        for ff in fields.values():
            try:
                p = ff.get('parent', 0)
                if p:
                    try:
                        parents_set.add(int(p))
                    except Exception:
                        parents_set.add(p)
            except Exception:
                pass
        for fid, field in fields.items():
            try:
                name = field.get('name', '')
                # include Packet Rate in the module settings tab like any other field
                ftype = field.get('type', 0)
                parent = field.get('parent', 0)
                # determine which layout to add to (parent grouping)
                target_layout = inner_layout
                if parent and parent in group_layouts:
                    target_layout = group_layouts[parent][1]
                elif parent and parent not in group_layouts:
                    # create a new group box placeholder for this parent and add it to root
                    parent_name = fields.get(parent, {}).get('name', f'Folder {parent}')
                    group_box = QtWidgets.QGroupBox(str(parent_name))
                    group_layout = QtWidgets.QVBoxLayout(group_box)
                    inner_layout.addWidget(group_box)
                    group_layouts[parent] = (group_box, group_layout)
                    target_layout = group_layout

                if ftype == 9:  # select/choice
                    row_layout = QtWidgets.QHBoxLayout()
                    label = QtWidgets.QLabel(f"{name}:")
                    combo = NoWheelComboBox()
                    values = field.get('values', [])
                    # If this field has a mapped unit and values are plain numeric strings,
                    # display them with the unit suffix in the UI (but keep the underlying indices the same).
                    try:
                        unit = UNIT_MAP.get(name.strip().lower())
                        display_values = []
                        for v in values:
                            if unit and isinstance(v, str) and re.search(r'[A-Za-z%]', v) is None:
                                # Append unit without whitespace (e.g., 10mW)
                                display_values.append(f"{v}{unit}")
                            else:
                                display_values.append(v)
                        combo.addItems(display_values)
                    except Exception:
                        combo.addItems(values)
                    # prefer explicit selection index if present
                    sel_idx = field.get('value', field.get('status', 0))
                    try:
                        if isinstance(sel_idx, str):
                            sel_idx = int(sel_idx)
                    except Exception:
                        sel_idx = 0
                    # Honor any pending write for this field - prefer the user's desired value
                    pending = self._pending_param_writes.get(int(fid)) if hasattr(self, '_pending_param_writes') else None
                    if pending:
                        desired, ts = pending
                        try:
                            if 0 <= int(desired) < len(values):
                                combo.blockSignals(True)
                                combo.setCurrentIndex(int(desired))
                                combo.blockSignals(False)
                        except Exception:
                            pass
                    elif 0 <= sel_idx < len(values):
                        combo.setCurrentIndex(sel_idx)
                    combo.currentIndexChanged.connect(lambda idx, f=fid: self._on_param_changed(f, idx))
                    # Save widget reference for targeted updates
                    try:
                        self._config_field_widgets[int(fid)] = combo
                    except Exception:
                        pass
                    row_layout.addWidget(label)
                    row_layout.addWidget(combo)
                    row_layout.addStretch()
                    target_layout.addLayout(row_layout)
                elif ftype == 11:  # info/label
                    # If this field is used as a folder parent (it owns a groupbox),
                    # skip adding an extra QLabel inside the group box since the
                    # QGroupBox already shows the title. We still continue to
                    # process this field so the group title gets updated below.
                    if int(fid) in parents_set:
                        # do not add a duplicate label for a folder header
                        pass
                    else:
                        label = QtWidgets.QLabel(f"{name}")
                        target_layout.addWidget(label)
                elif 0 <= ftype <= 8:  # numeric value
                    row_layout = QtWidgets.QHBoxLayout()
                    label = QtWidgets.QLabel(f"{name}:")
                    combo = NoWheelComboBox()
                    # Use parsed min/max/step if present
                    minv = field.get('min') if field.get('min') is not None else 0
                    maxv = field.get('max') if field.get('max') is not None else (minv + 100)
                    stepv = field.get('step') if field.get('step') is not None else 1

                    # Get unit for display
                    unit = None
                    try:
                        unit = UNIT_MAP.get(name.strip().lower())
                    except Exception:
                        pass

                    # Generate combo values from min, max, and step
                    try:
                        minv = int(minv)
                        maxv = int(maxv)
                        stepv = int(stepv) if stepv > 0 else 1
                        # Limit number of items to prevent UI issues with huge ranges
                        max_items = 1000
                        num_items = (maxv - minv) // stepv + 1
                        if num_items > max_items:
                            # If too many items, adjust step to fit within limit
                            stepv = max(1, (maxv - minv) // (max_items - 1))

                        values = []
                        value_map = {}  # Map display string to actual value
                        idx = 0
                        for val in range(minv, maxv + 1, stepv):
                            if unit:
                                display_str = f"{val}{unit}"
                            else:
                                display_str = str(val)
                            values.append(display_str)
                            value_map[idx] = val
                            idx += 1

                        combo.addItems(values)
                    except Exception as e:
                        self.onDebug(f"Error generating numeric combo values: {e}")
                        # Fallback: just add min and max
                        values = [str(minv), str(maxv)]
                        combo.addItems(values)
                        value_map = {0: minv, 1: maxv}

                    # Set current value
                    dev_value = int(field.get('value', field.get('status', field.get('default', minv if minv is not None else 0))))
                    # honor pending write
                    pending = self._pending_param_writes.get(int(fid)) if hasattr(self, '_pending_param_writes') else None
                    if pending:
                        try:
                            val, ts = pending
                            dev_value = int(val)
                        except Exception:
                            pass

                    # Find the index that matches dev_value
                    try:
                        # Find which index in value_map corresponds to dev_value
                        target_idx = 0
                        for idx, val in value_map.items():
                            if val == dev_value:
                                target_idx = idx
                                break
                        combo.blockSignals(True)
                        combo.setCurrentIndex(target_idx)
                        combo.blockSignals(False)
                    except Exception:
                        pass

                    # Connect on-change to directly call _on_param_changed
                    def _on_combo_changed(idx, f=fid, vmap=value_map):
                        try:
                            actual_value = vmap.get(idx, minv)
                            self._on_param_changed(f, actual_value)
                        except Exception as e:
                            self.onDebug(f"Numeric combo change error: {e}")

                    combo.currentIndexChanged.connect(_on_combo_changed)

                    # Save widget reference for targeted updates
                    try:
                        self._config_field_widgets[int(fid)] = combo
                    except Exception:
                        pass

                    row_layout.addWidget(label)
                    row_layout.addWidget(combo)
                    row_layout.addStretch()
                    target_layout.addLayout(row_layout)
                elif ftype == 12:  # string info (read-only)
                    value = field.get('value', '')
                    label = QtWidgets.QLabel(f"{name}: {value}")
                    # Allow strings to be editable via context menu / popup (future)
                    target_layout.addWidget(label)
                    try:
                        self._config_field_widgets[int(fid)] = label
                    except Exception:
                        pass
                elif ftype == 13:  # command/button
                    button = QtWidgets.QPushButton(f"{name}")
                    button.clicked.connect(lambda checked, f=fid: self._on_param_changed(f, 1))  # Assume toggle or something
                    target_layout.addWidget(button)
                    try:
                        self._config_field_widgets[int(fid)] = button
                    except Exception:
                        pass
                else:
                    label = QtWidgets.QLabel(f"{name} (type {ftype})")
                    target_layout.addWidget(label)
                    try:
                        self._config_field_widgets[int(fid)] = label
                    except Exception:
                        pass
            except Exception as e:
                self.onDebug(f"Error adding field {fid}: {e}")
            # If this field is a folder that had a placeholder group, update its name
            try:
                if fid in group_layouts:
                    # group_layouts[fid] = (group_box, layout)
                    group_layouts[fid][0].setTitle(str(field.get('name', f'Folder {fid}')))
            except Exception:
                pass

        inner_layout.addStretch()

        scroll.setWidget(widget)
        layout.addWidget(scroll)
        # Only set current_device_id for TX modules, not receivers
        if src in (CRSF_ADDRESS_CRSF_TRANSMITTER, CRSF_ADDRESS_TRANSMITTER_LEGACY):
            self.current_device_id = src
        module_settings.update()
        self.tabs.update()
        self.tabs.repaint()
        widget.update()
        scroll.update()

# -------------------------------------------------------------------

def create_arrow_icon(direction='down', color='#e0e0e0', size=16):
    """Create an arrow icon for combo boxes and spin boxes.

    Args:
        direction: 'up' or 'down'
        color: Arrow color
        size: Icon size in pixels

    Returns:
        QIcon with the arrow
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # Create arrow polygon
    if direction == 'down':
        points = [
            QPoint(size // 4, size // 3),
            QPoint(3 * size // 4, size // 3),
            QPoint(size // 2, 2 * size // 3)
        ]
    else:  # up
        points = [
            QPoint(size // 4, 2 * size // 3),
            QPoint(3 * size // 4, 2 * size // 3),
            QPoint(size // 2, size // 3)
        ]

    polygon = QPolygon(points)
    painter.setBrush(QColor(color))
    painter.setPen(QtCore.Qt.NoPen)
    painter.drawPolygon(polygon)
    painter.end()

    return QIcon(pixmap)


def create_checkmark_icon(color='#ffffff', size=16):
    """Create a checkmark icon for checkboxes.

    Args:
        color: Checkmark color
        size: Icon size in pixels

    Returns:
        QIcon with the checkmark
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    pen = painter.pen()
    pen.setColor(QColor(color))
    pen.setWidth(2)
    painter.setPen(pen)

    # Draw checkmark
    points = [
        QPoint(size // 4, size // 2),
        QPoint(size // 2 - 1, 3 * size // 4),
        QPoint(3 * size // 4, size // 4)
    ]

    painter.drawPolyline(QPolygon(points))
    painter.end()

    return QIcon(pixmap)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)

    # Create temporary directory for icons
    temp_dir = tempfile.mkdtemp()

    # Create and save arrow icons
    down_arrow = create_arrow_icon('down', '#e0e0e0', 12)
    up_arrow = create_arrow_icon('up', '#e0e0e0', 12)
    down_arrow_disabled = create_arrow_icon('down', '#555555', 12)
    up_arrow_disabled = create_arrow_icon('up', '#555555', 12)
    checkmark = create_checkmark_icon('#ffffff', 14)
    checkmark_disabled = create_checkmark_icon('#555555', 14)

    down_arrow_path = os.path.join(temp_dir, 'down_arrow.png').replace('\\', '/')
    up_arrow_path = os.path.join(temp_dir, 'up_arrow.png').replace('\\', '/')
    down_arrow_disabled_path = os.path.join(temp_dir, 'down_arrow_disabled.png').replace('\\', '/')
    up_arrow_disabled_path = os.path.join(temp_dir, 'up_arrow_disabled.png').replace('\\', '/')
    checkmark_path = os.path.join(temp_dir, 'checkmark.png').replace('\\', '/')
    checkmark_disabled_path = os.path.join(temp_dir, 'checkmark_disabled.png').replace('\\', '/')

    down_arrow.pixmap(12, 12).save(down_arrow_path)
    up_arrow.pixmap(12, 12).save(up_arrow_path)
    down_arrow_disabled.pixmap(12, 12).save(down_arrow_disabled_path)
    up_arrow_disabled.pixmap(12, 12).save(up_arrow_disabled_path)
    checkmark.pixmap(14, 14).save(checkmark_path)
    checkmark_disabled.pixmap(14, 14).save(checkmark_disabled_path)

    # Apply dark theme
    dark_stylesheet = """
    QWidget {{
        background-color: #2b2b2b;
        color: #e0e0e0;
        font-family: Segoe UI, Arial, sans-serif;
    }}

    QMainWindow, QDialog {{
        background-color: #2b2b2b;
    }}

    QLabel {{
        color: #e0e0e0;
        background-color: transparent;
    }}

    QPushButton {{
        background-color: #3c3c3c;
        color: #e0e0e0;
        border: 1px solid #555555;
        border-radius: 4px;
        padding: 2px 8px;
        min-height: 20px;
        font-size: 8pt;
    }}

    QPushButton:hover {{
        background-color: #4a4a4a;
        border: 1px solid #666666;
    }}

    QPushButton:pressed {{
        background-color: #2a2a2a;
    }}

    QPushButton:disabled {{
        background-color: #2b2b2b;
        color: #666666;
        border: 1px solid #3c3c3c;
    }}

    QLineEdit, QSpinBox, QDoubleSpinBox {{
        background-color: #3c3c3c;
        color: #e0e0e0;
        border: 1px solid #555555;
        border-radius: 3px;
        padding: 3px;
        selection-background-color: #0d47a1;
    }}

    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid #1e88e5;
    }}

    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
        background-color: #2b2b2b;
        color: #666666;
    }}

    QComboBox {{
        background-color: #3c3c3c;
        color: #e0e0e0;
        border: 1px solid #555555;
        border-radius: 3px;
        padding: 1px 5px;
        min-height: 20px;
    }}

    QComboBox:hover {{
        border: 1px solid #666666;
    }}

    QComboBox:disabled {{
        background-color: #2b2b2b;
        color: #666666;
    }}

    QComboBox::drop-down {{
        subcontrol-origin: padding;
        subcontrol-position: top right;
        width: 20px;
        border-left: 1px solid #555555;
        background-color: #3c3c3c;
        border-top-right-radius: 3px;
        border-bottom-right-radius: 3px;
    }}

    QComboBox::down-arrow {{
        image: url({down_arrow_path});
        width: 12px;
        height: 12px;
    }}

    QComboBox::down-arrow:disabled {{
        image: url({down_arrow_disabled_path});
    }}

    QComboBox QAbstractItemView {{
        background-color: #3c3c3c;
        color: #e0e0e0;
        selection-background-color: #0d47a1;
        selection-color: #ffffff;
        border: 1px solid #555555;
    }}

    QCheckBox {{
        color: #e0e0e0;
        spacing: 5px;
    }}

    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 2px solid #555555;
        border-radius: 3px;
        background-color: #3c3c3c;
    }}

    QCheckBox::indicator:hover {{
        border: 2px solid #666666;
    }}

    QCheckBox::indicator:checked {{
        background-color: #1e88e5;
        border: 2px solid #1e88e5;
        image: url({checkmark_path});
    }}

    QCheckBox::indicator:disabled {{
        background-color: #2b2b2b;
        border: 2px solid #3c3c3c;
    }}

    QCheckBox::indicator:checked:disabled {{
        background-color: #2b2b2b;
        border: 2px solid #3c3c3c;
        image: url({checkmark_disabled_path});
    }}

    QProgressBar {{
        background-color: #3c3c3c;
        border: 1px solid #555555;
        border-radius: 3px;
        text-align: center;
        color: #e0e0e0;
    }}

    QProgressBar::chunk {{
        background-color: #1e88e5;
        border-radius: 2px;
    }}

    QGroupBox {{
        color: #e0e0e0;
        border: 1px solid #555555;
        border-radius: 5px;
        margin-top: 10px;
        padding-top: 10px;
        font-weight: bold;
    }}

    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 10px;
        padding: 0 5px;
        background-color: #2b2b2b;
    }}

    QTabWidget::pane {{
        border: 1px solid #555555;
        background-color: #2b2b2b;
        border-radius: 3px;
    }}

    QTabBar::tab {{
        background-color: #3c3c3c;
        color: #e0e0e0;
        border: 1px solid #555555;
        border-bottom: none;
        padding: 8px 20px;
        margin-right: 2px;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
    }}

    QTabBar::tab:selected {{
        background-color: #2b2b2b;
        border-bottom: 2px solid #1e88e5;
    }}

    QTabBar::tab:hover:!selected {{
        background-color: #4a4a4a;
    }}

    QTabBar::tab:disabled {{
        background-color: #2b2b2b;
        color: #666666;
        border: 1px solid #3c3c3c;
    }}

    QPlainTextEdit, QTextEdit {{
        background-color: #1e1e1e;
        color: #e0e0e0;
        border: 1px solid #555555;
        border-radius: 3px;
        selection-background-color: #0d47a1;
    }}

    QScrollArea {{
        background-color: #2b2b2b;
        border: none;
    }}

    QScrollBar:vertical {{
        background-color: #222222;
        width: 10px;
        border: none;
        margin: 0px;
    }}

    QScrollBar::handle:vertical {{
        background-color: #4a4a4a;
        border-radius: 5px;
        min-height: 30px;
        margin: 2px 2px 2px 2px;
    }}

    QScrollBar::handle:vertical:hover {{
        background-color: #5a5a5a;
    }}

    QScrollBar::handle:vertical:pressed {{
        background-color: #6a6a6a;
    }}

    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
        border: none;
        background: none;
    }}

    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: none;
    }}

    QScrollBar:horizontal {{
        background-color: #222222;
        height: 10px;
        border: none;
        margin: 0px;
    }}

    QScrollBar::handle:horizontal {{
        background-color: #4a4a4a;
        border-radius: 5px;
        min-width: 30px;
        margin: 2px 2px 2px 2px;
    }}

    QScrollBar::handle:horizontal:hover {{
        background-color: #5a5a5a;
    }}

    QScrollBar::handle:horizontal:pressed {{
        background-color: #6a6a6a;
    }}

    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0px;
        border: none;
        background: none;
    }}

    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
        background: none;
    }}

    QFrame[frameShape="4"], QFrame[frameShape="5"] {{
        color: #555555;
    }}

    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        background-color: #3c3c3c;
        border-left: 1px solid #555555;
        width: 16px;
    }}

    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover {{
        background-color: #4a4a4a;
    }}

    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        background-color: #3c3c3c;
        border-left: 1px solid #555555;
        width: 16px;
    }}

    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background-color: #4a4a4a;
    }}

    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: url({up_arrow_path});
        width: 12px;
        height: 12px;
    }}

    QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{
        image: url({up_arrow_disabled_path});
    }}

    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: url({down_arrow_path});
        width: 12px;
        height: 12px;
    }}

    QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
        image: url({down_arrow_disabled_path});
    }}
    """.format(down_arrow_path=down_arrow_path, up_arrow_path=up_arrow_path,
               down_arrow_disabled_path=down_arrow_disabled_path, up_arrow_disabled_path=up_arrow_disabled_path,
               checkmark_path=checkmark_path, checkmark_disabled_path=checkmark_disabled_path)

    app.setStyleSheet(dark_stylesheet)

    w = Main()
    w.show()

    exit_code = app.exec_()

    # Clean up temporary files
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

    sys.exit(exit_code)
