#!/usr/bin/env python3
# ELRS Calibration GUI + Telemetry (PyQt5)
# pip install pyqt5 pygame pyserial

import sys
import json
import time
import re
import threading
from PyQt5 import QtWidgets, QtCore
from PyQt5.QtGui import QIcon

# Import from refactored modules
from crsf_protocol import *
from device_parameters import DeviceParameterParser, DeviceInfo
from serial_interface import SerialThread
from joystick_handler import JoystickHandler
from channel_ui import ChannelRow, SRC_CHOICES
from config_manager import ConfigManager, get_available_ports, DEFAULT_BAUD, CHANNELS, DEFAULT_CFG

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
}

SEND_HZ = 60


class Main(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ELRS Calibrator + Link Stats")
        self.setWindowIcon(QIcon('icon.ico'))
        self.resize(1430, 930)
        self.cfg = DEFAULT_CFG.copy()
        self._load_cfg()

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

        # Serial port selection controls
        port_widget = QtWidgets.QWidget()
        port_layout = QtWidgets.QHBoxLayout(port_widget)
        port_layout.setContentsMargins(0, 5, 0, 5)
        # Joystick status at far left
        self.joyStatusLabel = QtWidgets.QLabel("Scanning for joystick...")
        port_layout.addWidget(self.joyStatusLabel)

        # Divider between joystick status and COM controls
        joy_divider = QtWidgets.QFrame()
        joy_divider.setFrameShape(QtWidgets.QFrame.VLine)
        joy_divider.setFrameShadow(QtWidgets.QFrame.Sunken)
        joy_divider.setLineWidth(2)
        port_layout.addWidget(joy_divider)

        # Serial COM controls
        port_layout.addWidget(QtWidgets.QLabel("COM Port:"))

        self.portCombo = QtWidgets.QComboBox()
        self._refresh_port_list()
        self.portCombo.setCurrentText(self.cfg["serial_port"])
        self.portCombo.currentTextChanged.connect(self._on_port_changed)
        port_layout.addWidget(self.portCombo)

        refresh_btn = QtWidgets.QPushButton("Refresh")
        refresh_btn.clicked.connect(self._refresh_port_list)
        refresh_btn.setMaximumWidth(80)
        port_layout.addWidget(refresh_btn)

        # Packet Rate is now displayed in the Configuration tab

        port_layout.addStretch()

        # JR Bay (UART) and TX status labels — stacked vertically
        self.jrBayStatusLabel = QtWidgets.QLabel("JR Bay: Disconnected")
        self.jrBayStatusLabel.setStyleSheet("color: red; font-weight: bold;")
        self.txStatusLabel = QtWidgets.QLabel("TX: Disconnected")
        self.txStatusLabel.setStyleSheet("color: red; font-weight: bold;")
        # Create a small widget with vertical layout to stack the two labels
        status_widget = QtWidgets.QWidget()
        status_layout = QtWidgets.QVBoxLayout(status_widget)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(2)
        status_layout.addWidget(self.jrBayStatusLabel)
        status_layout.addWidget(self.txStatusLabel)
        # Make sure the widget doesn't expand vertically unnecessarily
        status_widget.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
        port_layout.addWidget(status_widget)

        port_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        layout.addWidget(port_widget)

        # Joystick (auto-scanning) - using JoystickHandler instead of Joy
        self.joy = JoystickHandler()
        self.joy.status.connect(self.onDebug)
        self.joy.status.connect(self.onJoyStatus)

        # Channels (scrollable, 2-column layout: 8 on left, 8 on right)
        self.rows = []
        grid = QtWidgets.QGridLayout()
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
            self.rows.append(row)
            # First 8 channels in column 0, next 8 in column 2 (with divider in column 1)
            col = 0 if i < CHANNELS // 2 else 2
            row_idx = i if i < CHANNELS // 2 else i - CHANNELS // 2
            grid.addWidget(row, row_idx, col)

        # Add vertical divider in column 1 (single frame spanning all rows)
        divider = QtWidgets.QFrame()
        divider.setFrameShape(QtWidgets.QFrame.VLine)
        divider.setFrameShadow(QtWidgets.QFrame.Sunken)
        divider.setLineWidth(2)
        grid.addWidget(divider, 0, 1, CHANNELS // 2, 1)

        ch_container = QtWidgets.QWidget()
        ch_container.setLayout(grid)

        ch_scroll = QtWidgets.QScrollArea()
        ch_scroll.setWidgetResizable(True)
        ch_scroll.setWidget(ch_container)
        ch_scroll.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

        # Telemetry
        tel = QtWidgets.QHBoxLayout()
        self.telLabels = {}
        for key in ["1RSS","2RSS","RSNR","TRSS","TSNR","LQ","TLQ","RFMD","TPWR"]:
            box = QtWidgets.QGroupBox(key)
            lab = QtWidgets.QLabel("--")
            lab.setAlignment(QtCore.Qt.AlignCenter)
            f = lab.font(); f.setPointSize(14); lab.setFont(f)
            v = QtWidgets.QVBoxLayout(box); v.addWidget(lab)
            tel.addWidget(box)
            self.telLabels[key] = lab

        # Initially set link stats labels to grey
        for lab in self.telLabels.values():
            lab.setStyleSheet("color: grey;")

        # Log (fixed height)
        self.log = QtWidgets.QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        self.log.setFixedHeight(140)

        # Tabs for Channels and Configuration
        self.tabs = QtWidgets.QTabWidget()

        # Channels tab
        channels_tab = QtWidgets.QWidget()
        channels_layout = QtWidgets.QVBoxLayout(channels_tab)
        channels_layout.addWidget(ch_scroll)
        self.tabs.addTab(channels_tab, "Channels")

        # Configuration tab
        config_tab = QtWidgets.QWidget()
        config_layout = QtWidgets.QVBoxLayout(config_tab)
        # Leave blank for now
        self.tabs.addTab(config_tab, "Configuration")
        config_tab.setMinimumSize(400, 300)
        # Loading indicator for config tab (indeterminate progress)
        self.config_loading = QtWidgets.QProgressBar()
        self.config_loading.setRange(0, 0)  # indeterminate
        self.config_loading.setMaximumHeight(12)
        self.config_loading.setVisible(False)
        # Add it to the config layout as the first widget (hidden by default)
        config_layout.addWidget(self.config_loading)
        # Mapping of field id -> widget used for updating without a full re-populate
        self._config_field_widgets = {}
        # Pending writes: fid -> (desired_value, timestamp)
        self._pending_param_writes = {}

        # Set Channels as default
        self.tabs.setCurrentIndex(0)

        layout.addWidget(self.tabs)

        # Telemetry (link stats) below the tabs
        layout.addLayout(tel)

        # Log below telemetry
        layout.addWidget(self.log)

        # Timer loop
        # Only the tabs area should expand/contract on resize
        layout.setStretch(1, 1)

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

    def onTel(self, d):
        for k, v in d.items():
            if k in self.telLabels:
                try:
                    unit = TELEMETRY_UNIT_MAP.get(k)
                    if unit and v is not None:
                        # display numeric values with units
                        # Keep simple formatting — if it's int or float show without extra quirks
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
            # JR Bay is simply the COM port when connected
            try:
                portname = getattr(self.serThread, 'port', self.cfg.get('serial_port', 'Unknown'))
                self.jrBayStatusLabel.setText(f"JR Bay: {portname}")
                self.jrBayStatusLabel.setStyleSheet("color: green; font-weight: bold;")
            except Exception:
                pass
        else:
            # Serial port disconnected
            try:
                self.jrBayStatusLabel.setText("JR Bay: Disconnected")
                self.jrBayStatusLabel.setStyleSheet("color: red; font-weight: bold;")
            except Exception:
                pass
            # When the serial port disconnects the TX is implicitly unreachable
            try:
                self.txStatusLabel.setText("TX: Disconnected")
                self.txStatusLabel.setStyleSheet("color: red; font-weight: bold;")
            except Exception:
                pass
            # rateCombo removed; packet rate is now in the config tab

    def onDebug(self, s):
        try:
            self.log.appendPlainText(s)
        except Exception:
            pass

    def onJoyStatus(self, s):
        # Show scanning/connected/disconnected messages or name
        try:
            self.joyStatusLabel.setText(str(s))
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
                # mark TX connected and update color/name
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
                        self.txStatusLabel.setText(f"TX: {tx_name}")
                    else:
                        self.txStatusLabel.setText("TX: Connected")
                    self.txStatusLabel.setStyleSheet("color: green; font-weight: bold;")
            except Exception as e:
                self.onDebug(f"onSync heartbeat update error: {e}")
        except Exception as e:
            self.onDebug(f"onSync error: {e}")

    def tick(self):
        axes, btns = self.joy.read()
        joystick_connected = self.joy.j is not None

        # Mapping mode: detect next button press or large axis move
        if self.mapping_row is not None:
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
                self.mapping_row.set_mapping(src, idx)
                try:
                    self.mapping_row.mapBtn.setText("Map")
                    self.mapping_row.mapBtn.setEnabled(True)
                except Exception:
                    pass
                self.onDebug(f"Mapped CH{self.mapping_row.idx+1} to {src}[{idx}]")
                self.mapping_row = None
                self.save_cfg()
            elif time.time() - self.mapping_started_at > 8.0:
                # Timeout
                try:
                    self.mapping_row.mapBtn.setText("Map")
                    self.mapping_row.mapBtn.setEnabled(True)
                except Exception:
                    pass
                self.onDebug("Mapping timed out; try again.")
                self.mapping_row = None

        ch = [r.compute(axes, btns) for r in self.rows]
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
                    self.txStatusLabel.setText("TX: Disconnected")
                    self.txStatusLabel.setStyleSheet("color: red; font-weight: bold;")
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

        # Link stats timeout check
        now = time.time()
        timeout = now - self.serThread.last_link_stats_time > 5.0
        color = "grey" if timeout else "black"
        for lab in self.telLabels.values():
            lab.setStyleSheet(f"color: {color};")

    def save_cfg(self):
        self.cfg["channels"] = [r.to_cfg() for r in self.rows]
        self._save_cfg_disk()
        self.onDebug("Config saved")

    def _save_cfg_disk(self):
        try:
            with open("calib.json", "w") as f:
                json.dump(self.cfg, f, indent=2)
        except Exception as e:
            self.onDebug(f"Save error: {e}")

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
        current = self.portCombo.currentText()
        self.portCombo.blockSignals(True)
        self.portCombo.clear()
        ports = get_available_ports()
        for port in ports:
            self.portCombo.addItem(port)
        # If the previous selection still exists, restore it
        if current and self.portCombo.findText(current) >= 0:
            self.portCombo.setCurrentText(current)
        elif ports:
            # Otherwise select the first available port
            self.portCombo.setCurrentIndex(0)
        self.portCombo.blockSignals(False)

    def _on_port_changed(self, port):
        """Handle COM port selection change"""
        if not port:
            return
        self.cfg["serial_port"] = port
        self.serThread.reconnect(port, DEFAULT_BAUD)
        self.save_cfg()


    # Packet rate moved into the Configuration tab as a standard 'select' field.
    # Writes are handled via the combo control in the config tab which calls _on_param_changed.

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

    def closeEvent(self, e):
        try:
            self.serThread.close()
        except:
            pass
        e.accept()

    # --- Mapping helpers ---
    def begin_mapping(self, row: ChannelRow):
        # If another mapping is active, cancel it visually
        if self.mapping_row is not None and hasattr(self.mapping_row, "mapBtn"):
            try:
                self.mapping_row.mapBtn.setText("Map")
                self.mapping_row.mapBtn.setEnabled(True)
            except Exception:
                pass
        self.mapping_row = row
        self.mapping_baseline = self.joy.read()
        self.mapping_started_at = time.time()
        try:
            row.mapBtn.setText("Listening…")
            row.mapBtn.setEnabled(False)
        except Exception:
            pass
        self.onDebug(f"Move an axis or press a button to map CH{row.idx+1} …")



    def _on_device_discovered(self, src: int, details: dict):
        # Update the UI state when a device is discovered: show device name and mark current device
        name = details.get('name', 'Unknown')
        try:
            # Only set current_device_id for TX modules, not receivers
            if src in (CRSF_ADDRESS_CRSF_TRANSMITTER, CRSF_ADDRESS_TRANSMITTER_LEGACY):
                self.serThread.current_device_id = src
                # Update the TX name in the UI; heartbeat will change the color
                # Only update label for TX modules, not receivers
                try:
                    self.txStatusLabel.setText(f"TX: {name}")
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
        for fid, field in fields.items():
            # Packet Rate combo moved into the configuration tab UI; the config tab will populate it
            # The _populate_config_tab function will create a QComboBox for this field like any other select.
            pass

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
        self._populate_config_tab(fields, src)
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

    def _populate_config_tab(self, fields, src):
        # Clear existing widgets in config tab
        config_tab = self.tabs.widget(1)  # Configuration tab
        if config_tab.layout():
            layout = config_tab.layout()
            # Clear all widgets from the existing layout
            # some widgets in the config tab are permanent (like the loading bar)
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
        layout = config_tab.layout()
        if layout is None:
            layout = QtWidgets.QVBoxLayout(config_tab)
            config_tab.setLayout(layout)

        # Reset widget mapping for this repopulation
        try:
            self._config_field_widgets.clear()
        except Exception:
            pass
        # Create scrollable area
        # Remove any existing scroll area if present
        # (look for first QScrollArea child and delete it to avoid duplicates)
        for child in config_tab.findChildren(QtWidgets.QScrollArea):
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
        # Add a small toolbar with a Refresh button at the top of the config tab
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
                # include Packet Rate in the config tab like any other field
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
                    combo = QtWidgets.QComboBox()
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
                    combo = QtWidgets.QComboBox()
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
        config_tab.update()
        self.tabs.update()
        self.tabs.repaint()
        widget.update()
        scroll.update()

# -------------------------------------------------------------------

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    w = Main()
    w.show()
    sys.exit(app.exec_())
