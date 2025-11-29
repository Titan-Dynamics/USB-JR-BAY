"""
Channel UI Components

This module contains UI components for channel configuration and mapping.
"""

import math
from PyQt5 import QtWidgets, QtCore


# Source type choices for channel mapping
SRC_CHOICES = ["axis", "button", "const"]


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
        self.val.setMaximumWidth(60)
        self.val.setMinimumWidth(60)

        self.src = QtWidgets.QComboBox()
        self.src.addItems(SRC_CHOICES)
        self.src.setMaximumWidth(80)
        self.src.setCurrentText(cfg.get("src", "const"))

        self.idxBox = QtWidgets.QSpinBox()
        self.idxBox.setRange(0, 63)
        self.idxBox.setValue(cfg.get("idx", 0))
        self.idxBox.setMaximumWidth(60)

        self.inv = QtWidgets.QCheckBox("inv")
        self.inv.setChecked(cfg.get("inv", False))
        self.inv.setMaximumWidth(60)

        self.toggleBox = QtWidgets.QCheckBox("Toggle")
        self.toggleBox.setChecked(cfg.get("toggle", False))
        self.toggleBox.setMaximumWidth(80)

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

        # Top row: full-width with scaling elements
        topLayout = QtWidgets.QHBoxLayout()
        topLayout.addWidget(self.lbl)
        topLayout.addWidget(self.nameBox)
        topLayout.addWidget(self.bar, 1)  # progress bar gets stretch
        topLayout.addWidget(self.val)
        layout.addLayout(topLayout, 0, 0, 1, 15)

        # Bottom row: fixed-width controls
        srcLbl = QtWidgets.QLabel("src")
        srcLbl.setMaximumWidth(30)
        layout.addWidget(srcLbl, 1, 0)
        layout.addWidget(self.src, 1, 1)
        idxLbl = QtWidgets.QLabel("idx")
        idxLbl.setMaximumWidth(30)
        layout.addWidget(idxLbl, 1, 2)
        layout.addWidget(self.idxBox, 1, 3)
        layout.addWidget(self.inv, 1, 4)
        minLbl = QtWidgets.QLabel("min")
        minLbl.setMaximumWidth(30)
        layout.addWidget(minLbl, 1, 5)
        layout.addWidget(self.minBox, 1, 6)
        midLbl = QtWidgets.QLabel("mid")
        midLbl.setMaximumWidth(30)
        layout.addWidget(midLbl, 1, 7)
        layout.addWidget(self.midBox, 1, 8)
        maxLbl = QtWidgets.QLabel("max")
        maxLbl.setMaximumWidth(30)
        layout.addWidget(maxLbl, 1, 9)
        layout.addWidget(self.maxBox, 1, 10)
        layout.addWidget(self.mapBtn, 1, 11)
        layout.addWidget(self.toggleBox, 1, 12)
        layout.addWidget(self.rotaryBox, 1, 13)
        layout.addWidget(self.rotaryStopsBox, 1, 14)

        # Connect signals
        self.nameBox.textChanged.connect(self.changed.emit)
        self.src.currentIndexChanged.connect(self._update_visual_state)
        self.rotaryBox.toggled.connect(self._update_visual_state)
        self.toggleBox.toggled.connect(self._on_toggle_changed)
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

    def _on_map(self):
        """Handle map button click."""
        self.mapRequested.emit(self)

    def _update_visual_state(self):
        """Update visual state based on source selection."""
        is_mapped = self.src.currentText() != "const"
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

        for widget in widgets_to_control:
            widget.setEnabled(is_mapped)
            if not is_mapped:
                widget.setStyleSheet("color: gray;")
            else:
                widget.setStyleSheet("")

        # Toggle and rotary only enabled for button source
        self.toggleBox.setEnabled(is_mapped and not is_axis)
        self.rotaryBox.setEnabled(is_mapped and not is_axis)
        if is_axis:
            self.toggleBox.setStyleSheet("color: gray;")
            self.rotaryBox.setStyleSheet("color: gray;")
        else:
            self.toggleBox.setStyleSheet("")
            self.rotaryBox.setStyleSheet("")

        # Inv button disabled if rotary is selected
        is_rotary = self.rotaryBox.isChecked()
        self.inv.setEnabled(is_mapped and not is_rotary)
        if is_rotary:
            self.inv.setStyleSheet("color: gray;")
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
            # src == "const": unmapped channel
            out = mn

        self.bar.setValue(out)
        self.val.setText(str(out))
        return out

    def to_cfg(self):
        """Convert current settings to configuration dictionary.

        Returns:
            Configuration dictionary
        """
        return {
            "name": self.nameBox.text(),
            "src": self.src.currentText(),
            "idx": self.idxBox.value(),
            "inv": self.inv.isChecked(),
            "toggle": self.toggleBox.isChecked(),
            "rotary": self.rotaryBox.isChecked(),
            "rotary_stops": self.rotaryStopsBox.value(),
            "min": self.minBox.value(),
            "center": self.midBox.value(),
            "max": self.maxBox.value(),
        }

    def set_mapping(self, src: str, idx: int):
        """Set channel mapping.

        Args:
            src: Source type ("axis", "button", or "const")
            idx: Source index
        """
        if src in SRC_CHOICES:
            self.src.setCurrentText(src)
        self.idxBox.setValue(idx)
