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
from serial_interface import SerialInterface, get_available_ports
from joystick_handler import JoystickHandler
from channel_ui import ChannelRow, SRC_CHOICES
from config_manager import ConfigManager, DEFAULT_BAUD, CHANNELS, DEFAULT_CFG
from version import VERSION, GIT_SHA
from crsf_state_machine import CRSFStateMachine
from crsf_protocol import rf_mode_name, tx_power_name, CRSF_ADDRESS_MODULE
from lua_state_machine import LuaStateMachine
from device_parameters import (
    PARAM_TYPE_UINT8, PARAM_TYPE_INT8, PARAM_TYPE_TEXT_SELECTION,
    PARAM_TYPE_STRING, PARAM_TYPE_FOLDER, PARAM_TYPE_INFO, PARAM_TYPE_COMMAND,
)

GUI_UPDATE_INTERVAL_S = 1.0 / 60  # ~60 Hz redraws
CSV_LOG_INTERVAL_S    = 0.1       # 10 Hz CSV logging


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

        # Throttle timestamps for the split-tick design
        self._last_gui_update = 0.0

        # CSV logging setup
        self.csv_filename = None
        self.csv_buffer = []  # Buffer for CSV rows (max 600 lines)
        self.csv_last_write_time = 0.0  # Track last write time for 10Hz throttling
        self.csv_start_time = None  # Track when logging started for filename
        self.last_log_time = 0.0  # Track last log time for throttling CSV log calls
        self.csv_latest_telemetry = {}  # Store latest telemetry data for CSV logging
        # Fieldnames: timestamp, channels 1-16, then link stats
        self.csv_fieldnames = ['timestamp'] + [f'CH{i+1}' for i in range(CHANNELS)] + ['1RSS', '2RSS', 'LQ', 'RSNR', 'RFMD', 'TPWR', 'TRSS', 'TLQ', 'TSNR']

        layout = QtWidgets.QVBoxLayout(self)

        # Mapping state (for joystick-to-channel learn)
        self.mapping_row = None
        self.mapping_baseline = ([], [])
        self.mapping_started_at = 0.0

        # Latest joystick data for mapping mode
        self.latest_axes = []
        self.latest_buttons = []

        # Joystick (auto-scanning) - called from main tick() at 500Hz
        self.joy = JoystickHandler()
        # Connect joystick status
        self.joy.status.connect(self.onDebug)
        self.joy.status.connect(self.onJoyStatus)

        # Serial interface - manages port connection
        self.serThread = SerialInterface(self.cfg["serial_port"], DEFAULT_BAUD)
        self.thread = threading.Thread(target=self.serThread.run, daemon=True)
        self.thread.start()
        self.serThread.debug.connect(self.onDebug)
        self.serThread.connection_status.connect(self.onConnectionStatus)

        # CRSF state machine - handles frame transmission and reception
        self.crsf_state_machine = CRSFStateMachine()
        self.crsf_state_machine.set_serial(self.serThread)
        self.crsf_state_machine.set_link_stats_callback(self.onLinkStats)

        # LUA state machine - ELRS parameter discovery and editing
        self.lua = LuaStateMachine(send=self.crsf_state_machine.queue_frame)
        self.lua.on_devices_changed  = self._lua_on_devices
        self.lua.on_loading_progress = self._lua_on_progress
        self.lua.on_loading_complete = self._lua_on_complete
        self.lua.on_loading_aborted  = self._lua_on_aborted
        self.lua.on_field_updated    = self._lua_on_field
        self.lua.on_debug            = self.onDebug
        self.lua.on_elrs_error       = self._lua_on_elrs_error
        self.lua.on_elrs_confirm     = self._lua_on_elrs_confirm
        self.lua.on_elrs_status      = self._lua_on_elrs_status_update
        self._lua_confirm_param_num  = None
        self.crsf_state_machine.set_lua_callback(self.lua.handle_frame)
        self.crsf_state_machine.set_lua_tick_callback(self.lua.tick)

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
        for key in ["LQ","TLQ","1RSS","2RSS","TRSS","RSNR","TSNR","RFMD","TPWR"]:
            box = QtWidgets.QGroupBox(key)
            lab = QtWidgets.QLabel("--")
            lab.setAlignment(QtCore.Qt.AlignCenter | QtCore.Qt.AlignVCenter)
            f = lab.font(); f.setPointSize(11); lab.setFont(f)
            v = QtWidgets.QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 10)
            v.addWidget(lab, alignment=QtCore.Qt.AlignCenter)
            tel.addWidget(box)
            self.telLabels[key] = lab

        # Initially set link stats labels to grey (no data)
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

        # If the saved port wasn't found (stale config), sync the serial thread
        # to whatever port the combo defaulted to.
        current_port = self.portCombo.currentData()
        if current_port and current_port != self.cfg["serial_port"]:
            # Defer until after the GUI is fully constructed so that
            # jrBayStatusLabel exists when onConnectionStatus fires.
            _port_text = self.portCombo.currentText()
            QtCore.QTimer.singleShot(0, lambda: self._on_port_changed(_port_text))

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

        # Parameters tab
        params_tab = self._create_config_tab()
        self.tabs.addTab(params_tab, "Parameters")

        self._params_tab_first_visit = True
        self._selected_card = None
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.tabs.setCurrentIndex(0)
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setContentsMargins(0, 4, 0, 4)
        top_bar.addWidget(self.joyStatusLabel)

        divider1 = QtWidgets.QFrame()
        divider1.setFrameShape(QtWidgets.QFrame.VLine)
        divider1.setFrameShadow(QtWidgets.QFrame.Sunken)
        divider1.setLineWidth(2)
        top_bar.addWidget(divider1)

        top_bar.addWidget(QtWidgets.QLabel("TitanLRS COM Port:"))
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

        # LUA notification banner — used for both errors and command confirmations
        self._lua_error_banner = QtWidgets.QFrame()
        _banner_layout = QtWidgets.QHBoxLayout(self._lua_error_banner)
        _banner_layout.setContentsMargins(10, 5, 10, 5)
        self._lua_error_label = QtWidgets.QLabel()
        self._lua_error_label.setStyleSheet(
            "color: #ffffff; font-weight: bold; border: none; background: transparent;"
        )
        self._lua_error_label.setWordWrap(True)
        _banner_btn_style = (
            "QPushButton { background-color: #ffffff; color: #000000; border: none;"
            " border-radius: 3px; padding: 2px 8px; font-weight: bold; }"
            " QPushButton:hover { background-color: #e0e0e0; }"
            " QPushButton:pressed { background-color: #bdbdbd; }"
        )
        self._lua_cancel_btn = QtWidgets.QPushButton("Cancel")
        self._lua_cancel_btn.setFixedWidth(68)
        self._lua_cancel_btn.setStyleSheet(_banner_btn_style)
        self._lua_cancel_btn.clicked.connect(self._lua_confirm_cancel)
        self._lua_ok_btn = QtWidgets.QPushButton("OK")
        self._lua_ok_btn.setFixedWidth(60)
        self._lua_ok_btn.setStyleSheet(_banner_btn_style)
        self._lua_ok_btn.clicked.connect(self._lua_banner_ok)
        _banner_layout.addWidget(self._lua_error_label, 1)
        _banner_layout.addWidget(self._lua_cancel_btn)
        _banner_layout.addWidget(self._lua_ok_btn)
        self._lua_error_banner.setVisible(False)
        self._lua_set_banner_mode('error')  # sets stylesheet + Cancel visibility
        layout.addWidget(self._lua_error_banner)

        layout.addLayout(tel)
        layout.addWidget(self.console_container)
        layout.setStretch(2, 1)

        # Pass channel rows and toggle group enforcer to JoystickHandler
        self.joy.set_channel_rows(self.rows)
        self.joy.set_toggle_group_enforcer(self._enforce_toggle_groups)

        # Drive tick at 1 ms so joystick sampling stays at true 1 kHz
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1)

        # Schedule initial connection attempt after GUI is shown
        def attempt_initial_connection():
            self.serThread._connect()
            self.serThread._initial_connect_attempted = True
        QtCore.QTimer.singleShot(500, attempt_initial_connection)

    def onConnectionStatus(self, is_connected):
        """Update status indicator based on actual connection state"""
        if is_connected:
            # Serial port connected
            try:
                self.jrBayStatusLabel.setText("Connected")
                self.jrBayStatusLabel.setStyleSheet("color: white; font-weight: bold;")
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
                self.lua.reset()
                self._lua_on_devices([])
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

    def onLinkStats(self, stats: dict):
        """Update UI and CSV logging with received link statistics.
        
        Args:
            stats: Parsed link statistics dictionary from CRSF protocol
        """
        try:
            # Map CRSF link stats fields to UI labels
            # uplink_rssi_1 -> 1RSS, uplink_rssi_2 -> 2RSS
            # uplink_link_quality -> LQ, uplink_snr -> RSNR
            # rf_mode -> RFMD, uplink_tx_power -> TPWR
            # downlink_rssi -> TRSS, downlink_link_quality -> TLQ, downlink_snr -> TSNR
            
            mapping = {
                '1RSS': stats.get('uplink_rssi_1', 0),    # Already signed dBm
                '2RSS': stats.get('uplink_rssi_2', 0),    # Already signed dBm
                'LQ': stats.get('uplink_link_quality', 0),
                'RSNR': stats.get('uplink_snr', 0),
                'RFMD': stats.get('rf_mode', 0),
                'TPWR': stats.get('uplink_tx_power', 0),
                'TRSS': stats.get('downlink_rssi', 0),    # Already signed dBm
                'TLQ': stats.get('downlink_link_quality', 0),
                'TSNR': stats.get('downlink_snr', 0),
            }
            
            # Update UI labels
            for key, value in mapping.items():
                if key in self.telLabels:
                    label = self.telLabels[key]
                    # Format based on field type
                    if 'RSS' in key:
                        label.setText(f"{value} dBm")
                    elif 'SNR' in key:
                        label.setText(f"{value} dB")
                    elif key in ('LQ', 'TLQ'):
                        label.setText(f"{value}%")
                    elif key == 'RFMD':
                        label.setText(rf_mode_name(value))
                    elif key == 'TPWR':
                        label.setText(tx_power_name(value))
                    else:
                        label.setText(str(value))
                    
                    # Set color to white (data received)
                    label.setStyleSheet("color: white;")
            
            # Store for CSV logging
            self.csv_latest_telemetry = mapping
            
        except Exception as e:
            self.onDebug(f"Error updating link stats: {e}")


    def _update_gui(self, ch, axes, btns, joystick_connected):
        """Update GUI elements with computed channel values.

        Args:
            ch: List of 16 computed channel values
            axes: List of joystick axis values
            btns: List of joystick button values
            joystick_connected: Boolean indicating if joystick is connected
        """
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

    def tick(self):
        """
        Main update loop driven by QTimer(0) — fires as fast as the Qt event
        queue allows.

        Fast path (every tick): joystick sample, failsafe wiring, CRSF state
        machine (drains RX, emits due frames).

        Throttled paths: GUI redraws at ~60 Hz; CSV logging at 10 Hz.
        """
        now = time.monotonic()

        # ---- Always: latency-sensitive serial + joystick path ----
        axes, btns = self.joy.read()
        self.latest_axes = axes
        self.latest_buttons = btns
        joystick_connected = self.joy.j is not None

        ch = self.joy.compute_channels(axes, btns)

        if joystick_connected:
            self.crsf_state_machine.update_channels(ch, channels_are_1000_2000=True)
            self.crsf_state_machine.set_failsafe(False)
        else:
            self.crsf_state_machine.set_failsafe(True)

        self.crsf_state_machine.run()

        # ---- Throttled: GUI redraws (~60 Hz) ----
        if now - self._last_gui_update >= GUI_UPDATE_INTERVAL_S:
            self._last_gui_update = now
            self._update_gui(ch, axes, btns, joystick_connected)
            self._service_mapping(now, axes, btns)

        # ---- Throttled: CSV logging (10 Hz) ----
        if (self.logging_enabled.isChecked()
                and now - self.last_log_time >= CSV_LOG_INTERVAL_S):
            self.last_log_time = now
            self._log_to_csv(ch)

    def _service_mapping(self, now: float, axes, btns):
        """Service the joystick-to-channel mapping flow (called at ~60 Hz)."""
        if self.mapping_row is None:
            return
        try:
            if now - self.mapping_started_at > 5.0:
                try:
                    self.mapping_row.mapBtn.setText("Map")
                    self.mapping_row.mapBtn.setEnabled(True)
                except Exception:
                    pass
                self.onDebug("Mapping timed out; try again.")
                self.mapping_row = None
                return

            base_axes, base_btns = self.mapping_baseline
            detected = None
            if btns and base_btns:
                for i in range(min(len(btns), len(base_btns))):
                    if base_btns[i] == 0 and btns[i] == 1:
                        detected = ("button", i)
                        break
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
                    try:
                        self.mapping_row.mapBtn.setText("Map")
                        self.mapping_row.mapBtn.setEnabled(True)
                    except Exception:
                        pass
                    self.mapping_row = None
        except Exception as e:
            self.onDebug(f"Mapping error: {e}")
            try:
                if self.mapping_row is not None:
                    self.mapping_row.mapBtn.setText("Map")
                    self.mapping_row.mapBtn.setEnabled(True)
            except Exception:
                pass
            self.mapping_row = None

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

            # Add link stats from latest telemetry (stored by onLinkStats callback)
            link_stats_fields = ['1RSS', '2RSS', 'LQ', 'RSNR', 'RFMD', 'TPWR', 'TRSS', 'TLQ', 'TSNR']
            for field in link_stats_fields:
                # Use stored telemetry data if available
                if field in self.csv_latest_telemetry:
                    row[field] = self.csv_latest_telemetry[field]
                else:
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

    # ------------------------------------------------------------------
    # Configuration tab
    # ------------------------------------------------------------------

    def _create_config_tab(self) -> QtWidgets.QWidget:
        """Build the Parameters tab widget: device list on left, params on right."""
        tab = QtWidgets.QWidget()
        outer = QtWidgets.QHBoxLayout(tab)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # ---- Left panel: Devices ----------------------------------------
        left_panel = QtWidgets.QFrame()
        left_panel.setObjectName("devicesPanel")
        left_panel.setFrameShape(QtWidgets.QFrame.StyledPanel)
        left_panel.setFrameShadow(QtWidgets.QFrame.Raised)
        left_panel.setStyleSheet("""
            QFrame#devicesPanel {
                background-color: #2a2a2a;
                border: 1px solid #4a4a4a;
                border-radius: 6px;
            }
        """)
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.setContentsMargins(6, 6, 6, 6)
        left_layout.setSpacing(4)

        left_header = QtWidgets.QHBoxLayout()
        dev_title = QtWidgets.QLabel("Devices")
        dev_title.setStyleSheet("font-weight: bold; font-size: 11pt; border: none;")
        self._cfg_scan_btn = QtWidgets.QPushButton("Reload")
        self._cfg_scan_btn.setMaximumWidth(72)
        self._cfg_scan_btn.clicked.connect(self._lua_scan)
        left_header.addWidget(dev_title)
        left_header.addStretch()
        left_header.addWidget(self._cfg_scan_btn)
        left_layout.addLayout(left_header)

        left_div = QtWidgets.QFrame()
        left_div.setFrameShape(QtWidgets.QFrame.HLine)
        left_div.setFrameShadow(QtWidgets.QFrame.Sunken)
        left_layout.addWidget(left_div)

        device_scroll = QtWidgets.QScrollArea()
        device_scroll.setWidgetResizable(True)
        device_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self._cfg_device_container = QtWidgets.QWidget()
        self._cfg_device_list_layout = QtWidgets.QVBoxLayout(self._cfg_device_container)
        self._cfg_device_list_layout.setSpacing(4)
        self._cfg_device_list_layout.setContentsMargins(0, 0, 0, 0)
        self._cfg_device_label = QtWidgets.QLabel("Press Reload to discover devices.")
        self._cfg_device_label.setStyleSheet("color: #888888; border: none;")
        self._cfg_device_label.setAlignment(QtCore.Qt.AlignCenter)
        self._cfg_device_list_layout.addWidget(self._cfg_device_label)
        self._cfg_device_list_layout.addStretch()
        device_scroll.setWidget(self._cfg_device_container)
        left_layout.addWidget(device_scroll)

        outer.addWidget(left_panel, 1)

        # ---- Right panel: Parameters ------------------------------------
        right_panel = QtWidgets.QFrame()
        right_panel.setObjectName("paramsPanel")
        right_panel.setFrameShape(QtWidgets.QFrame.StyledPanel)
        right_panel.setFrameShadow(QtWidgets.QFrame.Raised)
        right_panel.setStyleSheet("""
            QFrame#paramsPanel {
                background-color: #2a2a2a;
                border: 1px solid #4a4a4a;
                border-radius: 6px;
            }
        """)
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.setContentsMargins(6, 6, 6, 6)
        right_layout.setSpacing(4)

        right_header = QtWidgets.QHBoxLayout()
        self._cfg_device_name_label = QtWidgets.QLabel("Select a device")
        self._cfg_device_name_label.setStyleSheet("font-weight: bold; font-size: 11pt; border: none;")
        self._cfg_reload_btn = QtWidgets.QPushButton("Reload")
        self._cfg_reload_btn.setMaximumWidth(72)
        self._cfg_reload_btn.setEnabled(False)
        self._cfg_reload_btn.clicked.connect(self._lua_reload)
        right_header.addWidget(self._cfg_device_name_label)
        right_header.addStretch()
        right_header.addWidget(self._cfg_reload_btn)
        right_layout.addLayout(right_header)

        right_div = QtWidgets.QFrame()
        right_div.setFrameShape(QtWidgets.QFrame.HLine)
        right_div.setFrameShadow(QtWidgets.QFrame.Sunken)
        right_layout.addWidget(right_div)

        # Linkstat row: RX connection indicator + bad/good packet counters
        self._cfg_linkstat_row = QtWidgets.QWidget()
        linkstat_layout = QtWidgets.QHBoxLayout(self._cfg_linkstat_row)
        linkstat_layout.setContentsMargins(2, 2, 2, 2)
        linkstat_layout.setSpacing(8)
        self._cfg_rx_status_label = QtWidgets.QLabel("--")
        self._cfg_rx_status_label.setStyleSheet(
            "color: #e0e0e0; background-color: #3c3c3c; border: 1px solid #555555;"
            " border-radius: 3px; padding: 1px 6px;"
        )
        self._cfg_pkt_label = QtWidgets.QLabel("0/0")
        self._cfg_pkt_label.setStyleSheet(
            "color: #e0e0e0; background-color: #3c3c3c; border: 1px solid #555555;"
            " border-radius: 3px; padding: 1px 6px; font-family: monospace;"
        )
        linkstat_layout.addWidget(self._cfg_rx_status_label)
        linkstat_layout.addWidget(self._cfg_pkt_label)
        linkstat_layout.addStretch()
        self._cfg_linkstat_row.setVisible(False)
        right_layout.addWidget(self._cfg_linkstat_row)

        # Stacked: 0 = empty/no selection, 1 = loading, 2 = params
        self._cfg_stack = QtWidgets.QStackedWidget()

        # Pane 0: no device selected
        empty_pane = QtWidgets.QWidget()
        empty_layout = QtWidgets.QVBoxLayout(empty_pane)
        no_device_lbl = QtWidgets.QLabel("No device selected")
        no_device_lbl.setAlignment(QtCore.Qt.AlignCenter)
        no_device_lbl.setStyleSheet("color: #888888; border: none;")
        empty_layout.addStretch()
        empty_layout.addWidget(no_device_lbl)
        empty_layout.addStretch()
        self._cfg_stack.addWidget(empty_pane)

        # Pane 1: loading
        self._cfg_loading_pane = QtWidgets.QWidget()
        loading_layout = QtWidgets.QVBoxLayout(self._cfg_loading_pane)
        self._cfg_loading_label = QtWidgets.QLabel("Loading parameters...")
        self._cfg_loading_label.setAlignment(QtCore.Qt.AlignCenter)
        self._cfg_loading_label.setStyleSheet("border: none;")
        self._cfg_progress = QtWidgets.QProgressBar()
        self._cfg_progress.setRange(0, 100)
        self._cfg_progress.setValue(0)
        loading_layout.addStretch()
        loading_layout.addWidget(self._cfg_loading_label)
        loading_layout.addWidget(self._cfg_progress)
        loading_layout.addStretch()
        self._cfg_stack.addWidget(self._cfg_loading_pane)

        # Pane 2: params
        self._cfg_params_pane = QtWidgets.QWidget()
        params_outer = QtWidgets.QVBoxLayout(self._cfg_params_pane)
        params_outer.setContentsMargins(0, 0, 0, 0)
        params_outer.setSpacing(4)

        breadcrumb_row = QtWidgets.QHBoxLayout()
        self._cfg_back_btn = QtWidgets.QPushButton("← Back")
        self._cfg_back_btn.setMaximumWidth(80)
        self._cfg_back_btn.clicked.connect(self._lua_back)
        self._cfg_back_btn.setVisible(False)
        self._cfg_breadcrumb = QtWidgets.QLabel("Home")
        self._cfg_breadcrumb.setStyleSheet("border: none;")
        breadcrumb_row.addWidget(self._cfg_back_btn)
        breadcrumb_row.addWidget(self._cfg_breadcrumb)
        breadcrumb_row.addStretch()
        params_outer.addLayout(breadcrumb_row)

        self._cfg_params_scroll = QtWidgets.QScrollArea()
        self._cfg_params_scroll.setWidgetResizable(True)
        self._cfg_params_content = QtWidgets.QWidget()
        self._cfg_params_content_layout = QtWidgets.QVBoxLayout(self._cfg_params_content)
        self._cfg_params_content_layout.setSpacing(4)
        self._cfg_params_content_layout.addStretch()
        self._cfg_params_scroll.setWidget(self._cfg_params_content)
        params_outer.addWidget(self._cfg_params_scroll)

        self._cfg_stack.addWidget(self._cfg_params_pane)

        right_layout.addWidget(self._cfg_stack)
        outer.addWidget(right_panel, 2)

        return tab

    # -- LUA callbacks (called inline on the GUI thread) -----------------

    def _lua_on_devices(self, devices: list) -> None:
        """Rebuild the device list panel."""
        try:
            # Layout is: [label(0), ...cards..., stretch(last)]
            # Clear only the card widgets between label and stretch (indices 1..count-2)
            while self._cfg_device_list_layout.count() > 2:
                item = self._cfg_device_list_layout.takeAt(1)
                if item.widget():
                    item.widget().deleteLater()

            if devices:
                self._cfg_device_label.setVisible(False)
                for device in devices:
                    card = self._make_device_card(device)
                    self._cfg_device_list_layout.insertWidget(
                        self._cfg_device_list_layout.count() - 1, card
                    )
            else:
                self._cfg_device_label.setText("No devices found.")
                self._cfg_device_label.setVisible(True)

            self._selected_card = None
            self._cfg_scan_btn.setEnabled(True)
            self._cfg_scan_btn.setText("Reload")
            self._cfg_device_name_label.setText("Select a device")
            self._cfg_stack.setCurrentIndex(0)
            self._cfg_reload_btn.setEnabled(False)
            self._cfg_linkstat_row.setVisible(False)
        except Exception as e:
            self.onDebug(f"Config device list error: {e}")

    def _make_device_card(self, device: dict) -> QtWidgets.QFrame:
        """Build a clickable device card"""
        name     = device.get('name', '???')
        addr     = device.get('address', 0)
        n_params = device.get('parametersTotal', '?')

        card = QtWidgets.QFrame()
        card.setFrameShape(QtWidgets.QFrame.StyledPanel)
        card.setCursor(QtCore.Qt.PointingHandCursor)
        card.setStyleSheet("""
            QFrame {
                border: 1px solid #555555;
                border-radius: 4px;
                background-color: #3c3c3c;
            }
            QFrame:hover {
                background-color: #4a4a4a;
                border-color: #888888;
            }
        """)

        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(8, 6, 8, 6)
        card_layout.setSpacing(2)

        name_lbl = QtWidgets.QLabel(name)
        name_lbl.setStyleSheet("font-weight: bold; border: none; background: transparent;")

        addr_lbl = QtWidgets.QLabel(f"0x{addr:02X}")
        addr_lbl.setStyleSheet("color: #888888; font-family: monospace; font-size: 8pt; border: none; background: transparent;")

        count_lbl = QtWidgets.QLabel(f"{n_params} params")
        count_lbl.setStyleSheet("color: #888888; font-size: 8pt; border: none; background: transparent;")

        card_layout.addWidget(name_lbl)
        card_layout.addWidget(addr_lbl)
        card_layout.addWidget(count_lbl)

        dev = device
        card.mousePressEvent = lambda e, d=dev, c=card: self._lua_select_device(d, c)

        return card

    def _lua_on_progress(self, loaded: int, total: int) -> None:
        try:
            self._cfg_loading_label.setText(f"Loading parameters... ({loaded}/{total})")
            pct = int(loaded * 100 / total) if total else 0
            self._cfg_progress.setValue(pct)
            self._cfg_stack.setCurrentIndex(1)
        except Exception:
            pass

    def _lua_on_complete(self, parameters: dict) -> None:
        try:
            self._render_params()
            self._cfg_stack.setCurrentIndex(2)
            self._cfg_reload_btn.setEnabled(True)
        except Exception as e:
            self.onDebug(f"Config render error: {e}")

    def _lua_on_aborted(self, message: str) -> None:
        try:
            self._cfg_device_label.setText(f"Error: {message}")
            self._cfg_device_label.setVisible(True)
            self._cfg_device_name_label.setText("Select a device")
            self._cfg_stack.setCurrentIndex(0)
            self._cfg_reload_btn.setEnabled(False)
        except Exception:
            pass

    def _lua_on_field(self, param: dict) -> None:
        pass  # re-render happens in on_loading_complete after reload chain

    def _lua_on_elrs_status_update(self, bad_pkt: int, good_pkt: int, flags: int) -> None:
        try:
            rx_connected = bool(flags & 0x01)
            if rx_connected:
                self._cfg_rx_status_label.setText("RX Connected")
                self._cfg_rx_status_label.setStyleSheet(
                    "color: #ffffff; background-color: #2e7d32; border-radius: 3px; padding: 1px 6px;"
                )
            else:
                self._cfg_rx_status_label.setText("RX Disconnected")
                self._cfg_rx_status_label.setStyleSheet(
                    "color: #ffffff; background-color: #c62828; border-radius: 3px; padding: 1px 6px;"
                )
            self._cfg_pkt_label.setText(f"{bad_pkt}/{good_pkt}")
        except Exception:
            pass

    def _lua_set_banner_mode(self, mode: str) -> None:
        """Configure banner for 'error' (red, no Cancel) or 'confirm' (amber, with Cancel)."""
        if mode == 'error':
            self._lua_error_banner.setStyleSheet(
                "background-color: #c62828; border-radius: 4px;"
            )
            self._lua_cancel_btn.setVisible(False)
        else:
            self._lua_error_banner.setStyleSheet(
                "background-color: #e65100; border-radius: 4px;"
            )
            self._lua_cancel_btn.setVisible(True)
        self._lua_banner_mode = mode

    def _lua_banner_ok(self) -> None:
        """OK button: clear error or confirm command depending on current mode."""
        self._lua_error_banner.setVisible(False)
        if getattr(self, '_lua_banner_mode', 'error') == 'confirm':
            if self._lua_confirm_param_num is not None:
                self.lua.confirm_command(self._lua_confirm_param_num)
            self._lua_confirm_param_num = None
        else:
            self.lua.clear_elrs_error()

    def _lua_confirm_cancel(self) -> None:
        """Cancel button: dismiss confirm banner without sending confirmation."""
        self._lua_error_banner.setVisible(False)
        self._lua_confirm_param_num = None
        self.lua.cancel_command()

    def _lua_on_elrs_error(self, msg: str) -> None:
        """Show the error banner (e.g. 'Not while connected')."""
        self._lua_set_banner_mode('error')
        self._lua_error_label.setText(msg)
        self._lua_error_banner.setVisible(True)

    def _lua_on_elrs_confirm(self, msg: str, param_num: int) -> None:
        """Show the confirmation banner (e.g. 'Enable WiFi while connected?')."""
        self._lua_confirm_param_num = param_num
        self._lua_set_banner_mode('confirm')
        self._lua_error_label.setText(msg)
        self._lua_error_banner.setVisible(True)

    # -- Configuration tab actions ---------------------------------------

    def _lua_scan(self) -> None:
        self._cfg_scan_btn.setEnabled(False)
        self._cfg_scan_btn.setText("Scanning...")
        self._cfg_device_label.setText("Scanning for devices...")
        self._cfg_device_label.setVisible(True)
        self.lua.scan_devices()
        self._cfg_stack.setCurrentIndex(0)
        self._cfg_reload_btn.setEnabled(False)

    def _on_tab_changed(self, index: int) -> None:
        if index == 1 and self._params_tab_first_visit:
            self._params_tab_first_visit = False
            self._lua_scan()

    def _lua_reload(self) -> None:
        if self.lua.selected_device:
            self._cfg_loading_label.setText("Loading parameters... (0/0)")
            self._cfg_progress.setValue(0)
            self._cfg_stack.setCurrentIndex(1)
            self.lua.load_parameters()

    def _lua_select_device(self, device: dict, card: QtWidgets.QFrame = None) -> None:
        if self._selected_card is not None:
            self._selected_card.setStyleSheet("""
                QFrame {
                    border: 1px solid #555555;
                    border-radius: 4px;
                    background-color: #3c3c3c;
                }
                QFrame:hover {
                    background-color: #4a4a4a;
                    border-color: #888888;
                }
            """)
        self._selected_card = card
        if card is not None:
            card.setStyleSheet("""
                QFrame {
                    border: 2px solid #1e88e5;
                    border-radius: 4px;
                    background-color: #3c3c3c;
                }
                QFrame:hover {
                    background-color: #4a4a4a;
                    border-color: #1e88e5;
                }
            """)
        name = device.get('name', '???')
        self._cfg_device_name_label.setText(name)
        self._cfg_loading_label.setText(
            f"Loading parameters... (0/{device.get('parametersTotal', '?')})"
        )
        self._cfg_progress.setValue(0)
        self._cfg_stack.setCurrentIndex(1)
        is_elrs_tx = device.get('isElrs', False) and device.get('address') == CRSF_ADDRESS_MODULE
        if is_elrs_tx:
            _placeholder_style = (
                "color: #e0e0e0; background-color: #3c3c3c; border: 1px solid #555555;"
                " border-radius: 3px; padding: 1px 6px;"
            )
            self._cfg_rx_status_label.setText("--")
            self._cfg_rx_status_label.setStyleSheet(_placeholder_style)
            self._cfg_pkt_label.setText("--/--")
        self._cfg_linkstat_row.setVisible(is_elrs_tx)
        self.lua.select_device(device)

    def _lua_back(self) -> None:
        self.lua.navigate_back()
        self._render_params()

    # -- Parameter rendering ---------------------------------------------

    def _render_params(self) -> None:
        """Rebuild the parameter widget list for the current folder."""
        try:
            # Remove all widgets except the trailing stretch
            layout = self._cfg_params_content_layout
            while layout.count() > 1:
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

            # Breadcrumb
            if self.lua.folder_stack:
                crumbs = ["Home"] + [e['name'] for e in self.lua.folder_stack]
                self._cfg_breadcrumb.setText(" > ".join(crumbs))
                self._cfg_back_btn.setVisible(True)
            else:
                self._cfg_breadcrumb.setText("Home")
                self._cfg_back_btn.setVisible(False)

            # Visible params for current folder, sorted by number
            visible = sorted(
                [p for p in self.lua.parameters.values()
                 if p['parentFolder'] == self.lua.current_folder
                 and not p['hidden']],
                key=lambda p: p['number'],
            )

            for param in visible:
                row_widget = self._make_param_row(param)
                if row_widget:
                    layout.insertWidget(layout.count() - 1, row_widget)
        except Exception as e:
            self.onDebug(f"Render params error: {e}")

    def _make_param_row(self, param: dict) -> QtWidgets.QWidget:
        """Build a single parameter row widget."""
        try:
            container = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(container)
            row.setContentsMargins(2, 1, 2, 1)

            fid   = param['number']
            ptype = param['type']
            name  = param['name'] or f"Param {fid}"
            label = QtWidgets.QLabel(name)
            label.setMinimumWidth(160)
            label.setMaximumWidth(200)
            row.addWidget(label)

            if ptype in (PARAM_TYPE_UINT8, PARAM_TYPE_INT8):
                spin = NoWheelSpinBox()
                if ptype == PARAM_TYPE_INT8:
                    lo = param['min'] if param['min'] is not None else -128
                    hi = param['max'] if param['max'] is not None else 127
                else:
                    lo = param['min'] if param['min'] is not None else 0
                    hi = param['max'] if param['max'] is not None else 255
                spin.setRange(lo, hi)
                spin.blockSignals(True)
                spin.setValue(param['value'] if param['value'] is not None else lo)
                spin.blockSignals(False)
                if param['unit']:
                    spin.setSuffix(f" {param['unit']}")
                spin.valueChanged.connect(
                    lambda v, f=fid: self._on_param_changed(f, v)
                )
                row.addWidget(spin)

            elif ptype == PARAM_TYPE_TEXT_SELECTION:
                combo = NoWheelComboBox()
                # AdjustToContents makes sizeHint() = widest item, so the popup
                # inherits that width and doesn't clip option text.
                combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
                combo.setMinimumWidth(180)
                for i, opt in enumerate(param.get('options') or []):
                    if opt.strip():
                        combo.addItem(opt, i)
                combo.blockSignals(True)
                current_val = param.get('value')
                for j in range(combo.count()):
                    if combo.itemData(j) == current_val:
                        combo.setCurrentIndex(j)
                        break
                combo.blockSignals(False)
                combo.currentIndexChanged.connect(
                    lambda idx, f=fid, c=combo: self._on_param_changed(
                        f, c.itemData(idx) if idx >= 0 else 0
                    )
                )
                # Stretch factor 1: combo expands to fill the row instead of the
                # trailing spacer stealing all remaining width.
                row.addWidget(combo, 1)

            elif ptype == PARAM_TYPE_FOLDER:
                btn = QtWidgets.QPushButton("Enter")
                btn.clicked.connect(
                    lambda _, f=fid, n=name: self._lua_enter_folder(f, n)
                )
                row.addWidget(btn)

            elif ptype == PARAM_TYPE_INFO:
                val_label = QtWidgets.QLabel(str(param.get('value') or ''))
                val_label.setStyleSheet("color: #aaaaaa;")
                row.addWidget(val_label)

            elif ptype == PARAM_TYPE_COMMAND:
                btn_text = param.get('value') or "Execute"
                btn = QtWidgets.QPushButton(btn_text)
                btn.clicked.connect(
                    lambda _, f=fid: self._on_param_changed(f, 1)  # status=START
                )
                row.addWidget(btn)

            elif ptype == PARAM_TYPE_STRING:
                val_label = QtWidgets.QLabel(str(param.get('value') or ''))
                val_label.setStyleSheet("color: #aaaaaa;")
                row.addWidget(val_label)

            else:
                row.addWidget(QtWidgets.QLabel(f"Unsupported type 0x{ptype:02X}"))

            row.addStretch()
            return container
        except Exception as e:
            self.onDebug(f"Param row error (fid={param.get('number')}): {e}")
            return None

    def _lua_enter_folder(self, folder_id: int, name: str) -> None:
        self.lua.navigate_to_folder(folder_id, name)
        self._render_params()

    def _on_param_changed(self, fid: int, value: int) -> None:
        if not self.lua.update_parameter(int(fid), int(value)):
            self.onDebug(f"param write rejected (fid={fid})")

    def closeEvent(self, e):
        try:
            self.joy.stop_thread()
        except:
            pass
        try:
            self.serThread.close()
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

            # Use cached baseline joystick state from JoystickHandler
            axes = list(self.latest_axes) if self.latest_axes else []
            btns = list(self.latest_buttons) if self.latest_buttons else []
            if not axes and not btns:
                self.onDebug("Cannot read joystick state. Cannot map channel.")
                return

            self.mapping_row = row
            self.mapping_baseline = (axes, btns)
            self.mapping_started_at = time.monotonic()
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
    # Raise Windows system timer resolution from 15.6 ms to 1 ms so that
    # QTimer(0) fires at ~1 kHz instead of ~64 Hz. Required for stable 1000 Hz
    # RC output; harmless on macOS/Linux where the kernel is already fine-grained.
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass

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
