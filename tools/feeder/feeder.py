#!/usr/bin/env python3
# ELRS Calibration GUI + Telemetry (PyQt5)
# pip install pyqt5 pygame pyserial

import sys, json, time, struct, threading
from PyQt5 import QtWidgets, QtCore
import pygame
import re
import serial
import serial.tools.list_ports
from PyQt5.QtGui import QIcon

# Legacy USB host protocol constants removed — we now speak raw CRSF.
# Build CRSF frames directly from the host tool.
CRSF_ADDRESS_FLIGHT_CONTROLLER = 0xC8
CRSF_FRAMETYPE_RC_CHANNELS_PACKED = 0x16
CRSF_FRAMETYPE_LINK_STATISTICS = 0x14
CRSF_FRAMETYPE_HANDSET = 0x3A
CRSF_HANDSET_SUBCMD_TIMING = 0x10
CRSF_FRAMETYPE_DEVICE_PING = 0x28
CRSF_FRAMETYPE_DEVICE_INFO = 0x29
CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY = 0x2B
CRSF_FRAMETYPE_PARAMETER_READ = 0x2C
CRSF_FRAMETYPE_PARAMETER_WRITE = 0x2D
CRSF_FRAMETYPE_ELRS_STATUS = 0x2E

CRSF_ADDRESS_CRSF_TRANSMITTER = 0xEE
CRSF_ADDRESS_TRANSMITTER_LEGACY = 0xEA
CRSF_ADDRESS_ELRS_LUA = 0xEF

CHANNELS = 16
SEND_HZ = 60

DEFAULT_PORT = "COM15" if sys.platform.startswith("win") else "/dev/ttyACM0"
DEFAULT_BAUD = 5250000

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

def crc8_d5(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _us_to_crsf_val(us: int) -> int:
    """Convert microsecond pulse (1000..2000) to CRSF 11-bit unit (172..1811)."""
    try:
        u = int(us)
    except Exception:
        u = 1500
    # clamp
    if u <= 1000:
        return 172
    if u >= 2000:
        return 1811
    # scale: map 1000..2000 -> 172..1811 (span 1639)
    val = 172 + int(round((u - 1000) * 1639.0 / 1000.0))
    return max(0, min(2047, val))


def _crsf_val_to_us(val: int) -> int:
    """Convert CRSF 11-bit unit (172..1811) back to microseconds (1000..2000)."""
    try:
        v = int(val)
    except Exception:
        v = 172
    if v <= 172:
        return 1000
    if v >= 1811:
        return 2000
    us = 1000 + int(round((v - 172) * 1000.0 / 1639.0))
    return us


def build_crsf_channels_frame(ch16) -> bytes:
    """Pack 16 channels as a CRSF RC_CHANNELS_PACKED frame (22-byte payload)
    Returns the full frame: [addr][len][type][payload...][crc]
    """
    # Pack channels as 11-bit values into 22 bytes (LSB-first)
    bits = 0
    bit_pos = 0
    out = bytearray()
    for v in ch16:
        # Input channel values are expected to be microsecond pulses (1000..2000).
        # Convert to CRSF 11-bit units (172..1811) used in the RC_CHANNELS payload.
        vv = _us_to_crsf_val(int(v))
        bits |= (vv & 0x7FF) << bit_pos
        bit_pos += 11
        while bit_pos >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            bit_pos -= 8
    if bit_pos > 0:
        out.append(bits & 0xFF)
    # Ensure length is 22 bytes
    if len(out) != 22:
        # pad/truncate as needed (shouldn't happen with 16 channels)
        out = (out + bytearray(22))[:22]
    # build frame
    frame = bytearray()
    frame.append(CRSF_ADDRESS_FLIGHT_CONTROLLER)
    frame_size = 1 + len(out) + 1  # type + payload + crc
    frame.append(frame_size & 0xFF)
    frame.append(CRSF_FRAMETYPE_RC_CHANNELS_PACKED)
    frame.extend(out)
    crc = crc8_d5(bytes([CRSF_FRAMETYPE_RC_CHANNELS_PACKED]) + bytes(out))
    frame.append(crc)
    return bytes(frame)


def build_crsf_frame(ftype: int, payload: bytes) -> bytes:
    """Generic CRSF frame builder for sending commands/payloads.
    Frame format: [sync=0xC8][len][type][payload...][crc]
    len is type+payload+crc (so at minimum 1+0+1 = 2)
    """
    frame = bytearray()
    frame.append(CRSF_ADDRESS_FLIGHT_CONTROLLER)
    frame_size = 1 + len(payload) + 1
    frame.append(frame_size & 0xFF)
    frame.append(ftype & 0xFF)
    frame.extend(payload)
    crc = crc8_d5(bytes([ftype & 0xFF]) + payload)
    frame.append(crc)
    return bytes(frame)

# ---- Config ----
DEFAULT_CFG = {
    "serial_port": DEFAULT_PORT,
    "channels": [{"src": "const", "idx": 0, "inv": False, "min": 1000, "center": 1500, "max": 2000} for _ in range(CHANNELS)]
}

SRC_CHOICES = ["axis", "button", "const"]

# -------------------------------------------------------------------

def get_available_ports():
    """Get list of available COM ports"""
    ports = []
    for port, desc, hwid in sorted(serial.tools.list_ports.comports()):
        ports.append(port)
    return ports

# -------------------------------------------------------------------

class SerialThread(QtCore.QObject):
    telemetry = QtCore.pyqtSignal(dict)
    debug = QtCore.pyqtSignal(str)
    channels_update = QtCore.pyqtSignal(list)
    # (interval_us, offset_us, src) - include src address so UI can associate heartbeat
    sync_update = QtCore.pyqtSignal(int, int, int)
    connection_status = QtCore.pyqtSignal(bool)  # True = connected, False = disconnected
    device_discovered = QtCore.pyqtSignal(int, dict)  # src, details
    device_parameters_loaded = QtCore.pyqtSignal(int, dict)  # src, details
    device_parameters_progress = QtCore.pyqtSignal(int, int, int)  # src, fetched_count, total_count
    device_parameter_field_updated = QtCore.pyqtSignal(int, int, dict)  # src, fid, parsed field

    def __init__(self, port, baud):
        super().__init__()
        self.port = port
        self.baud = baud
        self.ser = None
        self.running = True
        self._last_status = False
        self._initial_connect_attempted = False
        # Shared channel buffer between UI and serial thread
        self.channels_lock = threading.Lock()
        self.latest_channels = [1500] * CHANNELS
        # Send interval in microseconds; host default is based on SEND_HZ
        self.send_interval_us = int(1000000 / SEND_HZ)
        self._last_send_time = time.perf_counter()
        # Track last joystick update time to inhibit CRSF TX when joystick disconnected
        self._last_joystick_update_sec = 0.0
        # Discovery and ELRS parameter read state (auto-discovered)
        self._last_device_ping_time = 0.0
        self._device_ping_interval = 1.0  # send pings every 1s
        self.elrs_devices = {}  # keyed by src address
        self.current_device_id = CRSF_ADDRESS_CRSF_TRANSMITTER
        # Auto trigger discovery once upon first packet from a TX
        self._auto_discovery_triggered = False
        self._first_rx_time = None  # Time when we first received data from TX
        self._discovery_delay = 0.5  # Wait 0.5 seconds after first RX before sending pings
        # Addresses pending device responses -> send pings until device responds
        self._awaiting_addresses = set()
        # Field load state for current device
        self.loadQ = []
        self.fieldChunk = 0
        self.fieldData = None
        self.fieldTimeout = 0.0
        self.field_timeout_seconds = 0.1  # 100ms timeout between param requests (matches LUA behavior)
        self.fieldRetries = {}  # Track retry count per field_id to prevent infinite loops
        self.expectChunksRemain = -1  # Expected chunks_remain value for next chunk (Lua validation)
        # Track last link stats packet time
        self.last_link_stats_time = 0.0
        # Don't connect here; let the Main class connect the signal first

    def _connect(self):
        """Attempt to connect to the serial port"""
        try:
            if self.ser:
                try:
                    self.ser.close()
                except:
                    pass
            self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
            # Flush any stale data from the input buffer (module may have buffered responses)
            self.ser.reset_input_buffer()
            self.debug.emit(f"Connected to {self.port} @ {self.baud} baud")
            self._update_status()
        except Exception as e:
            self.debug.emit(f"Failed to connect to {self.port}: {e}")
            self.ser = None
            self._update_status()
            # Reset discovery state so auto discovery will fire on reconnect
            try:
                self._on_disconnect()
            except Exception:
                pass

    def _update_status(self):
        """Emit connection status if it changed"""
        is_connected = self.ser is not None
        if is_connected != self._last_status:
            self._last_status = is_connected
            try:
                self.connection_status.emit(is_connected)
            except Exception as e:
                print(f"Error emitting connection status: {e}")
            # If we lost the connection, reset discovery state so that it restarts on reconnect
            if not is_connected:
                try:
                    self._on_disconnect()
                except Exception:
                    pass

    def reconnect(self, port, baud):
        """Reconnect with new port/baud settings"""
        self.port = port
        self.baud = baud
        self._connect()

    def close(self):
        self.running = False
        try:
            if self.ser:
                self.ser.close()
        except:
            pass
        try:
            self._on_disconnect()
        except Exception:
            pass

    def send_channels(self, ch16):
        # Update the shared channel buffer; the serial thread will send at the
        # configured frequency. This avoids coupling UI send calls to serial I/O.
        try:
            with self.channels_lock:
                self.latest_channels = list(ch16[:CHANNELS]) + [1500] * max(0, CHANNELS - len(ch16))
            # Mark joystick activity (update timestamp)
            self._last_joystick_update_sec = time.time()
        except Exception as e:
            self.debug.emit(f"send_channels error: {e}")

    def run(self):
        buf = bytearray()
        # We'll maintain a send loop here driven by `send_interval_us`.
        while self.running:
            if not self.ser:
                self._update_status()
                # Only auto-reconnect if initial connection was already attempted
                if self._initial_connect_attempted:
                    self._connect()
                time.sleep(0.5)
                continue
            try:
                data = self.ser.read(256)
                if data:
                    buf.extend(data)
                    # Parse CRSF frames from USB->ELRS forwarder
                    while True:
                        if len(buf) < 3:
                            break
                        # Frame size is the second byte (frame_size = type+payload+crc)
                        frame_size = buf[1]
                        total = frame_size + 2
                        if frame_size < 4 or frame_size > 64:
                            # invalid frame size — drop the first byte and continue
                            del buf[0]
                            continue
                        if len(buf) < total:
                            break
                        ftype = buf[2]
                        payload = bytes(buf[3: total - 1])
                        crc = buf[total - 1]
                        if crc8_d5(bytes([ftype]) + payload) == crc:
                            self._handle_frame(ftype, payload)
                            del buf[:total]
                        else:
                            # CRC mismatch — drop one byte and try again
                            del buf[0]
                else:
                    time.sleep(0.001)
            except Exception as e:
                self.ser = None
                self._update_status()
                try:
                    self._on_disconnect()
                except Exception:
                    pass
                time.sleep(0.2)

            # --- Periodic CRSF send based on configured interval (µs) ---
            try:
                now = time.perf_counter()
                elapsed_us = (now - self._last_send_time) * 1e6
                # Inhibit CRSF TX if no joystick updates for >1s (disconnected or never connected)
                has_recent_activity = time.time() - self._last_joystick_update_sec <= 1.0
                if elapsed_us >= self.send_interval_us:
                    self._last_send_time = now
                    # Only send channels if we have recent joystick activity
                    if has_recent_activity:
                        # Snapshot channels
                        with self.channels_lock:
                            channels_copy = list(self.latest_channels)
                        # Build packet and send
                        if self.ser:
                                    try:
                                        pkt = build_crsf_channels_frame(channels_copy)
                                        self.ser.write(pkt)
                                    except Exception as e:
                                        # If building/sending channels fails, log exception
                                        self.debug.emit(f"Serial write error (channels): {e}")
            except Exception as e:
                # Avoid crashing the periodic send loop
                self.debug.emit(f"Send loop error: {e}")

            # --- Discovery/Parameter read state machine (runs independently of channel sends) ---
            try:
                nowt = time.time()
                # Check if we should delay discovery pings (wait for module to initialize)
                discovery_ready = True
                if self._first_rx_time is not None and (nowt - self._first_rx_time) < self._discovery_delay:
                    discovery_ready = False

                # Start discovery pings immediately after delay if auto-discovery hasn't been triggered yet
                if discovery_ready and not self._auto_discovery_triggered and nowt - self._last_device_ping_time >= self._device_ping_interval:
                    self._last_device_ping_time = nowt
                    try:
                        self.debug.emit("Discovery: sending initial pings to both TX addresses")
                    except Exception:
                        pass
                    # Send initial pings to both TX addresses to discover all devices
                    self._send_crsf_cmd(CRSF_FRAMETYPE_DEVICE_PING, bytes([0x00, CRSF_ADDRESS_CRSF_TRANSMITTER]))
                    self._send_crsf_cmd(CRSF_FRAMETYPE_DEVICE_PING, bytes([0x00, CRSF_ADDRESS_TRANSMITTER_LEGACY]))
                    # Set awaiting addresses to include both for periodic pings
                    self._awaiting_addresses.add(CRSF_ADDRESS_CRSF_TRANSMITTER)
                    self._awaiting_addresses.add(CRSF_ADDRESS_TRANSMITTER_LEGACY)
                elif discovery_ready and self._awaiting_addresses and nowt - self._last_device_ping_time >= self._device_ping_interval:
                    self._last_device_ping_time = nowt
                    # Device ping uses payload: [broadcast_address, target_address]
                    # If we have awaiting addresses, ping them specifically; otherwise fallback to both
                    targets = list(self._awaiting_addresses) if self._awaiting_addresses else [CRSF_ADDRESS_CRSF_TRANSMITTER, CRSF_ADDRESS_TRANSMITTER_LEGACY]
                    for tgt in targets:
                        try:
                            self.debug.emit(f"Discovery: sending ping to 0x{tgt:02X} (awaiting={list(self._awaiting_addresses)})")
                        except Exception:
                            pass
                        self._send_crsf_cmd(CRSF_FRAMETYPE_DEVICE_PING, bytes([0x00, tgt]))

                # If we have a load queue, send the next PARAMETER_READ if no outstanding chunk
                if self.loadQ:
                    if time.time() >= self.fieldTimeout:
                        # Send next parameter read with fieldChunk
                        field_id = self.loadQ[-1]
                        # IMPORTANT: Only poll TX modules (0xEE/0xEA), never receivers
                        if self.current_device_id in self.elrs_devices:
                            device_id = self.current_device_id
                        else:
                            # Fallback: find first TX module in discovered devices
                            tx_devices = [addr for addr in self.elrs_devices.keys()
                                        if addr in (CRSF_ADDRESS_CRSF_TRANSMITTER, CRSF_ADDRESS_TRANSMITTER_LEGACY)]
                            if tx_devices:
                                device_id = tx_devices[0]
                            else:
                                # No TX devices discovered yet, skip polling
                                return
                        payload = bytes([device_id, CRSF_ADDRESS_ELRS_LUA, field_id & 0xFF, self.fieldChunk & 0xFF])
                        self._send_crsf_cmd(CRSF_FRAMETYPE_PARAMETER_READ, payload)
                        self.debug.emit(f"Param poll: requesting field {field_id} chunk {self.fieldChunk} from device 0x{device_id:02X} (loadQ: {len(self.loadQ)} remaining)")
                        # Set timeout to wait for a response
                        self.fieldTimeout = time.time() + self.field_timeout_seconds
            except Exception as e:
                self.debug.emit(f"Discovery/param state error: {e}")

    def _handle_frame(self, t, payload):
        # Handle CRSF frames
        # Track when we first receive data from TX for discovery delay
        if self._first_rx_time is None:
            self._first_rx_time = time.time()
            self.debug.emit(f"First RX frame received, waiting {self._discovery_delay}s before sending discovery pings")

        # Destination and source are usually: payload[0] = dest, payload[1] = src
        try:
            dest = payload[0] if len(payload) >= 2 else None
            src = payload[1] if len(payload) >= 2 else None
        except Exception:
            dest, src = None, None
        # Log all non-channel frames for debugging
        if t not in (CRSF_FRAMETYPE_RC_CHANNELS_PACKED,):
            src_str = f"0x{src:02X}" if src is not None else "?"
            payload_hex = payload[:20].hex() if len(payload) > 0 else ""
        # Auto-trigger one-shot discovery as soon as we see a frame from the TX address
        if not self._auto_discovery_triggered and src in (CRSF_ADDRESS_CRSF_TRANSMITTER, CRSF_ADDRESS_TRANSMITTER_LEGACY):
            try:
                self._trigger_one_shot_discovery(src)
            except Exception as e:
                self.debug.emit(f"auto discovery trigger error: {e}")
        if t == CRSF_FRAMETYPE_LINK_STATISTICS and len(payload) >= 10:
            # crsf_link_statistics_t (10 bytes)
            r = {
                "1RSS": int(struct.unpack("b", payload[0:1])[0]),
                "2RSS": int(struct.unpack("b", payload[1:2])[0]),
                "LQ": payload[2],
                "RSNR": int(struct.unpack("b", payload[3:4])[0]),
                "RFMD": payload[5] if len(payload) > 5 else 0,
                "TPWR": payload[6] if len(payload) > 6 else 0,
                "TRSS": int(struct.unpack("b", payload[7:8])[0]) if len(payload) > 7 else 0,
                "TLQ": payload[8] if len(payload) > 8 else 0,
                "TSNR": int(struct.unpack("b", payload[9:10])[0]) if len(payload) > 9 else 0,
                "FLAGS": payload[4] if len(payload) > 4 else 0,
            }
            self.telemetry.emit(r)
            self.last_link_stats_time = time.time()
        elif t == CRSF_FRAMETYPE_RC_CHANNELS_PACKED and len(payload) >= 22:
            # Unpack 16 channels from 22-byte CRSF payload
            chans = []
            bitbuf = 0
            bitcount = 0
            for b in payload:
                bitbuf |= b << bitcount
                bitcount += 8
                while bitcount >= 11 and len(chans) < 16:
                    v = bitbuf & 0x7FF
                    # Convert CRSF 11-bit integer to microsecond units for UI
                    chans.append(_crsf_val_to_us(v))
                    bitbuf >>= 11
                    bitcount -= 11
            # Emit channels (microseconds) via a dedicated signal so the GUI can update controls
            self.channels_update.emit(chans)
        elif t == CRSF_FRAMETYPE_HANDSET and len(payload) >= 11:
            # Extended handset frame: payload[0]=dest, payload[1]=orig, payload[2]=subType
            sub = payload[2]
            if sub == CRSF_HANDSET_SUBCMD_TIMING:
                # Rate and offset in big-endian. rate is in 0.1µs units; offset is signed 0.1µs units
                try:
                    rate_be = int.from_bytes(payload[3:7], 'big', signed=False)
                    offset_be = int.from_bytes(payload[7:11], 'big', signed=True)
                    interval_us = rate_be // 10
                    offset_us = int(offset_be) // 10
                    # Emit the sync update with microseconds and source address
                    # Update the internal send interval for the serial thread
                    # Sanity check (keep within reasonable radio timings)
                    if interval_us >= 500 and interval_us <= 50000:
                        self.send_interval_us = int(interval_us)
                    else:
                        self.debug.emit(f"[SYNC] Ignoring out-of-range interval: {interval_us}µs")
                    # Emit a UI-only event for display if connected: include src
                    self.sync_update.emit(int(interval_us), int(offset_us), src)
                except Exception as e:
                    self.debug.emit(f"Sync parse error: {e}")
        else:
            # Device discovery and parameter frames
            if t == CRSF_FRAMETYPE_DEVICE_INFO:
                self.debug.emit(f"Received DEVICE_INFO frame, payload length: {len(payload)}")
                try:
                    self._parse_device_info(payload)
                except Exception as e:
                    import traceback
                    self.debug.emit(f"DEVICE_INFO parse error: {e}")
                    self.debug.emit(f"Traceback: {traceback.format_exc()}")
                return
            if t == CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY:
                try:
                    self._parse_parameter_settings_entry(payload)
                except Exception as e:
                    self.debug.emit(f"PARAM_ENTRY parse error: {e}")
                return
            # If we saw a frame from a TX address, trigger one-shot discovery to pull device info
            if not self._auto_discovery_triggered and src in (CRSF_ADDRESS_CRSF_TRANSMITTER, CRSF_ADDRESS_TRANSMITTER_LEGACY):
                try:
                    self._trigger_one_shot_discovery(src)
                except Exception as e:
                    self.debug.emit(f"auto discovery trigger error: {e}")

    def _send_crsf_cmd(self, ftype: int, payload: bytes):
            """Write a CRSF command frame to the serial port if connected."""
            if not self.ser:
                return
            try:
                pkt = build_crsf_frame(ftype, payload)
                self.ser.write(pkt)
            except Exception as e:
                self.debug.emit(f"_send_crsf_cmd error: {e}")

    def _trigger_one_shot_discovery(self, src=None):
        """Send one-shot DEVICE_PING packets immediately and request parameter reads on response.
        This is used when we receive the first CRSF packet from the TX to auto-start discovery.
        """
        if self._auto_discovery_triggered:
            return
        self._auto_discovery_triggered = True
        # set current device id if known - ONLY for TX modules
        if src and src in (CRSF_ADDRESS_CRSF_TRANSMITTER, CRSF_ADDRESS_TRANSMITTER_LEGACY):
            self.current_device_id = src
        # Always queue both addresses for discovery to ensure we find all devices
        self._awaiting_addresses.add(CRSF_ADDRESS_CRSF_TRANSMITTER)
        self._awaiting_addresses.add(CRSF_ADDRESS_TRANSMITTER_LEGACY)
        # Send one-shot pings to both addresses (legacy and EE)
        self._send_crsf_cmd(CRSF_FRAMETYPE_DEVICE_PING, bytes([0x00, CRSF_ADDRESS_CRSF_TRANSMITTER]))
        self._send_crsf_cmd(CRSF_FRAMETYPE_DEVICE_PING, bytes([0x00, CRSF_ADDRESS_TRANSMITTER_LEGACY]))

    def request_parameter_read(self, device_id: int, field_id: int):
        """Request a single parameter read for a given device/field.
        This pushes the requested field onto the load queue (top) and sets
        fieldTimeout to 0 so the read is attempted immediately in the
        discovery/param loop. If field is already queued, it will be moved
        to the top.
        """
        try:
            device_id = int(device_id)
            field_id = int(field_id)
            if device_id not in self.elrs_devices:
                return
            # Ensure no duplicates: remove existing occurrences
            self.loadQ = [f for f in self.loadQ if int(f) != int(field_id)]
            # push to top
            self.loadQ.append(field_id)
            # Reset chunk state so a new read starts fresh
            self.fieldChunk = 0
            self.fieldData = None
            # Kick immediate attempt
            self.fieldTimeout = 0
        except Exception as e:
            self.debug.emit(f"request_parameter_read error: {e}")

    def request_device_reload(self, device_id: int):
        """Schedule a full parameter reload for a device.
        This will clear any existing progress for that device and enqueue all fields
        for re-reading from N down to 1 (same behavior as initial discovery read).
        """
        try:
            device_id = int(device_id)
            dev = self.elrs_devices.get(device_id)
            if not dev:
                return
            n = dev.get('n_params', 0)
            if n <= 0:
                self.debug.emit(f"request_device_reload: device 0x{device_id:02X} has no params to reload (n={n})")
                return
            # Reset fetched/fields so UI gets new information
            dev['fetched'] = set()
            dev['fields'] = {}
            dev['loaded'] = False
            # Build load queue from N down to 1
            self.loadQ = list(range(n, 0, -1))
            self.fieldChunk = 0
            self.fieldData = None
            self.fieldTimeout = 0
            self.fieldRetries = {}  # Clear retry counters on reload
        except Exception as e:
            self.debug.emit(f"request_device_reload error: {e}")

    def _parse_device_info(self, payload: bytes):
            """Parse the CRSF DEVICE_INFO payload and add to elrs_devices dict."""
            # Expect payload[0] = dst, payload[1] = src, next is name NUL-terminated
            if len(payload) < 4:
                raise ValueError("Device info payload too short")
            dst = payload[0]
            src = payload[1]
            # read name
            idx = 2
            name_bytes = []
            while idx < len(payload) and payload[idx] != 0:
                name_bytes.append(payload[idx])
                idx += 1
            name = bytes(name_bytes).decode(errors="ignore")
            idx += 1  # skip null
            offset_after_name = idx  # Save this for Lua-style offset calculation

            # Match Lua parsing exactly (elrsV3.lua line 405):
            # device.fldcnt = data[offset + 12]
            # where offset is the position after the name's null terminator

            # Read serial (4 bytes) at offset+0
            if offset_after_name + 4 <= len(payload):
                serial = payload[offset_after_name:offset_after_name+4]
            else:
                serial = b""

            # Read hardware version (4 bytes) at offset+4
            if offset_after_name + 8 <= len(payload):
                hw_ver = payload[offset_after_name+4:offset_after_name+8]
            else:
                hw_ver = b""

            # Read software version (3 bytes) at offset+8
            if offset_after_name + 11 <= len(payload):
                sw_maj = payload[offset_after_name+8]
                sw_min = payload[offset_after_name+9]
                sw_rev = payload[offset_after_name+10]
            else:
                sw_maj = sw_min = sw_rev = 0

            # Read n_params at offset+12 (Lua: data[offset + 12])
            if offset_after_name + 12 < len(payload):
                fields_count = payload[offset_after_name + 12]
            else:
                fields_count = 0

            # Read protocol version at offset+13
            if offset_after_name + 13 < len(payload):
                proto_ver = payload[offset_after_name + 13]
            else:
                proto_ver = 0

            self.debug.emit(f"DEVICE_INFO parsed: name='{name}', sw={sw_maj}.{sw_min}.{sw_rev}, n_params={fields_count}, proto_ver={proto_ver}")

            # Merge into existing device entry if present to avoid losing parsed fields on duplicate DEVICE_INFO
            existing = src in self.elrs_devices
            if existing:
                dev = self.elrs_devices[src]
                dev["name"] = name
                dev["serial"] = serial.hex()
                dev["hw_ver"] = hw_ver.hex()
                dev["sw_ver"] = f"{sw_maj}.{sw_min}.{sw_rev}"
                # If new field count is greater than 0 and was previously 0, adopt it
                prev_n = dev.get("n_params", 0)
                if fields_count and fields_count != prev_n:
                    dev["n_params"] = fields_count
                dev["proto_ver"] = proto_ver
            else:
                self.elrs_devices[src] = {
                    "name": name,
                    "serial": serial.hex(),
                    "hw_ver": hw_ver.hex(),
                    "sw_ver": f"{sw_maj}.{sw_min}.{sw_rev}",
                    "n_params": fields_count,
                    "proto_ver": proto_ver,
                    "fields": {}
                }

            # Build load queue: descending order from fields_count down to 1
            # Build load queue only if n_params > 0 and no queued work already
            # If device already marked loaded, skip reloading
            # IMPORTANT: Only load parameters from TX module (0xEE/0xEA), NOT from receivers
            dev = self.elrs_devices.get(src)
            is_tx_module = src in (CRSF_ADDRESS_CRSF_TRANSMITTER, CRSF_ADDRESS_TRANSMITTER_LEGACY)

            if not is_tx_module:
                # Receiver or other device - discover it but don't load params
                self.debug.emit(f"DEVICE_INFO response: {name} (0x{src:02X}) is not TX module, skipping parameter load")
            elif fields_count > 0 and (not self.loadQ) and not dev.get('loaded', False):
                # Only load the params that the device explicitly reports
                # Folder params require special handling via folder interaction
                self.loadQ = list(range(fields_count, 0, -1))
                self.current_device_id = src  # Lock to this TX module
                self.debug.emit(f"DEVICE_INFO response: {name} (0x{src:02X}) reports {fields_count} params, loadQ initialized: {len(self.loadQ)} items")
            elif dev.get('loaded', False):
                self.debug.emit(f"DEVICE_INFO response: {name} (0x{src:02X}) already loaded, skipping parameter reload")
            elif self.loadQ:
                self.debug.emit(f"DEVICE_INFO response: {name} (0x{src:02X}) loadQ already active ({len(self.loadQ)} items remaining), not restarting")
            else:
                self.debug.emit(f"DEVICE_INFO response: {name} (0x{src:02X}) reports {fields_count} params (no loadQ created)")
            self.fieldChunk = 0
            self.fieldData = None
            # Kick immediate field read
            self.fieldTimeout = 0
            # Emit discovered device to GUI only on first parse or if something changed
            try:
                dev = self.elrs_devices[src]
                # Use a per-device flag to avoid emitting duplicates for repeat DEVICE_INFO frames
                nowt = time.time()
                last_emit = dev.get('_last_discovery_emit_time', 0)
                emitted_before = dev.get('_discovery_emitted', False)
                # Emit on first discovery
                if not emitted_before:
                    self.device_discovered.emit(src, dev)
                    dev['_discovery_emitted'] = True
                    dev['_last_discovery_emit_time'] = nowt
                else:
                    # If the name or n_params changed, always emit immediately
                    if dev.get('name') != name or dev.get('n_params') != fields_count:
                        self.device_discovered.emit(src, dev)
                        dev['_last_discovery_emit_time'] = nowt
                    else:
                        # Otherwise, only re-emit if it has been a while (cooldown) to avoid spam
                        cooldown = 5.0
                        if nowt - last_emit > cooldown:
                            self.device_discovered.emit(src, dev)
                            dev['_last_discovery_emit_time'] = nowt
            except Exception:
                pass
            # Remove the responding address from awaiting list so we stop pinging it
            if src in self._awaiting_addresses:
                try:
                    self._awaiting_addresses.discard(src)
                except Exception:
                    pass
            # If we discovered on an address, remove the counterpart address too
            try:
                if src == CRSF_ADDRESS_CRSF_TRANSMITTER:
                    # remove legacy 0xEA if present
                    self._awaiting_addresses.discard(CRSF_ADDRESS_TRANSMITTER_LEGACY)
                elif src == CRSF_ADDRESS_TRANSMITTER_LEGACY:
                    self._awaiting_addresses.discard(CRSF_ADDRESS_CRSF_TRANSMITTER)
            except Exception:
                pass
            # If this discovery was triggered by auto-discovery, schedule a parameter read automatically
            if self._auto_discovery_triggered and is_tx_module:
                dev = self.elrs_devices.get(src, None)
                if dev is not None:
                    n = dev.get('n_params', 0)
                    if n > 0 and not self.loadQ and not dev.get('loaded', False):
                        self.loadQ = list(range(n, 0, -1))
                        self.debug.emit(f"Auto-discovery: scheduling load of {n} params for device 0x{src:02X}")
                    elif n == 0:
                        self.debug.emit(f"Auto-discovery: device 0x{src:02X} reports 0 params, nothing to load")
                # Do NOT reset the auto-discovery flag here: keep it set so the auto discovery is one-shot

    def _parse_parameter_settings_entry(self, payload: bytes):
            """Handle PARAMETER_SETTINGS_ENTRY: supports chunked reads and simple parsing.
            Payload structure (bytes): dst, src, field_id, chunksRemain, ...data
            """
            if len(payload) < 4:
                raise ValueError("Parameter entry too short")
            dst = payload[0]
            src = payload[1]
            field_id = payload[2]
            chunks_remain = payload[3]
            data_bytes = bytes(payload[4:])
            self.debug.emit(f"Param response: field {field_id} from 0x{src:02X}, chunk={self.fieldChunk}, chunks_remain={chunks_remain}, data_len={len(data_bytes)}")

            # Ignore responses from non-TX devices (receivers)
            if src not in (CRSF_ADDRESS_CRSF_TRANSMITTER, CRSF_ADDRESS_TRANSMITTER_LEGACY):
                self.debug.emit(f"Ignoring param response from non-TX device 0x{src:02X}")
                return

            # Ignore responses for fields not currently in loadQ (stale/duplicate responses)
            if field_id not in self.loadQ:
                self.debug.emit(f"Ignoring stale response for field {field_id} (not in loadQ)")
                return

            # Ignore responses for fields that aren't at the top of the queue
            if self.loadQ and self.loadQ[-1] != field_id:
                self.debug.emit(f"Ignoring out-of-order response for field {field_id} (expected field {self.loadQ[-1]})")
                return

            # CRITICAL Lua-style validation: If we have accumulated data, verify chunks_remain matches expected
            # This prevents processing corrupt chunked data (e.g., chunk count jumping to 255)
            if self.fieldData is not None and chunks_remain != self.expectChunksRemain:
                self.debug.emit(f"Chunk validation FAILED for field {field_id}: expected chunks_remain={self.expectChunksRemain}, got {chunks_remain} - aborting field (corrupt data)")
                # Mark as fetched even though it failed, for progress tracking
                dev = self.elrs_devices.get(src, None)
                if dev is not None:
                    fset = dev.get('fetched', set())
                    fset.add(field_id)
                    dev['fetched'] = fset
                # Abort this field, reset state, move to next
                if self.loadQ and self.loadQ[-1] == field_id:
                    self.loadQ.pop()
                self.fieldChunk = 0
                self.fieldData = None
                self.expectChunksRemain = -1
                self.fieldTimeout = 0
                return

            # Accumulate chunked data
            if chunks_remain > 0:
                # Safety check: prevent infinite chunk requests due to corrupt chunk data
                MAX_CHUNKS = 30  # Reasonable limit - most fields are single chunk or a few chunks
                if self.fieldChunk >= MAX_CHUNKS:
                    self.debug.emit(f"Field {field_id} exceeded maximum chunk limit ({MAX_CHUNKS}) at chunk {self.fieldChunk}, chunks_remain={chunks_remain} - aborting (likely corrupt chunk data)")
                    # Mark as fetched even though it failed, for progress tracking
                    dev = self.elrs_devices.get(src, None)
                    if dev is not None:
                        fset = dev.get('fetched', set())
                        fset.add(field_id)
                        dev['fetched'] = fset
                    # Abort this field and move to next
                    if self.loadQ and self.loadQ[-1] == field_id:
                        self.loadQ.pop()
                    self.fieldChunk = 0
                    self.fieldData = None
                    self.expectChunksRemain = -1
                    self.fieldTimeout = 0
                    return

                if self.fieldData is None:
                    self.fieldData = bytearray()
                self.fieldData.extend(data_bytes)
                self.fieldChunk += 1
                # Set expectation for NEXT chunk (Lua line 468)
                self.expectChunksRemain = chunks_remain - 1
                # Expect more chunks; request next immediately
                self.fieldTimeout = 0
                return
            else:
                # Single-chunk or final chunk - if we were accumulating, append, else treat as full
                if self.fieldData is not None:
                    self.fieldData.extend(data_bytes)
                    full = bytes(self.fieldData)
                else:
                    full = data_bytes
                # Reset chunk state
                self.fieldChunk = 0
                self.fieldData = None
                self.expectChunksRemain = -1
                # Now parse the field's metadata from 'full'
                try:
                    parsed = self._parse_field_blob(full)
                    # Validate parsed field - check for common corruption indicators
                    is_valid = self._validate_parsed_field(parsed, field_id)
                    if not is_valid:
                        # Check retry count to prevent infinite loops (max 3 retries)
                        retry_count = self.fieldRetries.get(field_id, 0)
                        if retry_count < 3:
                            self.debug.emit(f"Field {field_id} validation failed (attempt {retry_count + 1}/3), requesting retry: {parsed}")
                            # Don't store invalid parse, request retry by adding back to queue
                            dev = self.elrs_devices.get(src, None)
                            self.fieldRetries[field_id] = retry_count + 1
                            if dev is not None and hasattr(self, 'loadQ') and field_id not in self.loadQ:
                                # Add to front of queue for immediate retry
                                self.loadQ.append(field_id)
                            # Reset chunk state for clean retry
                            self.fieldChunk = 0
                            self.fieldData = None
                            self.expectChunksRemain = -1
                            self.fieldTimeout = 0
                            return
                        else:
                            self.debug.emit(f"Field {field_id} validation failed after 3 retries, storing as-is")
                            # Clear retry count and proceed to store the field (even if invalid)
                            self.fieldRetries.pop(field_id, None)
                except Exception as e:
                    # Check retry count for parse exceptions too
                    retry_count = self.fieldRetries.get(field_id, 0)
                    if retry_count < 3:
                        self.debug.emit(f"_parse_field_blob error (attempt {retry_count + 1}/3): {e}, requesting retry")
                        # Don't store raw data, request retry instead
                        dev = self.elrs_devices.get(src, None)
                        self.fieldRetries[field_id] = retry_count + 1
                        if dev is not None and hasattr(self, 'loadQ') and field_id not in self.loadQ:
                            self.loadQ.append(field_id)
                        # Reset chunk state for clean retry
                        self.fieldChunk = 0
                        self.fieldData = None
                        self.expectChunksRemain = -1
                        self.fieldTimeout = 0
                        return
                    else:
                        self.debug.emit(f"_parse_field_blob error after 3 retries: {e}, storing raw data")
                        parsed = {"raw": full.hex()}
                        self.fieldRetries.pop(field_id, None)
                # Store parsed field
                dev = self.elrs_devices.get(src, None)
                if dev is None:
                    # Unknown device - skip
                    self.debug.emit(f"Received param for unknown device 0x{src:02X}")
                else:
                    # Clear retry count on successful parse
                    self.fieldRetries.pop(field_id, None)

                    dev["fields"][field_id] = parsed
                    # Emit per-field update so UI can update widgets selectively
                    try:
                        self.device_parameter_field_updated.emit(src, field_id, parsed)
                    except Exception:
                        pass
                    # track fetched fields
                    fset = dev.get('fetched', set())
                    fset.add(field_id)
                    dev['fetched'] = fset
                    # Emit a progress update now
                    try:
                        fetched = len(fset)
                        total = dev.get('n_params', 0)
                        self.device_parameters_progress.emit(src, fetched, total)
                    except Exception:
                        pass
                    # Pop from loadQ if this is the top of the stack
                if self.loadQ and self.loadQ[-1] == field_id:
                    self.loadQ.pop()
                    self.debug.emit(f"Param complete: field {field_id} parsed successfully, loadQ now has {len(self.loadQ)} items remaining")
                # If queue is empty, report completion
                if not self.loadQ:
                    # Mark the device as loaded so subsequent DEVICE_INFO frames don't restart a read
                    dev = self.elrs_devices.get(src, None)
                    if dev is not None:
                        dev['loaded'] = True
                        self.debug.emit(f"All params loaded for device 0x{src:02X}, marking as loaded=True")
                # Emit signal that parameters are loaded
                try:
                    self.device_parameters_loaded.emit(src, self.elrs_devices[src])
                except Exception as e:
                    self.debug.emit(f"Emit parameters loaded error: {e}")
                # Emit a progress update for UI
                try:
                    fetched = len(dev.get('fetched', [])) if dev is not None else 0
                    total = dev.get('n_params', 0) if dev is not None else 0
                    self.device_parameters_progress.emit(src, fetched, total)
                except Exception:
                    pass
                # Allow next field request immediately
                self.fieldTimeout = 0

    def _parse_field_blob(self, data: bytes) -> dict:
            """Parse a single field blob (full assembled data for a parameter) into a dictionary.
            We parse: parent, type, hidden, name, values (selection), min/max/default/unit, status
            This is a best-effort parse and should handle common types.
            """
            out = {}
            if len(data) < 3:
                out["raw"] = data.hex(); return out

            def read_cstr(buf, off):
                s = []
                while off < len(buf) and buf[off] != 0:
                    s.append(buf[off]); off += 1
                try:
                    val = bytes(s).decode(errors="ignore")
                except Exception:
                    val = ""
                return val, off + 1 if off < len(buf) and buf[off] == 0 else off

            def read_opts(buf, off):
                vals = []
                cur = []
                vcnt = 0
                while off < len(buf) and buf[off] != 0:
                    b = buf[off]
                    off += 1
                    if b == 59:  # ';'
                        s = bytes(cur).decode(errors="ignore") if cur else ""
                        if s != "":
                            vals.append(s.strip())
                        cur = []
                    else:
                        cur.append(b)
                # final
                if cur:
                    s = bytes(cur).decode(errors="ignore")
                    if s != "":
                        vals.append(s.strip())
                # return the list and next offset after nul
                return vals, (off + 1 if off < len(buf) and buf[off] == 0 else off)

            def read_uint(buf, off, size):
                v = 0
                for i in range(size):
                    if off + i < len(buf):
                        v = (v << 8) + buf[off + i]
                return v

            offset = 0
            parent = data[offset]; offset += 1
            ftype = data[offset] & 0x7F; hidden = bool(data[offset] & 0x80); offset += 1
            out["parent"] = parent
            out["type"] = ftype
            out["hidden"] = hidden
            # Name string
            name, offset = read_cstr(data, offset)
            out["name"] = name

            # Handle types similar to elrsV3.lua
            # Text selection (type 9)
            if ftype == 9:
                vals, offset = read_opts(data, offset)
                out["values"] = vals
                # selection index is next byte if present
                if offset < len(data):
                    out["value"] = data[offset]
                    offset += 1
                # unit may follow
                if offset < len(data) and data[offset] != 0:
                    unit, offset = read_cstr(data, offset)
                    out["unit"] = unit

            # Numeric values: determine size and signedness using Lua's formula
            if 0 <= ftype <= 8:
                # size in bytes: floor(ftype / 2) + 1
                size = (ftype // 2) + 1
                is_signed = (ftype % 2) == 1
                # value at current offset, follow by min/max/default
                if offset + size <= len(data):
                    out["value"] = read_uint(data, offset, size)
                if offset + size*2 <= len(data):
                    out["min"] = read_uint(data, offset + size, size)
                if offset + size*3 <= len(data):
                    out["max"] = read_uint(data, offset + size*2, size)
                if offset + size*4 <= len(data):
                    out["default"] = read_uint(data, offset + size*3, size)
                # convert unsigned to signed range if required
                if is_signed:
                    # convert 2's complement
                    def signed_val(v, s):
                        band = 1 << (s*8 - 1)
                        if v & band:
                            v = v - (1 << (s*8))
                        return v
                    if "value" in out:
                        out["value"] = signed_val(out["value"], size)
                    if "min" in out:
                        out["min"] = signed_val(out["min"], size)
                    if "max" in out:
                        out["max"] = signed_val(out["max"], size)
                    if "default" in out:
                        out["default"] = signed_val(out["default"], size)

            # Float type (8)
            if ftype == 8:
                # data layout similar to Lua: float fields have 4 byte value and min/max/default/prec/step
                if offset + 4 <= len(data):
                    out["value_raw"] = read_uint(data, offset, 4)
                if offset + 8 <= len(data):
                    out["min"] = read_uint(data, offset + 4, 4)
                if offset + 12 <= len(data):
                    out["max"] = read_uint(data, offset + 8, 4)
                if offset + 16 <= len(data):
                    out["default"] = read_uint(data, offset + 12, 4)
                if offset + 21 <= len(data):
                    out["prec"] = data[offset + 16]
                if offset + 25 <= len(data):
                    out["step"] = read_uint(data, offset + 17, 4)
                # convert raw values using prec if applicable
                if "prec" in out and out["prec"] > 0:
                    div = 10 ** out["prec"]
                    try:
                        out["value"] = out.get("value_raw", 0) / div
                        if "min" in out: out["min"] = out["min"] / div
                        if "max" in out: out["max"] = out["max"] / div
                        if "default" in out: out["default"] = out["default"] / div
                    except Exception:
                        pass

            # String type (type 12/info type 13 in lua mapping): read string and maxlen
            if ftype == 12 or ftype == 13:
                # read string
                s, offset = read_cstr(data, offset)
                out["value"] = s
                # optional maxlen after string
                if offset < len(data):
                    out["maxlen"] = data[offset]
                    offset += 1

            # Command type
            if ftype == 13:
                if offset + 1 <= len(data):
                    out["status"] = data[offset]; offset += 1
                if offset + 1 <= len(data):
                    out["timeout"] = data[offset]; offset += 1
                if offset < len(data):
                    s, offset = read_cstr(data, offset)
                    out["info"] = s

            # Generic fallback: last byte as status if present
            if len(data) > 0:
                out["status"] = data[-1]
            return out

    def _validate_parsed_field(self, parsed: dict, field_id: int) -> bool:
        """Validate a parsed field to detect corruption.
        Returns True if the field appears valid, False if it should be re-read.
        """
        try:
            # Check if parse returned raw data only (indicates parse failure)
            if "raw" in parsed and len(parsed) == 1:
                return False

            # Validate field type is in expected range (0-13 for known types)
            ftype = parsed.get('type')
            if ftype is None:
                return False
            if not (0 <= ftype <= 13):
                return False

            # For type 9 (combo/select), validate we have values list
            if ftype == 9:
                values = parsed.get('values', [])
                if not isinstance(values, list) or len(values) == 0:
                    return False
                # Check that value index is within bounds
                val_idx = parsed.get('value')
                if val_idx is not None and val_idx < 0:
                    return False

            # For type 11 (folder/info), ensure we have a name
            if ftype == 11:
                name = parsed.get('name', '')
                if not name or len(name) == 0:
                    return False

            # Basic sanity check: all fields should have a name
            name = parsed.get('name', '')
            if not name or len(name) == 0:
                return False

            # Check for obviously corrupted names (non-printable chars, etc)
            if not all(32 <= ord(c) < 127 or c in '\n\r\t' for c in name):
                return False

            return True
        except Exception as e:
            # If validation itself fails, assume field is invalid
            return False

    def _on_disconnect(self):
        """Reset discovery and parameter-read state after the serial port disconnects.
        This ensures that auto discovery and param pulls will fire again when the port is reconnected.
        """
        try:
            self._auto_discovery_triggered = False
            self._first_rx_time = None  # Reset discovery delay timer
            self._awaiting_addresses.clear()
            self._last_device_ping_time = 0
            self.loadQ = []
            self.fieldChunk = 0
            self.fieldData = None
            self.fieldTimeout = 0
            self.fieldRetries = {}  # Clear retry counters
            self.expectChunksRemain = -1
            # Reset 'loaded' and fetched flags for each device so we re-pull on reconnect
            for dev in self.elrs_devices.values():
                dev['loaded'] = False
                dev['fields'] = {}
                if 'fetched' in dev and isinstance(dev['fetched'], set):
                    dev['fetched'] = set()
            self.debug.emit("Serial disconnected; discovery/param flags reset")
        except Exception as e:
            self.debug.emit(f"_on_disconnect error: {e}")

    def reset_tx_disconnected(self):
        """Reset TX-specific discovery and device state when the radio stops sending frames.
        This does not close the serial port. It prepares discovery so that a new device ping will be
        sent when the TX reconnects (auto discovery is re-enabled).
        """
        try:
            self._auto_discovery_triggered = False
            self._first_rx_time = None  # Reset discovery delay timer
            self._awaiting_addresses.clear()
            # queue both addresses so pings will fire
            self._awaiting_addresses.add(CRSF_ADDRESS_CRSF_TRANSMITTER)
            self._awaiting_addresses.add(CRSF_ADDRESS_TRANSMITTER_LEGACY)
            self.elrs_devices = {}
            # Also clear per-device emitted flags if any
            # reset current device id to default
            self.current_device_id = CRSF_ADDRESS_CRSF_TRANSMITTER
            self.loadQ = []
            self.fieldChunk = 0
            self.fieldData = None
            self.fieldTimeout = 0
            self.fieldRetries = {}  # Clear retry counters
            self.expectChunksRemain = -1
            self.debug.emit("TX disconnected; auto discovery reset")
        except Exception as e:
            self.debug.emit(f"reset_tx_disconnected error: {e}")

# -------------------------------------------------------------------
class Joy(QtCore.QObject):
    status = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        pygame.init()
        pygame.joystick.init()
        self.j = None
        self.name = "None"
        # Pygame 2 provides joystick hotplug events; detect support.
        self._joy_events_supported = hasattr(pygame, "JOYDEVICEADDED") and hasattr(pygame, "JOYDEVICEREMOVED")

        # periodic scanner
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._scan)
        self.timer.start(2000)

        self.status.emit("Scanning for joystick...")

    def _handle_device_added(self, device_index: int):
        """Handle a newly added joystick device (pygame 2+)."""
        try:
            # Ensure subsystem is initialized
            if not pygame.joystick.get_init():
                pygame.joystick.init()
            # If we already have a joystick, ignore additional ones
            if self.j is not None:
                return
            js = pygame.joystick.Joystick(device_index)
            js.init()
            self.j = js
            try:
                self.name = self.j.get_name()
            except Exception:
                self.name = "Joystick"
            self.status.emit(f"Joystick connected: {self.name}")
        except Exception as e:
            self.status.emit(f"Joystick init error: {e}")
            self.j = None
            self.name = "None"

    def _handle_device_removed(self, instance_id):
        """Handle joystick removal (pygame 2+)."""
        try:
            current_id = self.j.get_instance_id() if (self.j is not None and hasattr(self.j, "get_instance_id")) else None
        except Exception:
            current_id = None
        # If we don't know ids, or it matches our current one, drop it.
        if self.j is None or current_id is None or instance_id == current_id:
            self.status.emit(f"Joystick '{self.name}' disconnected. Scanning...")
            self.j = None
            self.name = "None"
            # Kick an immediate scan to pick up any other available device
            self._scan()

    def _scan(self):
        # Only scan if no joystick is currently active
        if self.j is not None:
            # Check that it still exists
            count = pygame.joystick.get_count()
            try:
                # Safely access name to confirm it hasn't gone invalid
                _ = self.j.get_name()
            except pygame.error:
                # joystick object became invalid
                self.status.emit(f"Joystick '{self.name}' disconnected. Scanning...")
                self.j = None
                self.name = "None"
                pygame.joystick.quit()
                pygame.joystick.init()
            return

        # If we get here, there is no active joystick — scan for one
        pygame.joystick.quit()
        pygame.joystick.init()
        count = pygame.joystick.get_count()
        if count == 0:
            self.status.emit("Scanning for joystick...")
        else:
            try:
                self.j = pygame.joystick.Joystick(0)
                self.j.init()
                self.name = self.j.get_name()
                self.status.emit(f"Joystick connected: {self.name}")
            except Exception as e:
                self.status.emit(f"Joystick init error: {e}")
                self.j = None
                self.name = "None"

    def read(self):
        # Prefer hotplug events if available for immediate reconnect
        if self._joy_events_supported:
            try:
                for ev in pygame.event.get([pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED]):
                    if ev.type == pygame.JOYDEVICEADDED:
                        # ev.device_index is the index to open
                        self._handle_device_added(getattr(ev, "device_index", 0))
                    elif ev.type == pygame.JOYDEVICEREMOVED:
                        self._handle_device_removed(getattr(ev, "instance_id", None))
            except Exception:
                # Fall back to simple pumping if anything goes wrong
                pygame.event.pump()
        else:
            pygame.event.pump()
        axes, btns = [], []
        if self.j:
            try:
                for i in range(self.j.get_numaxes()):
                    axes.append(self.j.get_axis(i))
                for i in range(self.j.get_numbuttons()):
                    btns.append(1 if self.j.get_button(i) else 0)
            except pygame.error:
                # Lost joystick during read
                self.status.emit(f"Joystick '{self.name}' lost. Scanning...")
                self.j = None
                self.name = "None"
                # Trigger a quick rescan so we don't wait for the periodic timer
                self._scan()
        return axes, btns

# -------------------------------------------------------------------

def map_axis_to_0_2000(val, inv, mn, ct, mx):
    """Simpler mapping from [-1..1] to [mn..mx] using ct as center.

    Behavior preserved:
    - Accepts non-numeric input (treated as center/0.0)
    - Allows inverted axes via `inv`
    - Clamps out-of-range inputs and snaps values near +/-1.0 to the endpoint
    - Uses rounding and clamps final integer to [mn, mx]
    """
    import math
    try:
        v = float(val)
    except Exception:
        v = 0.0
    if inv:
        v = -v
    # Clamp to [-1, 1]
    v = max(-1.0, min(1.0, v))
    # Snap if close to full deflection — prevents 1001/1999 off-by-one on many sticks
    EPS = 0.002
    if abs(abs(v) - 1.0) < EPS:
        v = math.copysign(1.0, v)
    # Piecewise linear mapping around the center (keeps non-symmetric ranges)
    if v >= 0:
        outf = ct + v * (mx - ct)
    else:
        outf = ct + v * (ct - mn)
    out = int(round(outf))
    return max(mn, min(mx, out))

class ChannelRow(QtWidgets.QWidget):
    changed = QtCore.pyqtSignal()
    mapRequested = QtCore.pyqtSignal(object)
    debug = QtCore.pyqtSignal(str)

    def __init__(self, idx, cfg):
        super().__init__()
        self.idx = idx
        layout = QtWidgets.QGridLayout(self)
        name = f"CH{idx+1}"

        self.lbl = QtWidgets.QLabel(name); self.lbl.setMaximumWidth(50)
        self.nameBox = QtWidgets.QLineEdit(); self.nameBox.setPlaceholderText("Name"); self.nameBox.setMaximumWidth(100)
        default_names = ["Ail", "Elev", "Thr", "Rudd", "Arm", "Mode"]
        default_name = default_names[idx] if idx < len(default_names) else ""
        self.nameBox.setText(cfg.get("name", default_name))
        self.bar = QtWidgets.QProgressBar(); self.bar.setRange(0,2000)
        self.val = QtWidgets.QLabel("1500"); self.val.setMaximumWidth(60); self.val.setMinimumWidth(60)

        self.src = QtWidgets.QComboBox(); self.src.addItems(SRC_CHOICES); self.src.setMaximumWidth(80)
        self.src.setCurrentText(cfg.get("src","const"))

        self.idxBox = QtWidgets.QSpinBox(); self.idxBox.setRange(0,63); self.idxBox.setValue(cfg.get("idx",0)); self.idxBox.setMaximumWidth(60)
        self.inv = QtWidgets.QCheckBox("inv"); self.inv.setChecked(cfg.get("inv",False)); self.inv.setMaximumWidth(60)

        self.toggleBox = QtWidgets.QCheckBox("Toggle"); self.toggleBox.setChecked(cfg.get("toggle", False)); self.toggleBox.setMaximumWidth(80)
        self.rotaryBox = QtWidgets.QCheckBox("Rotary"); self.rotaryBox.setChecked(cfg.get("rotary", False)); self.rotaryBox.setMaximumWidth(80)
        self.rotaryStopsBox = QtWidgets.QSpinBox(); self.rotaryStopsBox.setRange(3,6); self.rotaryStopsBox.setValue(cfg.get("rotary_stops", 3)); self.rotaryStopsBox.setMaximumWidth(60)
        self.rotaryStopsBox.setEnabled(cfg.get("rotary", False))

        self.minBox = QtWidgets.QSpinBox(); self.minBox.setRange(0,2000); self.minBox.setValue(cfg.get("min",1000)); self.minBox.setAlignment(QtCore.Qt.AlignLeft); self.minBox.setMaximumWidth(70)
        self.midBox = QtWidgets.QSpinBox(); self.midBox.setRange(0,2000); self.midBox.setValue(cfg.get("center",1500)); self.midBox.setAlignment(QtCore.Qt.AlignLeft); self.midBox.setMaximumWidth(70)
        self.maxBox = QtWidgets.QSpinBox(); self.maxBox.setRange(0,2000); self.maxBox.setValue(cfg.get("max",2000)); self.maxBox.setAlignment(QtCore.Qt.AlignLeft); self.maxBox.setMaximumWidth(70)

        self.mapBtn = QtWidgets.QPushButton("Map"); self.mapBtn.setMaximumWidth(70)

        # Update progress bar range based on min/max values
        def update_bar_range():
            self.bar.setRange(self.minBox.value(), self.maxBox.value())

        self.minBox.valueChanged.connect(update_bar_range)
        self.maxBox.valueChanged.connect(update_bar_range)
        update_bar_range()  # Set initial range

        # Top row: full-width with scaling elements
        topLayout = QtWidgets.QHBoxLayout()
        topLayout.addWidget(self.lbl)
        topLayout.addWidget(self.nameBox)
        topLayout.addWidget(self.bar, 1)  # progress bar gets stretch
        topLayout.addWidget(self.val)
        layout.addLayout(topLayout, 0, 0, 1, 15)

        # Bottom row: fixed-width controls
        srcLbl = QtWidgets.QLabel("src"); srcLbl.setMaximumWidth(30)
        layout.addWidget(srcLbl, 1,0)
        layout.addWidget(self.src, 1,1)
        idxLbl = QtWidgets.QLabel("idx"); idxLbl.setMaximumWidth(30)
        layout.addWidget(idxLbl, 1,2)
        layout.addWidget(self.idxBox, 1,3)
        layout.addWidget(self.inv, 1,4)
        minLbl = QtWidgets.QLabel("min"); minLbl.setMaximumWidth(30)
        layout.addWidget(minLbl, 1,5)
        layout.addWidget(self.minBox, 1,6)
        midLbl = QtWidgets.QLabel("mid"); midLbl.setMaximumWidth(30)
        layout.addWidget(midLbl, 1,7)
        layout.addWidget(self.midBox, 1,8)
        maxLbl = QtWidgets.QLabel("max"); maxLbl.setMaximumWidth(30)
        layout.addWidget(maxLbl, 1,9)
        layout.addWidget(self.maxBox, 1,10)
        layout.addWidget(self.mapBtn, 1,11)
        layout.addWidget(self.toggleBox, 1,12)
        layout.addWidget(self.rotaryBox, 1,13)
        layout.addWidget(self.rotaryStopsBox, 1,14)

        self.nameBox.textChanged.connect(self.changed.emit)
        self.src.currentIndexChanged.connect(self._update_visual_state)
        self.rotaryBox.toggled.connect(self._update_visual_state)
        self.toggleBox.toggled.connect(self._on_toggle_changed)
        self.rotaryBox.toggled.connect(self._on_rotary_changed)

        for w in [self.src,self.idxBox,self.inv,self.minBox,self.midBox,self.maxBox]:
            if isinstance(w, QtWidgets.QAbstractButton):
                w.toggled.connect(self.changed.emit)
            else:
                w.currentIndexChanged.connect(self.changed.emit) if isinstance(w, QtWidgets.QComboBox) else w.valueChanged.connect(self.changed.emit)

        for w in [self.rotaryStopsBox]:
            w.valueChanged.connect(self.changed.emit)

        self.mapBtn.clicked.connect(self._on_map)

        # Initial visual state
        self._update_visual_state()

    def _on_map(self):
        self.mapRequested.emit(self)

    def _update_visual_state(self):
        """Gray out the row if not mapped (src is const), enable if mapped"""
        is_mapped = self.src.currentText() != "const"
        src = self.src.currentText()
        is_axis = src == "axis"

        # List of widgets to enable/disable (Map button excluded - always enabled)
        widgets_to_control = [
            self.lbl, self.nameBox, self.bar, self.val,
            self.idxBox, self.inv, self.minBox, self.midBox, self.maxBox
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

        # Map button is always enabled so you can map unmapped channels
        self.mapBtn.setEnabled(True)
        self.mapBtn.setStyleSheet("")

        # Set default output value based on mapped state
        if not is_mapped:
            default_val = 1000
            self.bar.setValue(default_val)
            self.val.setText(str(default_val))

    def _on_toggle_changed(self):
        """Handle toggle checkbox - uncheck rotary if toggle is checked"""
        if self.toggleBox.isChecked() and self.rotaryBox.isChecked():
            self.rotaryBox.blockSignals(True)
            self.rotaryBox.setChecked(False)
            self.rotaryBox.blockSignals(False)
            self._update_visual_state()
        self.changed.emit()

    def _on_rotary_changed(self):
        """Handle rotary checkbox - uncheck toggle if rotary is checked"""
        if self.rotaryBox.isChecked() and self.toggleBox.isChecked():
            self.toggleBox.blockSignals(True)
            self.toggleBox.setChecked(False)
            self.toggleBox.blockSignals(False)
        self.changed.emit()

    def compute(self, axes, btns):
        src = self.src.currentText()
        idx = self.idxBox.value()
        inv = self.inv.isChecked()
        mn  = self.minBox.value()
        ct  = self.midBox.value()
        mx  = self.maxBox.value()
        rotary = self.rotaryBox.isChecked()
        rotary_stops = self.rotaryStopsBox.value()

        # Edge-detect/toggle state for button source
        if not hasattr(self, "_btn_last"):
            self._btn_last = 0
        if not hasattr(self, "_btn_toggle_state"):
            self._btn_toggle_state = 0
        if not hasattr(self, "_btn_rotary_state"):
            self._btn_rotary_state = 0
        if not hasattr(self, "_prev_btn_idx"):
            self._prev_btn_idx = idx

        if src == "axis":
            v = axes[idx] if idx < len(axes) else 0.0
            out = map_axis_to_0_2000(v, inv, mn, ct, mx)
        elif src == "button":
            if idx != self._prev_btn_idx:
                self._btn_last = btns[idx] if idx < len(btns) else 0
                self._prev_btn_idx = idx
            v = btns[idx] if idx < len(btns) else 0

            if rotary:
                # Rotary mode: cycle through stops on button press
                if self._btn_last == 0 and v == 1:
                    self._btn_rotary_state = (self._btn_rotary_state + 1) % rotary_stops
                # Calculate output value based on current stop
                # Divide range into (stops-1) intervals so last stop hits max
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
                self.debug.emit(f"CH{self.idx+1} button idx={idx} raw={v}, inv={inv}, min={mn}, max={mx} -> out={out}")
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
        if src in SRC_CHOICES:
            self.src.setCurrentText(src)
        self.idxBox.setValue(idx)

# -------------------------------------------------------------------

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

        # Joystick (auto-scanning)
        self.joy = Joy()
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
