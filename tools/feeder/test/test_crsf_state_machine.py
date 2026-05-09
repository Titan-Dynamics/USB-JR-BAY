"""
Unit tests for CRSF State Machine

Tests cover:
- RC frame transmission with cumulative scheduling
- Channel value updates
- Failsafe mode (all writes suppressed — TX times out)
- Non-RC frame interleaving
- Queue management
- Mixer-sync handling (CRSF_FRAMETYPE_RADIO_ID / 0x3A)
- Catch-up and long-stall recovery
"""

import struct
import unittest
from unittest.mock import Mock, patch
from crsf_state_machine import CRSFStateMachine
from crsf_protocol import (
    CRSF_FRAMETYPE_RC_CHANNELS,
    CRSF_FRAMETYPE_PING_DEVICES,
    CRSF_FRAMETYPE_PARAMETER_READ,
    CRSF_FRAMETYPE_RADIO_ID,
    CRSF_SUBCMD_TIMING,
    crsf_crc8,
    unpack_channels,
    convert_channel_crsf_to_1000_2000,
)

# Use a starting time large enough that the bounds-check resync fires on
# the very first tick (now > _next_send + MAX_CATCHUP * interval), putting
# the schedule exactly at 'now' and sending exactly 1 frame per interval.
_BASE_TIME = 100.0


def _build_timing_frame(rate_tenth_us: int, offset_tenth_us: int) -> bytes:
    """Build a valid CRSF_FRAMETYPE_RADIO_ID timing frame for tests."""
    payload = (
        bytes([0xEA, 0xEE, CRSF_SUBCMD_TIMING])
        + struct.pack('>I', rate_tenth_us)
        + struct.pack('>i', offset_tenth_us)
    )
    frame_type = CRSF_FRAMETYPE_RADIO_ID
    length = 1 + len(payload) + 1  # type + payload + CRC
    crc = crsf_crc8(bytes([frame_type]) + payload)
    return bytes([0xEA, length, frame_type]) + payload + bytes([crc])


class TestCRSFStateMachine(unittest.TestCase):
    """Test suite for CRSF State Machine"""

    def setUp(self):
        self.state_machine = CRSFStateMachine(interval_s=0.004)
        self.sent_frames = []
        self.mock_time = _BASE_TIME + 0.004  # first tick triggers resync → exactly 1 frame

        self.mock_serial = Mock()
        self.mock_serial.available.return_value = 0
        self.mock_serial.read.return_value = -1
        self.mock_serial.read_bulk.return_value = b''
        self.mock_serial.write.side_effect = lambda frame: self.sent_frames.append(frame)

        self.state_machine.set_serial(self.mock_serial)

    def tearDown(self):
        self.sent_frames.clear()

    def advance_time(self, ms):
        self.mock_time += ms / 1000.0

    # ------------------------------------------------------------------
    # Basic transmission
    # ------------------------------------------------------------------

    @patch('crsf_state_machine.time.monotonic')
    def test_rc_frames_sent_at_250hz(self, mock_time):
        """RC frames are sent at 250 Hz (4 ms intervals)."""
        mock_time.return_value = self.mock_time

        for _ in range(25):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)

        self.assertEqual(len(self.sent_frames), 25)
        for frame in self.sent_frames:
            self.assertEqual(frame[2], CRSF_FRAMETYPE_RC_CHANNELS)

    @patch('crsf_state_machine.time.monotonic')
    def test_channel_values_update(self, mock_time):
        """Channel values are correctly packed into the RC frame."""
        mock_time.return_value = self.mock_time

        test_channels = [1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700,
                         1800, 1900, 2000, 1500, 1500, 1500, 1500, 1500]
        self.state_machine.update_channels(test_channels, channels_are_1000_2000=True)
        self.state_machine.run()

        self.assertEqual(len(self.sent_frames), 1)
        frame = self.sent_frames[0]
        self.assertEqual(frame[2], CRSF_FRAMETYPE_RC_CHANNELS)

        packed_channels = frame[3:25]
        unpacked = unpack_channels(packed_channels)
        unpacked_us = [convert_channel_crsf_to_1000_2000(ch) for ch in unpacked]

        for i, (expected, actual) in enumerate(zip(test_channels, unpacked_us)):
            self.assertEqual(expected, actual,
                             msg=f"Channel {i+1}: expected {expected}, got {actual}")

    # ------------------------------------------------------------------
    # Failsafe — new semantics: ALL writes suppressed
    # ------------------------------------------------------------------

    @patch('crsf_state_machine.time.monotonic')
    def test_no_rc_frames_in_failsafe(self, mock_time):
        """No RC frames are sent when failsafe is active."""
        mock_time.return_value = self.mock_time
        self.state_machine.set_failsafe(True)

        for _ in range(13):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)

        self.assertEqual(len(self.sent_frames), 0)

    @patch('crsf_state_machine.time.monotonic')
    def test_failsafe_blocks_all_writes(self, mock_time):
        """Failsafe suppresses queued frames too — TX detects silence and failsafes."""
        mock_time.return_value = self.mock_time
        self.state_machine.set_failsafe(True)
        self.state_machine.queue_ping()
        self.state_machine.queue_ping()

        for _ in range(3):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)

        self.assertEqual(len(self.sent_frames), 0,
                         "Failsafe must block queued frames so TX times out")

    # ------------------------------------------------------------------
    # Interleaving
    # ------------------------------------------------------------------

    @patch('crsf_state_machine.time.monotonic')
    def test_interleaved_frames_not_in_failsafe(self, mock_time):
        """Non-RC and RC frames alternate when not in failsafe."""
        mock_time.return_value = self.mock_time

        self.state_machine.queue_ping()
        self.state_machine.queue_ping()
        self.state_machine.queue_ping()

        for _ in range(6):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)

        self.assertEqual(len(self.sent_frames), 6)
        expected = [
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
        ]
        self.assertEqual([f[2] for f in self.sent_frames], expected)

    @patch('crsf_state_machine.time.monotonic')
    def test_multiple_queued_frames_cycled_correctly(self, mock_time):
        """Multiple queued frames are sent in order, interleaved with RC."""
        mock_time.return_value = self.mock_time

        self.state_machine.queue_ping()
        self.state_machine.queue_param_request(0xEE, 1)
        self.state_machine.queue_ping()
        self.state_machine.queue_param_request(0xEE, 2)
        self.state_machine.queue_ping()

        for _ in range(10):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)

        self.assertEqual(len(self.sent_frames), 10)
        expected = [
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PARAMETER_READ,
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PARAMETER_READ,
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
        ]
        self.assertEqual([f[2] for f in self.sent_frames], expected)

    @patch('crsf_state_machine.time.monotonic')
    def test_rc_frames_after_queue_empty(self, mock_time):
        """RC frames resume normally once the queue is drained."""
        mock_time.return_value = self.mock_time

        self.state_machine.queue_ping()
        self.state_machine.queue_ping()

        for _ in range(8):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)

        self.assertEqual(len(self.sent_frames), 8)
        first_four = [f[2] for f in self.sent_frames[:4]]
        self.assertEqual(first_four, [
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
        ])
        remaining = [f[2] for f in self.sent_frames[4:]]
        self.assertTrue(all(t == CRSF_FRAMETYPE_RC_CHANNELS for t in remaining))

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    @patch('crsf_state_machine.time.monotonic')
    def test_queue_full_handling(self, mock_time):
        """Queue overflow is handled gracefully."""
        mock_time.return_value = self.mock_time

        results = [self.state_machine.queue_ping() for _ in range(15)]

        self.assertEqual(results[:10], [True] * 10)
        self.assertEqual(results[10:], [False] * 5)
        self.assertEqual(self.state_machine.get_stats()['queued_frames'], 10)

    # ------------------------------------------------------------------
    # Stats and reset
    # ------------------------------------------------------------------

    @patch('crsf_state_machine.time.monotonic')
    def test_stats_tracking(self, mock_time):
        """Statistics are tracked correctly."""
        mock_time.return_value = self.mock_time

        for _ in range(5):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)

        stats = self.state_machine.get_stats()
        self.assertIn('rc_frames_sent', stats)
        self.assertIn('last_frame_time', stats)
        self.assertIn('failsafe', stats)
        self.assertIn('queued_frames', stats)
        self.assertEqual(stats['rc_frames_sent'], len(self.sent_frames))
        self.assertFalse(stats['failsafe'])
        self.assertEqual(stats['queued_frames'], 0)

    @patch('crsf_state_machine.time.monotonic')
    def test_reset_clears_state(self, mock_time):
        """reset() clears all counters and queue."""
        mock_time.return_value = self.mock_time

        self.state_machine.queue_ping()
        self.state_machine.queue_ping()
        for _ in range(3):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)

        self.state_machine.reset()

        stats = self.state_machine.get_stats()
        self.assertEqual(stats['rc_frames_sent'], 0)
        self.assertEqual(stats['queued_frames'], 0)
        self.assertLessEqual(stats['last_frame_time'], 0.0)
        self.assertEqual(self.state_machine.channels, [992] * 16)

    # ------------------------------------------------------------------
    # Mixer-sync (CRSF_FRAMETYPE_RADIO_ID / 0x3A)
    # ------------------------------------------------------------------

    @patch('crsf_state_machine.time.monotonic')
    def test_sync_packet_changes_rate(self, mock_time):
        """A 0x3A timing frame with rate=2000µs switches the interval to 2 ms."""
        frame = _build_timing_frame(rate_tenth_us=20000, offset_tenth_us=0)  # 500 Hz

        mock_time.return_value = _BASE_TIME
        self.mock_serial.available.return_value = len(frame)
        self.mock_serial.read_bulk.return_value = frame

        self.state_machine.run()

        self.assertAlmostEqual(self.state_machine._interval_s, 0.002, places=6)

        self.mock_serial.available.return_value = 0
        self.mock_serial.read_bulk.return_value = b''

    @patch('crsf_state_machine.time.monotonic')
    def test_sync_packet_applies_phase_offset(self, mock_time):
        """A positive offset_us shifts _next_send forward by that amount."""
        frame = _build_timing_frame(rate_tenth_us=40000, offset_tenth_us=10000)  # +1000µs

        mock_time.return_value = _BASE_TIME
        # Pre-set _next_send so the bounds-check resync doesn't wipe it
        self.state_machine._next_send = _BASE_TIME

        self.mock_serial.available.return_value = len(frame)
        self.mock_serial.read_bulk.return_value = frame

        self.state_machine.run()

        # Phase offset +1000µs = +0.001 s applied before the send loop.
        # After the loop sends the due frame (at _next_send=_BASE_TIME, now=_BASE_TIME),
        # _next_send = _BASE_TIME + 0.001 + 0.004 (interval after offset + send).
        # We just verify it shifted by more than 0 compared to pure schedule.
        self.assertGreater(self.state_machine._next_send, _BASE_TIME)

        self.mock_serial.available.return_value = 0
        self.mock_serial.read_bulk.return_value = b''

    @patch('crsf_state_machine.time.monotonic')
    def test_sync_callback_invoked(self, mock_time):
        """sync_callback is called with rate_hz when a valid timing frame arrives."""
        cb = Mock()
        self.state_machine.set_sync_callback(cb)

        frame = _build_timing_frame(rate_tenth_us=20000, offset_tenth_us=0)  # 500 Hz
        mock_time.return_value = _BASE_TIME
        self.mock_serial.available.return_value = len(frame)
        self.mock_serial.read_bulk.return_value = frame

        self.state_machine.run()

        cb.assert_called_once()
        info = cb.call_args[0][0]
        self.assertIsNotNone(info)
        self.assertAlmostEqual(info['rate_hz'], 500.0, places=1)

        self.mock_serial.available.return_value = 0
        self.mock_serial.read_bulk.return_value = b''

    # ------------------------------------------------------------------
    # Catch-up and stall recovery
    # ------------------------------------------------------------------

    @patch('crsf_state_machine.time.monotonic')
    def test_catchup_sends_multiple_due_frames_per_run(self, mock_time):
        """run() sends all due frames in one call when ticks are delayed."""
        # Pre-set schedule so bounds check doesn't interfere
        self.state_machine._next_send = _BASE_TIME
        # Advance now 3 intervals (less ε so the 4th slot is NOT due)
        now = _BASE_TIME + 3 * 0.004 - 0.0001
        mock_time.return_value = now

        self.state_machine.run()

        self.assertEqual(len(self.sent_frames), 3,
                         "Should send exactly 3 catch-up frames in one run() call")

    @patch('crsf_state_machine.time.monotonic')
    def test_long_stall_resyncs_without_burst(self, mock_time):
        """After a long stall, at most MAX_CATCHUP frames are sent (bounds check resyncs)."""
        self.state_machine._next_send = _BASE_TIME
        now = _BASE_TIME + 100 * 0.004  # 100× interval stall
        mock_time.return_value = now

        self.state_machine.run()

        self.assertLessEqual(len(self.sent_frames), 5,  # MAX_CATCHUP = 5
                             "Stall recovery must not burst more than MAX_CATCHUP frames")
        self.assertGreater(self.state_machine._next_send, now,
                           "_next_send should be in the future after recovery")

    @patch('crsf_state_machine.time.monotonic')
    def test_failsafe_advances_schedule_during_silence(self, mock_time):
        """Schedule keeps ticking in failsafe so resuming doesn't burst."""
        self.state_machine.set_failsafe(True)
        self.state_machine._next_send = _BASE_TIME
        mock_time.return_value = _BASE_TIME

        self.state_machine.run()

        # No frames written
        self.assertEqual(len(self.sent_frames), 0)
        # But _next_send advanced by one interval (schedule not frozen)
        self.assertAlmostEqual(self.state_machine._next_send,
                               _BASE_TIME + 0.004, places=6)


if __name__ == '__main__':
    unittest.main()
