"""
Serial Interface Module

Handles serial port connection management and raw frame transmission.
"""

import time
import serial
import serial.tools.list_ports
from PyQt5 import QtCore


def get_available_ports():
    """Get list of available serial ports.

    Returns:
        List of tuples (port, description)
    """
    ports = []
    for port, desc, hwid in sorted(serial.tools.list_ports.comports()):
        ports.append((port, desc))
    return ports


class SerialInterface(QtCore.QObject):
    """Object for managing serial port connection.

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
        self._last_connect_error = None  # suppress repeated identical failure messages

    def _connect(self):
        """Attempt to connect to the serial port."""
        try:
            if self.ser:
                try:
                    self.ser.close()
                except:
                    pass
            self.ser = serial.Serial(self.port, self.baud, timeout=0.001, write_timeout=0.05)
            # Flush any stale data from the input buffer
            self.ser.reset_input_buffer()
            self._last_connect_error = None
            self.debug.emit(f"Connected to {self.port} @ {self.baud} baud")
            self._update_status()
        except Exception as e:
            err = str(e)
            if err != self._last_connect_error:
                self._last_connect_error = err
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

    def _handle_disconnect(self):
        """Close a broken port so the run loop will reconnect."""
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self._last_connect_error = None  # allow first reconnect failure to be logged
        self._update_status()

    def reconnect(self, port, baud):
        """Reconnect with new port/baud settings.

        Args:
            port: New serial port name
            baud: New baud rate
        """
        self.port = port
        self.baud = baud
        self._connect()

    def available(self) -> int:
        """Check how many bytes are available to read.

        Returns:
            Number of bytes in the receive buffer
        """
        if self.ser:
            try:
                return self.ser.in_waiting
            except Exception as e:
                self.debug.emit(f"Error checking available bytes: {e}")
                self._handle_disconnect()
        return 0

    def read(self) -> int:
        """Read a single byte from the serial port.

        Returns:
            Single byte (0-255) or -1 if no data available
        """
        if self.ser:
            try:
                data = self.ser.read(1)
                if len(data) > 0:
                    return data[0]
            except Exception as e:
                self.debug.emit(f"Error reading from serial: {e}")
                self._handle_disconnect()
        return -1

    def read_bulk(self, n: int) -> bytes:
        """Read up to n bytes from the serial port in one syscall.

        Args:
            n: Maximum number of bytes to read

        Returns:
            Bytes read (may be fewer than n if less is available)
        """
        if self.ser:
            try:
                data = self.ser.read(n)
                return data if data else b''
            except Exception as e:
                self.debug.emit(f"Error bulk reading from serial: {e}")
                self._handle_disconnect()
        return b''

    def write(self, data: bytes) -> int:
        """Write bytes to the serial port.

        Args:
            data: Bytes to write

        Returns:
            Number of bytes written, or 0 on error
        """
        if self.ser:
            try:
                return self.ser.write(data)
            except serial.SerialTimeoutException:
                self.debug.emit("Serial write timeout (transient, will retry)")
                return 0
            except Exception as e:
                self.debug.emit(f"Error writing to serial: {e}")
                self._handle_disconnect()
        return 0

    def close(self):
        """Close the serial connection and stop the thread."""
        self.running = False
        try:
            if self.ser:
                self.ser.close()
        except:
            pass

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
                # Just keep the connection alive
                time.sleep(0.1)
            except Exception as e:
                self.debug.emit(f"Error in serial thread: {e}")
                self.ser = None
                self._update_status()
                time.sleep(0.2)
