"""
Channel UI Components

This module contains UI components for channel configuration and mapping.
"""

import math
from PyQt5 import QtWidgets, QtCore


# Source type choices for channel mapping
SRC_CHOICES = ["axis", "button", "none"]


def map_axis_to_range(val, inv, mn, ct, mx):
    """Map joystick axis value to output range.

    Args:
        val: Axis value from joystick (-1.0 to 1.0)
        inv: Whether to invert the axis
        mn: Minimum output value
        ct: Center output value
        mx: Maximum output value

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

        # Widgets
        self.lbl = QtWidgets.QLabel(name)
        self.lbl.setMaximumWidth(50)

        self.nameBox = QtWidgets.QLineEdit()
        self.nameBox.setPlaceholderText("Name")
        self.nameBox.setMaximumWidth(100)
        default_names = ["Ail", "Elev", "Thr", "Rudd", "Arm", "Mode"]
        default_name = default_names[idx] if idx < len(default_names) else ""
        self.nameBox.setText(cfg.get("name", default_name))

        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 2000)

        self.val = QtWidgets.QLabel("1500")

        self.src = QtWidgets.QComboBox()
        self.src.addItems(SRC_CHOICES)
        self.src.setMaximumWidth(80)
        self.src.setCurrentText(cfg.get("src", "none"))

        self.idxBox = QtWidgets.QSpinBox()
        self.idxBox.setRange(0, 63)
        self.idxBox.setValue(cfg.get("idx", 0))
        self.idxBox.setMaximumWidth(60)

        self.inv = QtWidgets.QCheckBox("Reverse")
        self.inv.setChecked(cfg.get("inv", False))
        self.inv.setMaximumWidth(80)

        self.toggleBox = QtWidgets.QCheckBox("Toggle")
        self.toggleBox.setChecked(cfg.get("toggle", False))
        self.toggleBox.setMaximumWidth(80)

        self.toggleGroupBox = QtWidgets.QComboBox()
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
        self.toggleGroupBox.setEnabled(cfg.get("toggle", False))

        self.rotaryBox = QtWidgets.QCheckBox("Rotary")
        self.rotaryBox.setChecked(cfg.get("rotary", False))
        self.rotaryBox.setMaximumWidth(80)

        self.rotaryStopsBox = QtWidgets.QSpinBox()
        self.rotaryStopsBox.setRange(3, 6)
        self.rotaryStopsBox.setValue(cfg.get("rotary_stops", 3))
        self.rotaryStopsBox.setMaximumWidth(60)
        self.rotaryStopsBox.setEnabled(cfg.get("rotary", False))

        self.minBox = QtWidgets.QSpinBox()
        self.minBox.setRange(0, 2000)
        self.minBox.setValue(cfg.get("min", 1000))
        self.minBox.setAlignment(QtCore.Qt.AlignLeft)
        self.minBox.setMaximumWidth(70)

        self.midBox = QtWidgets.QSpinBox()
        self.midBox.setRange(0, 2000)
        self.midBox.setValue(cfg.get("center", 1500))
        self.midBox.setAlignment(QtCore.Qt.AlignLeft)
        self.midBox.setMaximumWidth(70)

        self.maxBox = QtWidgets.QSpinBox()
        self.maxBox.setRange(0, 2000)
        self.maxBox.setValue(cfg.get("max", 2000))
        self.maxBox.setAlignment(QtCore.Qt.AlignLeft)
        self.maxBox.setMaximumWidth(70)

        self.mapBtn = QtWidgets.QPushButton("Map")
        self.mapBtn.setMaximumWidth(70)

        # Update progress bar range based on min/max values
        def update_bar_range():
            self.bar.setRange(self.minBox.value(), self.maxBox.value())

        self.minBox.valueChanged.connect(update_bar_range)
        self.maxBox.valueChanged.connect(update_bar_range)
        update_bar_range()

        # Top row: CHX, Name, Axis, IDX, Map, %, 1500
        topLayout = QtWidgets.QHBoxLayout()
        topLayout.addWidget(self.lbl)
        topLayout.addWidget(self.nameBox)
        topLayout.addWidget(self.src)
        self.idxLbl = QtWidgets.QLabel("idx")
        self.idxLbl.setMaximumWidth(30)
        topLayout.addWidget(self.idxLbl)
        topLayout.addWidget(self.idxBox)
        topLayout.addWidget(self.mapBtn)
        topLayout.addWidget(self.bar, 1)  # progress bar gets stretch
        topLayout.addWidget(self.val)
        layout.addLayout(topLayout, 0, 0, 1, 15)

        # Bottom row: Min, Mid, Max, Toggle, Toggle Group, Rotary, Rotary Stops, Reverse
        self.minLbl = QtWidgets.QLabel("Min")
        self.minLbl.setMaximumWidth(30)
        layout.addWidget(self.minLbl, 1, 0)
        layout.addWidget(self.minBox, 1, 1)
        self.midLbl = QtWidgets.QLabel("Mid")
        self.midLbl.setMaximumWidth(30)
        layout.addWidget(self.midLbl, 1, 2)
        layout.addWidget(self.midBox, 1, 3)
        self.maxLbl = QtWidgets.QLabel("Max")
        self.maxLbl.setMaximumWidth(30)
        layout.addWidget(self.maxLbl, 1, 4)
        layout.addWidget(self.maxBox, 1, 5)
        layout.addWidget(self.toggleBox, 1, 6)
        layout.addWidget(self.toggleGroupBox, 1, 7)
        layout.addWidget(self.rotaryBox, 1, 8)
        layout.addWidget(self.rotaryStopsBox, 1, 9)
        layout.addWidget(self.inv, 1, 10)

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

        for w in [self.rotaryStopsBox]:
            w.valueChanged.connect(self.changed.emit)

        self.mapBtn.clicked.connect(self._on_map)

        # Initial visual state
        self._update_visual_state()

        # Button state tracking
        self._btn_last = 0
        self._btn_toggle_state = 0
        self._btn_rotary_state = 0
        self._prev_btn_idx = self.idxBox.value()
        self._toggle_activated_time = 0  # Track when toggle was last activated

    def _on_map(self):
        """Handle map button click."""
        self.mapRequested.emit(self)

    def _update_visual_state(self):
        """Update visual state based on source selection."""
        is_mapped = self.src.currentText() != "none"
        src = self.src.currentText()
        is_axis = src == "axis"

        # List of widgets to enable/disable (Map button excluded - always enabled)
        widgets_to_control = [
            self.lbl,
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

        # Toggle and rotary only enabled for button source
        self.toggleBox.setEnabled(is_mapped and not is_axis)
        self.rotaryBox.setEnabled(is_mapped and not is_axis)
        if not is_mapped or is_axis:
            self.toggleBox.setStyleSheet("color: #666666;")
            self.rotaryBox.setStyleSheet("color: #666666;")
        else:
            self.toggleBox.setStyleSheet("")
            self.rotaryBox.setStyleSheet("")

        # Inv button disabled if rotary is selected
        is_rotary = self.rotaryBox.isChecked()
        self.inv.setEnabled(is_mapped and not is_rotary)
        if not is_mapped or is_rotary:
            self.inv.setStyleSheet("color: #666666;")
        else:
            self.inv.setStyleSheet("")

        # Rotary stops only enabled if rotary is checked
        self.rotaryStopsBox.setEnabled(is_mapped and self.rotaryBox.isChecked())

        # Map button is always enabled
        self.mapBtn.setEnabled(True)
        self.mapBtn.setStyleSheet("")

        # Set default output value based on mapped state
        if not is_mapped:
            default_val = 1000
            self.bar.setValue(default_val)
            self.val.setText(str(default_val))

    def _on_toggle_changed(self):
        """Handle toggle checkbox - uncheck rotary if toggle is checked."""
        if self.toggleBox.isChecked() and self.rotaryBox.isChecked():
            self.rotaryBox.blockSignals(True)
            self.rotaryBox.setChecked(False)
            self.rotaryBox.blockSignals(False)
            self._update_visual_state()
        # Enable/disable toggle group box based on toggle state
        self.toggleGroupBox.setEnabled(self.toggleBox.isChecked())
        self.changed.emit()

    def _on_rotary_changed(self):
        """Handle rotary checkbox - uncheck toggle if rotary is checked."""
        if self.rotaryBox.isChecked() and self.toggleBox.isChecked():
            self.toggleBox.blockSignals(True)
            self.toggleBox.setChecked(False)
            self.toggleBox.blockSignals(False)
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
        rotary = self.rotaryBox.isChecked()
        rotary_stops = self.rotaryStopsBox.value()

        # Handle button index changes
        if idx != self._prev_btn_idx:
            self._btn_last = btns[idx] if idx < len(btns) else 0
            self._prev_btn_idx = idx

        if src == "axis":
            v = axes[idx] if idx < len(axes) else 0.0
            out = map_axis_to_range(v, inv, mn, ct, mx)
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
            try:
                self.debug.emit(
                    f"CH{self.idx+1} button idx={idx} raw={v}, inv={inv}, min={mn}, max={mx} -> out={out}"
                )
            except Exception:
                pass
            self._btn_last = v
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

        return {
            "name": self.nameBox.text(),
            "src": self.src.currentText(),
            "idx": self.idxBox.value(),
            "inv": self.inv.isChecked(),
            "toggle": self.toggleBox.isChecked(),
            "toggle_group": saved_group,
            "rotary": self.rotaryBox.isChecked(),
            "rotary_stops": self.rotaryStopsBox.value(),
            "min": self.minBox.value(),
            "center": self.midBox.value(),
            "max": self.maxBox.value(),
        }

    def set_mapping(self, src: str, idx: int):
        """Set channel mapping.

        Args:
            src: Source type ("axis", "button", or "none")
            idx: Source index
        """
        if src in SRC_CHOICES:
            self.src.setCurrentText(src)
        self.idxBox.setValue(idx)
