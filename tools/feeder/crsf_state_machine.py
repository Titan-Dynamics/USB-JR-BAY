"""
CRSF State Machine Module

This module manages the state machine for sending and receiving CRSF frames.
It handles:
- RC frame transmission at regular intervals
- Queued parameter/command frames
- Failsafe handling

The state machine is decoupled from UART operations
"""

import time
from typing import Optional, Callable, List
from collections import deque
from crsf_protocol import (
    build_rc_frame, build_ping_frame, build_param_request,
    convert_channel_1000_2000_to_crsf
)


class CRSFStateMachine:
    """
    State machine for managing CRSF frame transmission.

    This class handles the timing and queuing of different frame types:
    - Regular RC frames (sent regularly at specified interval)
    - Queued parameter/command frames (sent in place of RC frames)
    - Device ping frames

    Attributes:
        rc_interval_ms: Interval between frames in milliseconds
        failsafe: Whether the system is in failsafe mode
    """

    def __init__(self, rc_interval_ms: int = 4):
        """
        Initialize the CRSF state machine.

        Args:
            rc_interval_ms: Interval between RC frames in milliseconds (default: 4ms = 250Hz)
        """
        self.rc_interval_ms = rc_interval_ms

        # State tracking
        self.last_frame_time = 0.0
        self.rc_frames_sent = 0
        self.failsafe = False
        self.last_was_rc = False  # Track if last frame sent was RC (for alternating)
        
        # Channel data (CRSF range: 0-1984)
        self.channels = [992] * 16  # Default to center (992 = CRSF center)

        # Output frame queue (for params, pings, etc.)
        self.output_queue = deque(maxlen=10)

        # Callback for sending frames (set externally for decoupling)
        self.send_callback: Optional[Callable[[bytes], None]] = None

    def set_send_callback(self, callback: Callable[[bytes], None]):
        """
        Set the callback function for sending frames.

        Args:
            callback: Function that takes frame bytes and sends them via UART/serial
                     Signature: callback(frame: bytes) -> None
        """
        self.send_callback = callback

    def update_channels(self, channels: List[int], channels_are_1000_2000: bool = True):
        """
        Update RC channel values.

        Args:
            channels: List of 16 channel values
        """
        if len(channels) != 16:
            raise ValueError(f"Expected 16 channels, got {len(channels)}")

        # Convert from 1000-2000 to CRSF 0-1984
        self.channels = [convert_channel_1000_2000_to_crsf(ch) for ch in channels]

    def set_failsafe(self, failsafe: bool):
        """
        Set failsafe mode.

        When in failsafe, RC frames are not sent.

        Args:
            failsafe: True to enable failsafe, False to disable
        """
        self.failsafe = failsafe

    def queue_frame(self, frame: bytes) -> bool:
        """
        Queue a frame to be sent in the next available slot.

        Queued frames are sent instead of RC frames when the queue is not empty.

        Args:
            frame: Complete CRSF frame (with address, length, type, payload, CRC)

        Returns:
            True if frame was queued successfully, False if queue is full
        """
        if len(self.output_queue) >= self.output_queue.maxlen:
            return False

        self.output_queue.append(frame)
        return True

    def queue_ping(self) -> bool:
        """
        Queue a device ping frame.

        Returns:
            True if ping was queued successfully
        """
        return self.queue_frame(build_ping_frame())

    def queue_param_request(self, device_addr: int, param_index: int, chunk: int = 0) -> bool:
        """
        Queue a parameter request frame.

        Args:
            device_addr: Target device address
            param_index: Parameter index to read
            chunk: Chunk number for multi-chunk parameters

        Returns:
            True if request was queued successfully
        """
        return self.queue_frame(build_param_request(device_addr, param_index, chunk))

    def is_time_to_send_frame(self) -> bool:
        """
        Check if it's time to send the next frame.

        Returns:
            True if enough time has elapsed since the last frame
        """
        current_time = time.time()
        return (current_time - self.last_frame_time) >= self.rc_interval_ms / 1000.0

    def run(self):
        """
        Execute one iteration of the state machine.

        This should be called frequently from the main loop.

        Logic:
        - If in failsafe: only send non-RC frames back-to-back
        - If not in failsafe and have queued frames: alternate between non-RC and RC
        - If no non-RC frames: send RC frames back-to-back (unless in failsafe)
        """
        # Check if it's time to send something
        if not self.is_time_to_send_frame():
            return

        # Case 1: In failsafe - only send queued frames
        if self.failsafe:
            if self.output_queue:
                frame = self.output_queue.popleft()
                self.last_frame_time = time.time()
                self.last_was_rc = False
                if self.send_callback:
                    self.send_callback(frame)
                return
            # In failsafe with no queued frames: don't send anything
            return

        # Case 2: Not in failsafe with queued frames - alternate between queued and RC
        if self.output_queue:
            if self.last_was_rc:
                # Last was RC, now send queued frame
                frame = self.output_queue.popleft()
                self.last_frame_time = time.time()
                self.last_was_rc = False
                if self.send_callback:
                    self.send_callback(frame)
                return
            else:
                # Last was not RC (or first frame), send RC frame
                frame = build_rc_frame(self.channels)
                self.last_frame_time = time.time()
                self.rc_frames_sent += 1
                self.last_was_rc = True
                if self.send_callback:
                    self.send_callback(frame)
                return

        # Case 3: No queued frames - send RC frames back-to-back
        frame = build_rc_frame(self.channels)
        self.last_frame_time = time.time()
        self.rc_frames_sent += 1
        self.last_was_rc = True
        if self.send_callback:
            self.send_callback(frame)

    def get_stats(self) -> dict:
        """
        Get statistics about the state machine.

        Returns:
            Dictionary with statistics:
                - rc_frames_sent: Total RC frames sent
                - last_frame_time: Timestamp of last frame
                - failsafe: Current failsafe state
                - queued_frames: Number of frames in output queue
        """
        return {
            'rc_frames_sent': self.rc_frames_sent,
            'last_frame_time': self.last_frame_time,
            'failsafe': self.failsafe,
            'queued_frames': len(self.output_queue),
        }

    def reset(self):
        """
        Reset the state machine to initial state.

        Clears all queued frames and resets counters.
        """
        self.last_frame_time = 0.0
        self.rc_frames_sent = 0
        self.last_was_rc = False
        self.output_queue.clear()
        self.channels = [992] * 16  # Reset to center
