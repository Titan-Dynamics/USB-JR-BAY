"""
Serial Interface Module

Minimal serial port connection management (no CRSF, no telemetry, no parameters).
Just connects to the port and reports connection status.
"""

import time
import serial
from PyQt5 import QtCore


class SerialThread(QtCore.QObject):
    """Thread object for managing serial port connection.

    This class handles:
    - Serial port connection/reconnection
    - Connection status reporting

    Signals:
        debug: Emitted with debug/log messages
        connection_status: Emitted with True/False for connected/disconnected
    """

    debug = QtCore.pyqtSignal(str)
    connection_status = QtCore.pyqtSignal(bool)  # True = connected, False = disconnected

    def __init__(self, port, baud):
        """Initialize the serial thread.

        Args:
            port: Serial port name (e.g., 'COM3' or '/dev/ttyUSB0')
            baud: Baud rate (typically 5250000 for ELRS)
        """
        super().__init__()
        self.port = port
        self.baud = baud
        self.ser = None
        self.running = True
        self._last_status = False
        self._initial_connect_attempted = False

    def _connect(self):
        """Attempt to connect to the serial port."""
        try:
            if self.ser:
                try:
                    self.ser.close()
                except:
                    pass
            self.ser = serial.Serial(self.port, self.baud, timeout=0.001, write_timeout=1.0)
            # Flush any stale data from the input buffer
            self.ser.reset_input_buffer()
            self.debug.emit(f"Connected to {self.port} @ {self.baud} baud")
            self._update_status()
        except Exception as e:
            self.debug.emit(f"Failed to connect to {self.port}: {e}")
            self.ser = None
            self._update_status()

    def _update_status(self):
        """Emit connection status if it changed."""
        is_connected = self.ser is not None
        if is_connected != self._last_status:
            self._last_status = is_connected
            try:
                self.connection_status.emit(is_connected)
            except Exception as e:
                print(f"Error emitting connection status: {e}")

    def reconnect(self, port, baud):
        """Reconnect with new port/baud settings.

        Args:
            port: New serial port name
            baud: New baud rate
        """
        self.port = port
        self.baud = baud
        self._connect()

    def close(self):
        """Close the serial connection and stop the thread."""
        self.running = False
        try:
            if self.ser:
                self.ser.close()
        except:
            pass

    def update_channels(self, channels):
        """Receive computed channel values from main GUI thread.

        This method exists for compatibility but does nothing (we don't send data).

        Args:
            channels: List of 16 channel values (1000-2000)
        """
        pass  # No-op - we don't send anything

    def run(self):
        """Main thread loop: maintains connection."""
        while self.running:
            if not self.ser:
                self._update_status()
                # Only auto-reconnect if initial connection was already attempted
                if self._initial_connect_attempted:
                    self._connect()
                time.sleep(0.5)
                continue
            try:
                # Just keep the connection alive, don't read or write
                time.sleep(0.1)
            except Exception as e:
                self.ser = None
                self._update_status()
                time.sleep(0.2)
