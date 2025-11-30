"""
Channel UI Components

This module contains UI components for channel configuration and mapping.
"""

import math
import time
from PyQt5 import QtWidgets, QtCore, QtGui


# Source type choices for channel mapping
SRC_CHOICES = ["axis", "button", "multi", "none"]


class NoWheelComboBox(QtWidgets.QComboBox):
    """QComboBox that ignores mouse wheel events."""

    def wheelEvent(self, event):
        """Ignore wheel events to prevent accidental value changes."""
        event.ignore()


class MultiButtonRow(QtWidgets.QWidget):
    """A row for displaying and configuring a multi-button mapping."""

    def __init__(self, btn_idx, output_val, min_val, max_val, parent_dialog=None, is_pending=False, is_default=False):
        super().__init__()
        self.btn_idx = btn_idx
        self.output_val = output_val
        self.min_val = min_val
        self.max_val = max_val
        self.parent_dialog = parent_dialog
        self.is_pending = is_pending  # True if waiting for button press
        self.is_default = is_default  # True if this is the default button

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(6)

        if is_pending:
            # Pending state: waiting for button press
            self.status_lbl = QtWidgets.QLabel("Press a button to map...")
            self.status_lbl.setStyleSheet("color: #888888; font-style: italic;")
            layout.addWidget(self.status_lbl)
            layout.addStretch()
        else:
            # Configured state: show button number and value
            self.btn_lbl = QtWidgets.QLabel(f"Button {btn_idx}")
            self.btn_lbl.setFixedHeight(22)
            layout.addWidget(self.btn_lbl)

            # Stretch to push value and X to the right
            layout.addStretch()

            # Default checkbox
            self.default_chk = QtWidgets.QCheckBox("Default")
            self.default_chk.setChecked(is_default)
            self.default_chk.setFixedHeight(22)
            self.default_chk.toggled.connect(self._on_default_toggled)
            layout.addWidget(self.default_chk)

            # Output value
            self.val_box = QtWidgets.QSpinBox()
            self.val_box.setRange(min_val, max_val)
            self.val_box.setValue(output_val)
            self.val_box.setMaximumWidth(65)
            self.val_box.setFixedHeight(22)
            layout.addWidget(self.val_box)

            # Remove button (elegant X)
            self.remove_btn = QtWidgets.QPushButton("✕")
            self.remove_btn.setFixedWidth(18)
            self.remove_btn.setFixedHeight(22)
            self.remove_btn.setStyleSheet("background-color: transparent; border: none; padding: 0px; margin: 0px; color: #e0e0e0;")
            layout.addWidget(self.remove_btn)

    def get_config(self):
        """Get the current configuration from this row."""
        if not self.is_pending:
            return (str(self.btn_idx), self.val_box.value())
        return None

    def set_highlighted(self, highlighted):
        """Highlight this row when its button is pressed."""
        if highlighted:
            self.setStyleSheet("background-color: #1e88e5; border-radius: 3px;")
        else:
            self.setStyleSheet("")

    def finalize_with_button(self, btn_idx, output_val):
        """Convert from pending to configured state."""
        self.btn_idx = btn_idx
        self.output_val = output_val
        self.is_pending = False

        # Clear layout
        while self.layout().count():
            item = self.layout().takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        layout = self.layout()
        layout.setContentsMargins(4, 1, 4, 1)
        layout.setSpacing(6)

        # Button label
        self.btn_lbl = QtWidgets.QLabel(f"Button {btn_idx}")
        self.btn_lbl.setFixedHeight(22)
        layout.addWidget(self.btn_lbl)

        # Stretch to push value and X to the right
        layout.addStretch()

        # Default checkbox
        self.default_chk = QtWidgets.QCheckBox("Default")
        self.default_chk.setChecked(self.is_default)
        self.default_chk.setFixedHeight(22)
        self.default_chk.toggled.connect(self._on_default_toggled)
        layout.addWidget(self.default_chk)

        # Output value
        self.val_box = QtWidgets.QSpinBox()
        self.val_box.setRange(self.min_val, self.max_val)
        self.val_box.setValue(output_val)
        self.val_box.setMaximumWidth(65)
        self.val_box.setFixedHeight(22)
        layout.addWidget(self.val_box)

        # Remove button
        self.remove_btn = QtWidgets.QPushButton("✕")
        self.remove_btn.setFixedWidth(18)
        self.remove_btn.setFixedHeight(22)
        self.remove_btn.setStyleSheet("background-color: transparent; border: none; padding: 0px; margin: 0px; color: #e0e0e0;")
        layout.addWidget(self.remove_btn)

        if hasattr(self.parent_dialog, '_on_remove_row'):
            self.remove_btn.clicked.connect(lambda: self.parent_dialog._on_remove_row(self))

    def _on_default_toggled(self):
        """Handle default checkbox toggle - ensure only one button is default."""
        if self.parent_dialog and self.default_chk.isChecked():
            # Uncheck all other rows
            for row in self.parent_dialog._button_rows:
                if row != self and hasattr(row, 'default_chk'):
                    row.default_chk.blockSignals(True)
                    row.default_chk.setChecked(False)
                    row.default_chk.blockSignals(False)
                    row.is_default = False
            self.is_default = True
            # Update parent dialog's default button index
            self.parent_dialog.default_btn_idx = self.btn_idx
        else:
            self.is_default = self.default_chk.isChecked()


class MultiButtonDialog(QtWidgets.QDialog):
    """Dialog for managing multi-button mappings."""

    def __init__(self, button_map, min_val, max_val, parent=None):
        """Initialize multi-button dialog.

        Args:
            button_map: Dictionary mapping button indices to output values
            min_val: Minimum output value
            max_val: Maximum output value
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("Multi-Button Config")
        self.setModal(True)
        # Remove question mark icon from title bar
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowContextHelpButtonHint)
        self.resize(250, 245)  # Compact width, reduced height (225 + 25px)
        self.button_map = button_map.copy() if button_map else {}
        self.button_map_order = [k for k in self.button_map.keys() if k != "__default_btn__"]  # Preserve insertion order, exclude metadata
        self.default_btn_idx = self.button_map.get("__default_btn__", None)  # Extract default button index
        self.min_val = min_val
        self.max_val = max_val
        self.parent_channel = parent
        self._mapping_row = None  # Track which row is being mapped (pending row)
        self._mapping_started_at = 0.0  # Track when mapping started for timeout
        self._button_rows = []  # List of MultiButtonRow widgets
        self._last_button_press_states = {}  # Track last button press state for each button
        # Apply dark theme
        self.setStyleSheet(self._get_dark_stylesheet())
        self._init_ui()
        # Set dark title bar on Windows
        self._set_dark_title_bar()
        # Start timer to detect button presses
        self._update_timer = QtCore.QTimer()
        self._update_timer.timeout.connect(self._check_button_press)
        self._update_timer.start(50)  # Check every 50ms

    def _get_dark_stylesheet(self):
        """Get dark theme stylesheet for this dialog."""
        return """
            QDialog {{
                background-color: #2b2b2b;
                color: #e0e0e0;
            }}
            QLabel {{
                color: #e0e0e0;
            }}
            QTableWidget {{
                background-color: #3c3c3c;
                alternate-background-color: #343434;
                gridline-color: #555555;
                color: #e0e0e0;
            }}
            QTableWidget::item {{
                padding: 4px;
            }}
            QHeaderView::section {{
                background-color: #2b2b2b;
                color: #e0e0e0;
                padding: 4px;
                border: 1px solid #555555;
            }}
            QSpinBox {{
                background-color: #3c3c3c;
                color: #e0e0e0;
                border: 1px solid #555555;
                padding: 2px;
            }}
            QPushButton {{
                background-color: #3c3c3c;
                color: #e0e0e0;
                border: 1px solid #555555;
                padding: 2px 8px;
                border-radius: 3px;
                font-size: 8pt;
            }}
            QPushButton:hover {{
                background-color: #4a4a4a;
                border: 1px solid #666666;
            }}
            QPushButton:pressed {{
                background-color: #2a2a2a;
            }}
            QDialogButtonBox {{
                button-layout: 0;
            }}
        """

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

    def _init_ui(self):
        """Initialize dialog UI."""
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # Scrollable area for button rows
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { border: none; }")
        scroll_widget = QtWidgets.QWidget()
        self.rows_layout = QtWidgets.QVBoxLayout(scroll_widget)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(1)

        # Populate with existing mappings (in order)
        for btn_idx_str in self.button_map_order:
            btn_idx = int(btn_idx_str)
            val = self.button_map[btn_idx_str]
            self._add_row(btn_idx, val)

        self.rows_layout.addStretch()  # Push rows to top
        scroll_area.setWidget(scroll_widget)
        layout.addWidget(scroll_area, 1)  # Give scroll area most space

        # Add button
        add_btn = QtWidgets.QPushButton("Add Button")
        add_btn.setMaximumHeight(24)
        add_btn.clicked.connect(self._add_button)
        layout.addWidget(add_btn)

        # Add vertical padding between Add Button and Save/Cancel (same as horizontal gap between buttons)
        layout.addSpacing(4)

        # Dialog buttons in a horizontal layout taking full width
        buttons_layout = QtWidgets.QHBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)
        buttons_layout.setSpacing(4)

        save_btn = QtWidgets.QPushButton("Save")
        cancel_btn = QtWidgets.QPushButton("Cancel")

        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

        buttons_layout.addWidget(save_btn)
        buttons_layout.addWidget(cancel_btn)

        layout.addLayout(buttons_layout)

        self.setLayout(layout)

    def _add_row(self, btn_idx, val):
        """Add a configured row to the list."""
        is_default = (btn_idx == self.default_btn_idx)
        row = MultiButtonRow(btn_idx, val, self.min_val, self.max_val, self, is_pending=False, is_default=is_default)
        row.remove_btn.clicked.connect(lambda: self._on_remove_row(row))
        self._button_rows.append(row)
        # Insert before the stretch
        self.rows_layout.insertWidget(len(self._button_rows) - 1, row)

    def _add_button(self):
        """Add a pending button mapping row waiting for button press."""
        # Get list of already mapped buttons
        mapped_buttons = set()
        for row in self._button_rows:
            if not row.is_pending:
                mapped_buttons.add(row.btn_idx)

        # Check if there's already a pending row
        if self._mapping_row is not None:
            return  # Already adding a button

        # Calculate default output value based on number of mapped buttons
        num_mapped = len(mapped_buttons)
        default_values = [1200, 1320, 1440, 1560, 1680, 1800]
        if num_mapped < len(default_values):
            default_val = default_values[num_mapped]
        else:
            default_val = 1800

        # Auto-set first button as default if no default exists yet
        is_first_button = (num_mapped == 0)
        if is_first_button and self.default_btn_idx is None:
            self.default_btn_idx = None  # Will be set when button is mapped

        # Create a pending row with calculated default value
        row = MultiButtonRow(0, default_val, self.min_val, self.max_val, self, is_pending=True)
        self._button_rows.append(row)
        self._mapping_row = row  # Mark as the row to be mapped
        self._mapping_started_at = time.time()  # Start timeout timer
        # Insert before the stretch
        self.rows_layout.insertWidget(len(self._button_rows) - 1, row)

    def _on_remove_row(self, row):
        """Remove a row from the list."""
        if row in self._button_rows:
            # If removing the default button, make the first remaining button the default
            if row.is_default and row.btn_idx == self.default_btn_idx:
                self.default_btn_idx = None
                # Find and set the first non-pending button as default
                for r in self._button_rows:
                    if r != row and not r.is_pending:
                        self.default_btn_idx = r.btn_idx
                        r.default_chk.blockSignals(True)
                        r.default_chk.setChecked(True)
                        r.default_chk.blockSignals(False)
                        r.is_default = True
                        break

            self._button_rows.remove(row)
            self.rows_layout.removeWidget(row)
            row.deleteLater()
            if row == self._mapping_row:
                self._mapping_row = None

    def _check_button_press(self):
        """Check if a button is pressed while waiting for mapping."""
        if self._mapping_row is None or not self.parent_channel:
            return

        # Check for timeout (5 seconds)
        if time.time() - self._mapping_started_at > 5.0:
            self._on_remove_row(self._mapping_row)
            return

        # Try to get button states from parent
        try:
            # The parent channel needs to provide button states
            # For now, we'll rely on the mapping system to tell us
            pass
        except Exception:
            pass

    def set_mapped_button(self, btn_idx):
        """Called by parent channel when a button is mapped (for pending row)."""
        if self._mapping_row is not None:
            # Check if this button is already mapped
            for row in self._button_rows:
                if not row.is_pending and row.btn_idx == btn_idx:
                    # Button already mapped, cancel the pending row
                    self._on_remove_row(self._mapping_row)
                    return

            # Convert pending row to configured row with its calculated output value
            output_val = self._mapping_row.output_val

            # Auto-set first button as default
            is_first_button = (len([r for r in self._button_rows if not r.is_pending]) == 0)
            if is_first_button and self.default_btn_idx is None:
                self.default_btn_idx = btn_idx

            self._mapping_row.finalize_with_button(btn_idx, output_val)

            # Set default checkbox if this is the default button
            if btn_idx == self.default_btn_idx:
                self._mapping_row.default_chk.blockSignals(True)
                self._mapping_row.default_chk.setChecked(True)
                self._mapping_row.default_chk.blockSignals(False)
                self._mapping_row.is_default = True

            self._mapping_row = None

    def update_button_highlight(self, button_states):
        """Update row highlighting based on which button is currently pressed.

        Args:
            button_states: List of button states (0 or 1 for each button index)
        """
        # Clear all highlighting
        for row in self._button_rows:
            row.set_highlighted(False)

        # Highlight row if its button is currently pressed
        for row in self._button_rows:
            if not row.is_pending and row.btn_idx < len(button_states):
                if button_states[row.btn_idx] == 1:
                    row.set_highlighted(True)

    def get_button_map(self):
        """Get the button map from the dialog."""
        button_map = {}
        # Preserve insertion order, skip pending rows
        for row in self._button_rows:
            cfg = row.get_config()
            if cfg is not None:
                btn_idx_str, val = cfg
                button_map[btn_idx_str] = val

        # Store the default button index if it exists
        if self.default_btn_idx is not None:
            button_map["__default_btn__"] = self.default_btn_idx

        return button_map

    def closeEvent(self, event):
        """Stop the timer when dialog is closed."""
        self._update_timer.stop()
        super().closeEvent(event)


def map_axis_to_range(val, inv, mn, ct, mx, expo=1.0):
    """Map joystick axis value to output range.

    Args:
        val: Axis value from joystick (-1.0 to 1.0)
        inv: Whether to invert the axis
        mn: Minimum output value
        ct: Center output value
        mx: Maximum output value
        expo: Exponential curve factor (1.0 = linear, >1 = more sensitive at center)

    Returns:
        Mapped output value (clamped to [mn, mx])
    """
    try:
        v = float(val)
    except Exception:
        v = 0.0

    if inv:
        v = -v

    # Clamp to [-1, 1]
    v = max(-1.0, min(1.0, v))

    # Snap if close to full deflection — prevents off-by-one errors
    EPS = 0.002
    if abs(abs(v) - 1.0) < EPS:
        v = math.copysign(1.0, v)

    # Apply exponential curve - symmetric for positive and negative
    if expo != 1.0 and v != 0.0:
        # Preserve sign and apply exponential to absolute value
        sign = 1.0 if v >= 0.0 else -1.0
        v = sign * (abs(v) ** expo)

    # Piecewise linear mapping around the center
    if v >= 0:
        outf = ct + v * (mx - ct)
    else:
        outf = ct + v * (ct - mn)

    out = int(round(outf))
    return max(mn, min(mx, out))


class ChannelRow(QtWidgets.QWidget):
    """UI row for configuring a single channel."""

    changed = QtCore.pyqtSignal()
    mapRequested = QtCore.pyqtSignal(object)
    debug = QtCore.pyqtSignal(str)

    def __init__(self, idx, cfg):
        """Initialize channel row.

        Args:
            idx: Channel index (0-based)
            cfg: Configuration dictionary for this channel
        """
        super().__init__()
        self.idx = idx
        layout = QtWidgets.QGridLayout(self)
        name = f"CH{idx + 1}"

        # Widget height constant
        WIDGET_HEIGHT = 26

        # Widgets
        self.nameBox = QtWidgets.QLineEdit()
        self.nameBox.setPlaceholderText("Name")
        self.nameBox.setFixedHeight(WIDGET_HEIGHT)
        default_names = ["Ail", "Elev", "Thr", "Rudd", "Arm", "Mode"]
        default_name = default_names[idx] if idx < len(default_names) else ""
        self.nameBox.setText(cfg.get("name", default_name))

        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 2000)
        self.bar.setFixedHeight(WIDGET_HEIGHT)

        self.val = QtWidgets.QLabel("1500")
        self.val.setFixedHeight(WIDGET_HEIGHT)

        self.src = NoWheelComboBox()
        self.src.addItems(SRC_CHOICES)
        self.src.setMaximumWidth(80)
        self.src.setFixedHeight(WIDGET_HEIGHT)
        self.src.setCurrentText(cfg.get("src", "none"))

        self.idxBox = QtWidgets.QSpinBox()
        self.idxBox.setRange(0, 63)
        self.idxBox.setValue(cfg.get("idx", 0))
        self.idxBox.setMaximumWidth(60)
        self.idxBox.setFixedHeight(WIDGET_HEIGHT)

        self.expoLbl = QtWidgets.QLabel("Expo")
        self.expoLbl.setMaximumWidth(40)
        self.expoLbl.setFixedHeight(WIDGET_HEIGHT)

        self.expoBox = QtWidgets.QDoubleSpinBox()
        self.expoBox.setRange(1.0, 5.0)
        self.expoBox.setSingleStep(0.1)
        self.expoBox.setValue(cfg.get("expo", 1.0))
        self.expoBox.setMaximumWidth(55)
        self.expoBox.setFixedHeight(WIDGET_HEIGHT)

        self.inv = QtWidgets.QCheckBox("Reverse")
        self.inv.setChecked(cfg.get("inv", False))
        self.inv.setMaximumWidth(80)
        self.inv.setFixedHeight(WIDGET_HEIGHT)

        self.toggleBox = QtWidgets.QCheckBox("Toggle")
        self.toggleBox.setChecked(cfg.get("toggle", False))
        self.toggleBox.setMaximumWidth(80)
        self.toggleBox.setFixedHeight(WIDGET_HEIGHT)

        self.toggleGroupBox = NoWheelComboBox()
        self.toggleGroupBox.addItem("None")  # Index 0 = None (stored as -1)
        for i in range(1, 9):  # Groups 1-8 (indices 1-8, stored as 0-7)
            self.toggleGroupBox.addItem(f"Group {i}")
        # Toggle groups: stored as -1 for None, 0-7 for Groups 1-8
        # Default is None (-1)
        saved_group = cfg.get("toggle_group", -1)
        # Convert saved value to dropdown index: -1 -> 0, 0 -> 1, 1 -> 2, etc.
        if saved_group == -1:
            dropdown_index = 0
        else:
            dropdown_index = max(1, min(8, saved_group + 1))
        self.toggleGroupBox.setCurrentIndex(dropdown_index)
        self.toggleGroupBox.setMaximumWidth(80)
        self.toggleGroupBox.setFixedHeight(WIDGET_HEIGHT)
        self.toggleGroupBox.setEnabled(cfg.get("toggle", False))

        self.rotaryBox = QtWidgets.QCheckBox("Rotary")
        self.rotaryBox.setChecked(cfg.get("rotary", False))
        self.rotaryBox.setMaximumWidth(80)
        self.rotaryBox.setFixedHeight(WIDGET_HEIGHT)

        self.rotaryStopsBox = QtWidgets.QSpinBox()
        self.rotaryStopsBox.setRange(3, 6)
        self.rotaryStopsBox.setValue(cfg.get("rotary_stops", 3))
        self.rotaryStopsBox.setMaximumWidth(60)
        self.rotaryStopsBox.setFixedHeight(WIDGET_HEIGHT)
        self.rotaryStopsBox.setEnabled(cfg.get("rotary", False))

        self.minBox = QtWidgets.QSpinBox()
        self.minBox.setRange(0, 2000)
        self.minBox.setValue(cfg.get("min", 1000))
        self.minBox.setAlignment(QtCore.Qt.AlignLeft)
        self.minBox.setMaximumWidth(70)
        self.minBox.setFixedHeight(WIDGET_HEIGHT)

        self.midBox = QtWidgets.QSpinBox()
        self.midBox.setRange(0, 2000)
        self.midBox.setValue(cfg.get("center", 1500))
        self.midBox.setAlignment(QtCore.Qt.AlignLeft)
        self.midBox.setMaximumWidth(70)
        self.midBox.setFixedHeight(WIDGET_HEIGHT)

        self.maxBox = QtWidgets.QSpinBox()
        self.maxBox.setRange(0, 2000)
        self.maxBox.setValue(cfg.get("max", 2000))
        self.maxBox.setAlignment(QtCore.Qt.AlignLeft)
        self.maxBox.setMaximumWidth(70)
        self.maxBox.setFixedHeight(WIDGET_HEIGHT)

        self.mapBtn = QtWidgets.QPushButton("Map")
        self.mapBtn.setFixedWidth(50)
        self.mapBtn.setFixedHeight(WIDGET_HEIGHT)

        self.multiButtonBtn = QtWidgets.QPushButton("Configure")
        self.multiButtonBtn.setMinimumWidth(105)
        self.multiButtonBtn.setFixedHeight(WIDGET_HEIGHT)
        self.multiButtonBtn.setVisible(False)
        self.multiButtonBtn.setEnabled(False)

        # Update progress bar range based on min/max values
        def update_bar_range():
            self.bar.setRange(self.minBox.value(), self.maxBox.value())

        self.minBox.valueChanged.connect(update_bar_range)
        self.maxBox.valueChanged.connect(update_bar_range)
        update_bar_range()

        # Top row: Name, Axis, IDX, Map/Configure, %, 1500
        topLayout = QtWidgets.QHBoxLayout()
        topLayout.addWidget(self.nameBox)
        topLayout.addWidget(self.src)
        self.idxLbl = QtWidgets.QLabel("id")
        self.idxLbl.setMaximumWidth(30)
        self.idxLbl.setFixedHeight(WIDGET_HEIGHT)
        topLayout.addWidget(self.idxLbl)
        topLayout.addWidget(self.idxBox)
        topLayout.addWidget(self.mapBtn)
        topLayout.addWidget(self.multiButtonBtn)
        topLayout.addWidget(self.bar, 1)  # progress bar gets stretch
        topLayout.addWidget(self.val)
        layout.addLayout(topLayout, 0, 0, 1, 15)

        # Bottom row: Min, Mid, Max, Toggle, Toggle Group, Rotary, Rotary Stops, Reverse
        self.minLbl = QtWidgets.QLabel("Min")
        self.minLbl.setMaximumWidth(30)
        self.minLbl.setFixedHeight(WIDGET_HEIGHT)
        layout.addWidget(self.minLbl, 1, 0)
        layout.addWidget(self.minBox, 1, 1)
        self.midLbl = QtWidgets.QLabel("Mid")
        self.midLbl.setMaximumWidth(30)
        self.midLbl.setFixedHeight(WIDGET_HEIGHT)
        layout.addWidget(self.midLbl, 1, 2)
        layout.addWidget(self.midBox, 1, 3)
        self.maxLbl = QtWidgets.QLabel("Max")
        self.maxLbl.setMaximumWidth(30)
        self.maxLbl.setFixedHeight(WIDGET_HEIGHT)
        layout.addWidget(self.maxLbl, 1, 4)
        layout.addWidget(self.maxBox, 1, 5)
        layout.addWidget(self.inv, 1, 6)
        layout.addWidget(self.toggleBox, 1, 7)
        layout.addWidget(self.toggleGroupBox, 1, 8)
        layout.addWidget(self.rotaryBox, 1, 9)
        layout.addWidget(self.rotaryStopsBox, 1, 10)
        layout.addWidget(self.expoLbl, 1, 11)
        layout.addWidget(self.expoBox, 1, 12)

        # Add spacer to prevent bottom row from stretching
        layout.setColumnStretch(13, 1)  # Column 13 absorbs extra space

        # Connect signals
        self.nameBox.textChanged.connect(self.changed.emit)
        self.src.currentIndexChanged.connect(self._update_visual_state)
        self.rotaryBox.toggled.connect(self._update_visual_state)
        self.toggleBox.toggled.connect(self._on_toggle_changed)
        self.toggleGroupBox.currentIndexChanged.connect(self.changed.emit)
        self.rotaryBox.toggled.connect(self._on_rotary_changed)

        for w in [self.src, self.idxBox, self.inv, self.minBox, self.midBox, self.maxBox]:
            if isinstance(w, QtWidgets.QAbstractButton):
                w.toggled.connect(self.changed.emit)
            else:
                if isinstance(w, QtWidgets.QComboBox):
                    w.currentIndexChanged.connect(self.changed.emit)
                else:
                    w.valueChanged.connect(self.changed.emit)

        for w in [self.rotaryStopsBox, self.expoBox]:
            w.valueChanged.connect(self.changed.emit)

        self.mapBtn.clicked.connect(self._on_map)
        self.multiButtonBtn.clicked.connect(self._on_configure_multibutton)

        # Initial visual state
        self._update_visual_state()

        # Button state tracking
        self._btn_last = 0
        self._btn_toggle_state = 0
        self._btn_rotary_state = 0
        self._prev_btn_idx = self.idxBox.value()
        self._toggle_activated_time = 0  # Track when toggle was last activated
        self._multi_button_map = cfg.get("multi_button_map", {})  # Multi-button mapping
        self._multi_button_default_idx = self._multi_button_map.get("__default_btn__", None)  # Default button index
        self._multi_button_last_states = {}  # Track last state of each multi-button
        # Initialize current value to default button's value if available
        if self._multi_button_default_idx is not None:
            default_idx_str = str(self._multi_button_default_idx)
            self._multi_button_current_value = self._multi_button_map.get(default_idx_str, None)
        else:
            self._multi_button_current_value = None
        self._active_multibutton_dialog = None  # Reference to active dialog for mapping

    def _on_map(self):
        """Handle map button click."""
        self.mapRequested.emit(self)

    def _on_configure_multibutton(self):
        """Handle multi-button configuration button click."""
        dialog = MultiButtonDialog(
            self._multi_button_map,
            self.minBox.value(),
            self.maxBox.value(),
            self
        )
        self._active_multibutton_dialog = dialog  # Store reference for mapping
        if dialog.exec_() == QtWidgets.QDialog.Accepted:
            self._multi_button_map = dialog.get_button_map()
            # Extract and update default button index
            self._multi_button_default_idx = self._multi_button_map.get("__default_btn__", None)
            # Initialize current value to default button's value if available
            if self._multi_button_default_idx is not None:
                default_idx_str = str(self._multi_button_default_idx)
                self._multi_button_current_value = self._multi_button_map.get(default_idx_str, None)
            else:
                self._multi_button_current_value = None
            # Initialize last states (excluding metadata key)
            self._multi_button_last_states = {btn_idx: 0 for btn_idx in self._multi_button_map if btn_idx != "__default_btn__"}
            self.changed.emit()
        self._active_multibutton_dialog = None

    def _update_visual_state(self):
        """Update visual state based on source selection."""
        is_mapped = self.src.currentText() != "none"
        src = self.src.currentText()
        is_axis = src == "axis"
        is_multi = src == "multi"

        # List of widgets to enable/disable (Map button excluded - always enabled)
        widgets_to_control = [
            self.nameBox,
            self.bar,
            self.val,
            self.idxBox,
            self.inv,
            self.minBox,
            self.midBox,
            self.maxBox,
        ]

        # List of labels to gray out when disabled
        labels_to_control = [
            self.idxLbl,
            self.minLbl,
            self.midLbl,
            self.maxLbl,
        ]

        for widget in widgets_to_control:
            widget.setEnabled(is_mapped)
            if not is_mapped:
                widget.setStyleSheet("color: #666666;")
            else:
                widget.setStyleSheet("")

        # Gray out labels when channel is disabled
        for label in labels_to_control:
            if not is_mapped:
                label.setStyleSheet("color: #666666;")
            else:
                label.setStyleSheet("")

        # Check current states
        is_toggle_checked = self.toggleBox.isChecked()
        is_rotary_checked = self.rotaryBox.isChecked()

        # Toggle and rotary only enabled for button source (and not multi)
        self.toggleBox.setEnabled(is_mapped and not is_axis and not is_multi)
        self.rotaryBox.setEnabled(is_mapped and not is_axis and not is_multi)

        if not is_mapped or is_axis or is_multi:
            self.toggleBox.setStyleSheet("color: #666666;")
            self.rotaryBox.setStyleSheet("color: #666666;")
            # Uncheck toggle and rotary if not a button source or multi-button
            if is_toggle_checked or is_rotary_checked:
                self.toggleBox.blockSignals(True)
                self.rotaryBox.blockSignals(True)
                self.toggleBox.setChecked(False)
                self.rotaryBox.setChecked(False)
                self.toggleGroupBox.setEnabled(False)
                self.toggleBox.blockSignals(False)
                self.rotaryBox.blockSignals(False)
                self.changed.emit()
        else:
            self.toggleBox.setStyleSheet("")
            self.rotaryBox.setStyleSheet("")

        # Hide toggle and rotary widgets for multi or when none selected
        self.toggleBox.setVisible(is_mapped and not is_multi)
        self.toggleGroupBox.setVisible(is_mapped and not is_multi)
        self.rotaryBox.setVisible(is_mapped and not is_multi)
        self.rotaryStopsBox.setVisible(is_mapped and not is_multi)

        # Inv button disabled if rotary is selected
        is_rotary = self.rotaryBox.isChecked()
        self.inv.setEnabled(is_mapped and not is_rotary)
        if not is_mapped or is_rotary:
            self.inv.setStyleSheet("color: #666666;")
        else:
            self.inv.setStyleSheet("")

        # Expo only visible for axis source
        self.expoLbl.setVisible(is_axis)
        self.expoBox.setVisible(is_axis)
        self.expoBox.setEnabled(is_axis)

        # Rotary stops only enabled if rotary is checked
        self.rotaryStopsBox.setEnabled(is_mapped and is_rotary_checked)

        # Toggle group box only enabled if toggle is checked
        self.toggleGroupBox.setEnabled(is_mapped and is_toggle_checked)

        # Gray out disabled controls
        if not is_toggle_checked or not is_mapped:
            self.toggleGroupBox.setStyleSheet("color: #666666;")
        else:
            self.toggleGroupBox.setStyleSheet("")

        if not is_rotary_checked or not is_mapped:
            self.rotaryStopsBox.setStyleSheet("color: #666666;")
        else:
            self.rotaryStopsBox.setStyleSheet("")

        # Map button is always enabled
        self.mapBtn.setEnabled(True)
        self.mapBtn.setStyleSheet("")

        # Multi configure button only visible for multi source
        self.multiButtonBtn.setVisible(is_multi)
        self.multiButtonBtn.setEnabled(is_multi)

        # Hide idx controls for multi or when none selected (buttons are configured in dialog)
        self.idxLbl.setVisible(is_mapped and not is_multi)
        self.idxBox.setVisible(is_mapped and not is_multi)

        # Hide reverse for multi or when none selected
        self.inv.setVisible(is_mapped and not is_multi)

        # Hide entire bottom row for multi or when none selected
        self.minLbl.setVisible(is_mapped and not is_multi)
        self.minBox.setVisible(is_mapped and not is_multi)
        self.midLbl.setVisible(is_mapped and not is_multi)
        self.midBox.setVisible(is_mapped and not is_multi)
        self.maxLbl.setVisible(is_mapped and not is_multi)
        self.maxBox.setVisible(is_mapped and not is_multi)

        # Hide Map button when multi is selected
        self.mapBtn.setVisible(not is_multi)

        # Set default output value based on mapped state
        if not is_mapped:
            default_val = 1000
            self.bar.setValue(default_val)
            self.val.setText(str(default_val))

    def _on_toggle_changed(self):
        """Handle toggle checkbox - enable/disable toggle group box based on toggle state."""
        is_toggle_checked = self.toggleBox.isChecked()

        # Enable/disable toggle group box based on toggle state
        self.toggleGroupBox.setEnabled(is_toggle_checked)

        self.changed.emit()

    def _on_rotary_changed(self):
        """Handle rotary checkbox - update visual state."""
        self._update_visual_state()
        self.changed.emit()

    def compute(self, axes, btns):
        """Compute output value based on current joystick state.

        Args:
            axes: List of axis values
            btns: List of button states

        Returns:
            Channel output value (1000-2000)
        """
        src = self.src.currentText()
        idx = self.idxBox.value()
        inv = self.inv.isChecked()
        mn = self.minBox.value()
        ct = self.midBox.value()
        mx = self.maxBox.value()
        expo = self.expoBox.value()
        rotary = self.rotaryBox.isChecked()
        rotary_stops = self.rotaryStopsBox.value()

        # Handle button index changes
        if idx != self._prev_btn_idx:
            self._btn_last = btns[idx] if idx < len(btns) else 0
            self._prev_btn_idx = idx

        if src == "axis":
            v = axes[idx] if idx < len(axes) else 0.0
            out = map_axis_to_range(v, inv, mn, ct, mx, expo)
        elif src == "button":
            v = btns[idx] if idx < len(btns) else 0

            if rotary:
                # Rotary mode: cycle through stops on button press
                if self._btn_last == 0 and v == 1:
                    self._btn_rotary_state = (self._btn_rotary_state + 1) % rotary_stops
                # Calculate output value based on current stop
                stop_range = mx - mn
                if rotary_stops > 1:
                    stop_value = stop_range / (rotary_stops - 1)
                    out = int(mn + self._btn_rotary_state * stop_value)
                else:
                    out = mn
            elif self.toggleBox.isChecked():
                # Toggle mode: on/off state
                if self._btn_last == 0 and v == 1:
                    self._btn_toggle_state = 0 if self._btn_toggle_state else 1
                    # Track when this toggle was activated
                    import time
                    self._toggle_activated_time = time.time() if self._btn_toggle_state else 0
                eff = self._btn_toggle_state
                out = mx if (eff ^ inv) else mn
            else:
                # Direct mode: button press = max, release = min
                eff = v
                out = mx if (eff ^ inv) else mn
            self._btn_last = v
        elif src == "multi":
            # Multi mode: check each configured button and set value
            # Check for any button presses and update the current value
            for btn_idx_str, btn_val in self._multi_button_map.items():
                # Skip metadata keys
                if btn_idx_str == "__default_btn__":
                    continue

                btn_idx = int(btn_idx_str)
                v = btns[btn_idx] if btn_idx < len(btns) else 0

                # Initialize last state if needed
                if btn_idx_str not in self._multi_button_last_states:
                    self._multi_button_last_states[btn_idx_str] = 0

                # Detect button press (transition from 0 to 1)
                if self._multi_button_last_states[btn_idx_str] == 0 and v == 1:
                    self._multi_button_current_value = btn_val

                self._multi_button_last_states[btn_idx_str] = v

            # Check if dialog is open and waiting for button press
            if hasattr(self, '_active_multibutton_dialog') and self._active_multibutton_dialog:
                # Update highlighting in dialog based on current button states
                self._active_multibutton_dialog.update_button_highlight(btns)

                # Dialog is waiting for button mapping - detect any button press
                if self._active_multibutton_dialog._mapping_row is not None:
                    for btn_idx in range(len(btns)):
                        v = btns[btn_idx]
                        if btn_idx not in self._multi_button_last_states:
                            self._multi_button_last_states[btn_idx] = 0
                        # Detect button press (any button being mapped)
                        if self._multi_button_last_states[btn_idx] == 0 and v == 1:
                            self._active_multibutton_dialog.set_mapped_button(btn_idx)
                        self._multi_button_last_states[btn_idx] = v

            # Use the current value if set, otherwise minimum
            out = self._multi_button_current_value if self._multi_button_current_value is not None else mn
        else:
            # src == "none": non-mapped channel
            out = mn

        self.bar.setValue(out)
        self.val.setText(str(out))
        return out

    def to_cfg(self):
        """Convert current settings to configuration dictionary.

        Returns:
            Configuration dictionary
        """
        # Convert dropdown index to saved value: 0 -> -1, 1 -> 0, 2 -> 1, etc.
        dropdown_index = self.toggleGroupBox.currentIndex()
        if dropdown_index == 0:
            saved_group = -1  # "None"
        else:
            saved_group = dropdown_index - 1  # Groups 1-8 stored as 0-7

        cfg = {
            "name": self.nameBox.text(),
            "src": self.src.currentText(),
            "idx": self.idxBox.value(),
            "inv": self.inv.isChecked(),
            "expo": self.expoBox.value(),
            "toggle": self.toggleBox.isChecked(),
            "toggle_group": saved_group,
            "rotary": self.rotaryBox.isChecked(),
            "rotary_stops": self.rotaryStopsBox.value(),
            "min": self.minBox.value(),
            "center": self.midBox.value(),
            "max": self.maxBox.value(),
        }
        # Add multi-button map if present
        if self._multi_button_map:
            cfg["multi_button_map"] = self._multi_button_map
        return cfg

    def set_mapping(self, src: str, idx: int):
        """Set channel mapping.

        Args:
            src: Source type ("axis", "button", "multi", or "none")
            idx: Source index
        """
        # If dialog is active, pass the mapping to it
        if hasattr(self, '_active_multibutton_dialog') and self._active_multibutton_dialog:
            self._active_multibutton_dialog.set_mapped_button(idx)
        else:
            # Normal mapping for non-multi channels
            if src in SRC_CHOICES:
                self.src.setCurrentText(src)
            self.idxBox.setValue(idx)
