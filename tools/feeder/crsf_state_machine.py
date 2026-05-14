"""
CRSF State Machine Module

This module manages the state machine for sending and receiving CRSF frames.
It handles:
- RC frame transmission at dynamic intervals synced from TX timing packets
- Queued parameter/command frames
- Failsafe handling (silence on joystick disconnect so TX times out)

The state machine is decoupled from UART operations and must be driven from
a single thread (the GUI tick loop). No internal threads.
"""

import time
from typing import Optional, Callable, List
from collections import deque
from crsf_protocol import (
    build_rc_frame, build_ping_frame, build_param_request,
    convert_channel_1000_2000_to_crsf, CRSFFrameDecoder,
    CRSF_FRAMETYPE_LINK_STATISTICS, parse_link_statistics,
    CRSF_FRAMETYPE_RADIO_ID, parse_handset_timing,
    CRSF_FRAMETYPE_DEVICE_INFO,
    CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY,
    CRSF_FRAMETYPE_ELRS_STATUS,
)

# On Windows, QTimer(0) fires at ~64–250 Hz (15.6 ms OS quantum) rather than
# the near-continuous rate on macOS/Linux. With a 1 ms RC interval the old
# value of 5 set the reset threshold to 5 ms, which fired on every Windows
# tick and capped throughput to ~100–250 frames/sec instead of ~1000.
# 25 gives a 25 ms threshold: covers Windows 64 Hz (15.6 ms) ticks without
# triggering a reset, while still resetting after any real stall (e.g. sleep).
_MAX_CATCHUP = 25
_LOST_SYNC_AFTER_S = 2.0


class CRSFStateMachine:
    """
    State machine for managing CRSF frame transmission.

    Driven from the GUI tick loop (single-threaded). Cumulative scheduling
    with a catch-up loop handles delayed ticks without dropping frames.

    Attributes:
        _interval_s: Current send interval in seconds (updated by sync packets)
        failsafe: When True, no frames are sent (TX times out → RX failsafe)
    """

    def __init__(self, interval_s: float = 0.004):
        """
        Initialize the CRSF state machine.

        Args:
            interval_s: Initial send interval in seconds (default: 0.004 = 250 Hz)
        """
        self._interval_s = interval_s

        # Cumulative scheduling
        self._next_send = 0.0
        self._last_frame_time = 0.0

        # Sync-lock state
        self._sync_locked = False
        self._last_sync_recv = 0.0

        # State tracking
        self.rc_frames_sent = 0
        self.failsafe = False
        self.last_was_rc = False

        # Channel data (CRSF range)
        self.channels = [992] * 16  # Default: center

        # Output frame queue (for params, pings, etc.)
        self.output_queue = deque(maxlen=10)

        # Serial port object (set externally)
        self.serial_port = None

        # Frame decoder for incoming CRSF frames
        self.frame_decoder = CRSFFrameDecoder()

        # Callbacks (set externally, called on GUI thread)
        self.link_stats_callback: Optional[Callable[[dict], None]] = None
        self.sync_callback: Optional[Callable[[Optional[dict]], None]] = None
        self.lua_callback: Optional[Callable[[dict], None]] = None
        self.lua_tick_callback: Optional[Callable[[float], None]] = None

    def set_serial(self, serial_port):
        """Set the serial port object (must have available(), read_bulk(), write())."""
        self.serial_port = serial_port

    def set_link_stats_callback(self, callback: Callable[[dict], None]):
        """Set callback for link statistics frames. Signature: callback(stats: dict)."""
        self.link_stats_callback = callback

    def set_sync_callback(self, callback: Callable[[Optional[dict]], None]):
        """
        Set callback for CRSF sync-lock state changes.
        Called with {'rate_hz': float, 'offset_us': float} on sync, None on lost-sync.
        Safe to update widgets directly — called inline from the GUI tick.
        """
        self.sync_callback = callback

    def set_lua_callback(self, callback: Callable[[dict], None]):
        """Forward DEVICE_INFO / PARAM_ENTRY / ELRS_STATUS frames to the LUA state machine."""
        self.lua_callback = callback

    def set_lua_tick_callback(self, callback: Callable[[float], None]):
        """Call LuaStateMachine.tick(now) once per run() invocation."""
        self.lua_tick_callback = callback

    def update_channels(self, channels: List[int], channels_are_1000_2000: bool = True):
        """Update RC channel values."""
        if len(channels) != 16:
            raise ValueError(f"Expected 16 channels, got {len(channels)}")
        if channels_are_1000_2000:
            self.channels = [convert_channel_1000_2000_to_crsf(ch) for ch in channels]
        else:
            self.channels = list(channels)

    def set_failsafe(self, failsafe: bool):
        """
        Set failsafe mode.

        When True, no frames are written. The TX detects the silence and
        transitions to noCrossfire after ~1 s; the RX applies its failsafe.
        The send schedule keeps advancing so re-enabling doesn't burst.
        """
        self.failsafe = failsafe

    def queue_frame(self, frame: bytes) -> bool:
        """Queue a frame for transmission. Returns False if queue is full."""
        if len(self.output_queue) >= self.output_queue.maxlen:
            return False
        self.output_queue.append(frame)
        return True

    def queue_ping(self) -> bool:
        """Queue a device ping frame."""
        return self.queue_frame(build_ping_frame())

    def queue_param_request(self, device_addr: int, param_index: int, chunk: int = 0) -> bool:
        """Queue a parameter request frame."""
        return self.queue_frame(build_param_request(device_addr, param_index, chunk))

    def _next_frame_to_send(self) -> Optional[bytes]:
        """
        Pick the next frame to transmit.

        Returns None when in failsafe (caller should still advance the schedule).
        Interleaves queued frames with RC frames when the queue is non-empty.
        """
        if self.failsafe:
            return None

        if self.output_queue:
            if self.last_was_rc:
                # Alternate: queued frame after RC
                frame = self.output_queue.popleft()
                self.last_was_rc = False
                return frame
            else:
                # RC turn
                self.rc_frames_sent += 1
                self.last_was_rc = True
                return build_rc_frame(self.channels)

        # No queued frames: send RC back-to-back
        self.rc_frames_sent += 1
        self.last_was_rc = True
        return build_rc_frame(self.channels)

    def run(self):
        """
        Execute one iteration of the state machine.

        Called once per GUI tick. Drains incoming RX bytes, then sends all
        frames that are due (cumulative catch-up, bounded by _MAX_CATCHUP).
        """
        self._process_incoming_frames()

        if self.lua_tick_callback:
            self.lua_tick_callback(time.monotonic())

        if self.serial_port is None:
            return

        now = time.monotonic()

        # Lost-sync detection: notify UI but keep using last known interval
        if self._sync_locked and (now - self._last_sync_recv) > _LOST_SYNC_AFTER_S:
            self._sync_locked = False
            if self.sync_callback:
                self.sync_callback(None)

        # Resync schedule if too far behind (e.g. after system sleep or modal dialog)
        if self._next_send < now - _MAX_CATCHUP * self._interval_s:
            self._next_send = now

        sent = 0
        while now >= self._next_send and sent < _MAX_CATCHUP:
            frame = self._next_frame_to_send()
            if frame is None:
                # In failsafe: advance schedule so we don't burst on resume
                self._next_send += self._interval_s
                return
            self.serial_port.write(frame)
            self._last_frame_time = now
            self._next_send += self._interval_s
            sent += 1

    def get_stats(self) -> dict:
        """Return state machine statistics."""
        return {
            'rc_frames_sent': self.rc_frames_sent,
            'last_frame_time': self._last_frame_time,
            'failsafe': self.failsafe,
            'queued_frames': len(self.output_queue),
        }

    def reset(self):
        """Reset to initial state. Clears queue and counters."""
        self._next_send = 0.0
        self._last_frame_time = 0.0
        self.rc_frames_sent = 0
        self.last_was_rc = False
        self.output_queue.clear()
        self.channels = [992] * 16

    def _process_incoming_frames(self):
        """Drain all available RX bytes and decode any complete CRSF frames."""
        if not self.serial_port:
            return
        n = self.serial_port.available()
        if n <= 0:
            return
        data = self.serial_port.read_bulk(n)
        for b in data:
            frame = self.frame_decoder.push_byte(b)
            if frame is not None:
                self._handle_received_frame(frame)

    def _handle_received_frame(self, frame: dict):
        """Dispatch a decoded CRSF frame to the appropriate handler."""
        if not frame.get('valid', False):
            return

        frame_type = frame.get('type')

        if frame_type == CRSF_FRAMETYPE_LINK_STATISTICS:
            stats = parse_link_statistics(frame.get('payload', b''))
            if stats and self.link_stats_callback:
                self.link_stats_callback(stats)

        elif frame_type == CRSF_FRAMETYPE_RADIO_ID:
            # Extended frame: payload starts with dest+orig, strip them for the parser
            payload = frame.get('payload', b'')
            sync = parse_handset_timing(payload[2:])
            if sync is None:
                return
            rate_us, offset_us = sync
            self._interval_s = rate_us / 1_000_000
            # Phase correction: only apply on first lock acquisition.
            # The TX sends RADIO_ID after every processed RC frame (~1000/sec on
            # 1000 Hz mode). Applying offset_us on every packet compounds into a
            # permanent rate shift: at -276 µs/packet × 1000 packets/sec the
            # schedule compresses by 276 ms/sec → ~1380 Hz instead of 1000 Hz.
            # Windows USB-CDC adds a fixed ~200-400 µs latency that the phase
            # correction cannot eliminate (we send earlier but USB eats the gain),
            # so the offset never converges to zero and the rate stays elevated.
            # One-shot on lock acquisition aligns the initial phase; after that
            # _interval_s alone tracks the rate correctly.
            if not self._sync_locked:
                self._next_send += offset_us / 1_000_000
            self._last_sync_recv = time.monotonic()
            self._sync_locked = True
            if self.sync_callback:
                self.sync_callback({'rate_hz': 1e6 / rate_us, 'offset_us': offset_us})

        elif frame_type in (CRSF_FRAMETYPE_DEVICE_INFO,
                            CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY,
                            CRSF_FRAMETYPE_ELRS_STATUS):
            if self.lua_callback:
                self.lua_callback(frame)
