"""
Serial Interface Module

This module contains the SerialThread class which handles all serial communication
with the ELRS module, including:
- CRSF frame parsing and transmission
- Device discovery and parameter loading
- Telemetry and channel data processing
- Connection management
"""

import time
import struct
import threading
import serial
from PyQt5 import QtCore

from crsf_protocol import (
    CRSF_ADDRESS_FLIGHT_CONTROLLER,
    CRSF_FRAMETYPE_RC_CHANNELS_PACKED,
    CRSF_FRAMETYPE_LINK_STATISTICS,
    CRSF_FRAMETYPE_DEVICE_PING,
    CRSF_FRAMETYPE_DEVICE_INFO,
    CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY,
    CRSF_FRAMETYPE_PARAMETER_READ,
    CRSF_FRAMETYPE_PARAMETER_WRITE,
    CRSF_FRAMETYPE_ELRS_STATUS,
    CRSF_ADDRESS_CRSF_TRANSMITTER,
    CRSF_ADDRESS_TRANSMITTER_LEGACY,
    CRSF_ADDRESS_ELRS_LUA,
    crc8_d5,
    build_crsf_frame,
    build_crsf_channels_frame,
    crsf_val_to_us,
    unpack_crsf_channels,
)

from device_parameters import (
    DeviceParameterParser,
    DeviceInfo,
)

# Constants from main feeder module
CHANNELS = 16
SEND_HZ = 60


class SerialThread(QtCore.QObject):
    """Thread object for managing serial communication with ELRS module.

    This class handles:
    - Serial port connection/reconnection
    - CRSF frame parsing and building
    - Device discovery via ping/info frames
    - Parameter loading and chunked reads
    - Telemetry data extraction
    - Channel data transmission and reception

    Signals:
        telemetry: Emitted with link statistics dict
        debug: Emitted with debug/log messages
        channels_update: Emitted with list of 16 channel values (microseconds)
        connection_status: Emitted with True/False for connected/disconnected
        device_discovered: Emitted with (src, device_dict) when device responds
        device_parameters_loaded: Emitted with (src, device_dict) when params loaded
        device_parameters_progress: Emitted with (src, fetched, total) during loading
        device_parameter_field_updated: Emitted with (src, field_id, parsed_field)
    """

    telemetry = QtCore.pyqtSignal(dict)
    debug = QtCore.pyqtSignal(str)
    channels_update = QtCore.pyqtSignal(list)
    connection_status = QtCore.pyqtSignal(bool)  # True = connected, False = disconnected
    device_discovered = QtCore.pyqtSignal(int, dict)  # src, details
    device_parameters_loaded = QtCore.pyqtSignal(int, dict)  # src, details
    device_parameters_progress = QtCore.pyqtSignal(int, int, int)  # src, fetched_count, total_count
    device_parameter_field_updated = QtCore.pyqtSignal(int, int, dict)  # src, fid, parsed field

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

        # Shared channel buffer between UI and serial thread
        self.channels_lock = threading.Lock()
        self.latest_channels = [1500] * CHANNELS

        # Send interval in microseconds; fixed at 250Hz (4000us)
        self.send_interval_us = 4000  # 250Hz = 4ms period
        self._last_send_time = time.perf_counter()

        # Track last joystick update time to inhibit CRSF TX when joystick disconnected
        self._last_joystick_update_sec = 0.0

        # Pending command queue (mimics ESP32 single-slot queue behavior)
        # Non-RC frames are sent in place of the next scheduled RC frame
        self._pending_cmd_lock = threading.Lock()
        self._pending_cmd_frame = None  # Single-slot queue for non-RC frames

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
        """Attempt to connect to the serial port."""
        try:
            if self.ser:
                try:
                    self.ser.close()
                except:
                    pass
            self.ser = serial.Serial(self.port, self.baud, timeout=0.001, write_timeout=1.0)
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
        """Emit connection status if it changed."""
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
        try:
            self._on_disconnect()
        except Exception:
            pass

    def send_channels(self, ch16):
        """Update the channel buffer to be sent at the configured frequency.

        Args:
            ch16: List of channel values in microseconds (1000-2000)
        """
        try:
            with self.channels_lock:
                self.latest_channels = list(ch16[:CHANNELS]) + [1500] * max(0, CHANNELS - len(ch16))
            # Mark joystick activity (update timestamp)
            self._last_joystick_update_sec = time.time()
        except Exception as e:
            self.debug.emit(f"send_channels error: {e}")

    def run(self):
        """Main thread loop: reads frames, sends channels, and manages discovery."""
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
                        # Check if we have a pending command to send instead of RC frame
                        pending_cmd = None
                        with self._pending_cmd_lock:
                            if self._pending_cmd_frame is not None:
                                pending_cmd = self._pending_cmd_frame
                                self._pending_cmd_frame = None  # Clear the slot
                        
                        if self.ser:
                            try:
                                if pending_cmd is not None:
                                    # Send pending command instead of RC frame (mimics ESP32 behavior)
                                    pkt = pending_cmd
                                else:
                                    # Send normal RC frame
                                    with self.channels_lock:
                                        channels_copy = list(self.latest_channels)
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
        """Handle a parsed CRSF frame.

        Args:
            t: Frame type byte
            payload: Frame payload bytes
        """
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
            chans = unpack_crsf_channels(payload)
            # Emit channels (microseconds) via a dedicated signal so the GUI can update controls
            self.channels_update.emit(chans)
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
        """Queue a CRSF command frame to be sent in place of next RC frame.
        
        This mimics the ESP32 behavior where non-RC frames are queued and sent
        instead of the next scheduled RC frame, preventing simultaneous sends.

        Args:
            ftype: Frame type byte
            payload: Frame payload bytes
        """
        if not self.ser:
            return
        try:
            pkt = build_crsf_frame(ftype, payload)
            
            # Queue the frame to be sent in place of next RC frame
            queued = False
            with self._pending_cmd_lock:
                if self._pending_cmd_frame is None:
                    self._pending_cmd_frame = pkt
                    queued = True

        except Exception as e:
            self.debug.emit(f"_send_crsf_cmd error: {e}")

    def _trigger_one_shot_discovery(self, src=None):
        """Send one-shot DEVICE_PING packets immediately and request parameter reads on response.

        This is used when we receive the first CRSF packet from the TX to auto-start discovery.

        Args:
            src: Source address that triggered discovery (optional)
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

        Args:
            device_id: Device source address
            field_id: Parameter field ID to read
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

        Args:
            device_id: Device source address to reload
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
        """Parse the CRSF DEVICE_INFO payload and add to elrs_devices dict.

        Args:
            payload: Raw DEVICE_INFO payload bytes
        """
        try:
            # Use DeviceInfo parser to extract fields
            dst, src, name, serial, hw_ver, sw_ver, fields_count, proto_ver = DeviceInfo.parse_device_info_payload(payload)
        except Exception as e:
            raise ValueError(f"DeviceInfo parsing failed: {e}")

        self.debug.emit(f"DEVICE_INFO parsed: name='{name}', sw={sw_ver}, n_params={fields_count}, proto_ver={proto_ver}")

        # Merge into existing device entry if present to avoid losing parsed fields on duplicate DEVICE_INFO
        existing = src in self.elrs_devices
        if existing:
            dev = self.elrs_devices[src]
            dev["name"] = name
            dev["serial"] = serial
            dev["hw_ver"] = hw_ver
            dev["sw_ver"] = sw_ver
            # If new field count is greater than 0 and was previously 0, adopt it
            prev_n = dev.get("n_params", 0)
            if fields_count and fields_count != prev_n:
                dev["n_params"] = fields_count
            dev["proto_ver"] = proto_ver
        else:
            self.elrs_devices[src] = {
                "name": name,
                "serial": serial,
                "hw_ver": hw_ver,
                "sw_ver": sw_ver,
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

        Args:
            payload: Raw parameter settings entry payload
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
                parsed = DeviceParameterParser.parse_field_blob(full)
                # Validate parsed field - check for common corruption indicators
                is_valid = DeviceParameterParser.validate_parsed_field(parsed, field_id)
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
                    self.debug.emit(f"DeviceParameterParser.parse_field_blob error (attempt {retry_count + 1}/3): {e}, requesting retry")
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
                    self.debug.emit(f"DeviceParameterParser.parse_field_blob error after 3 retries: {e}, storing raw data")
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
