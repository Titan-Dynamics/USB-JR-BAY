"""
Unit tests for CRSF State Machine

Tests cover:
- RC frame transmission at 250Hz (4ms intervals)
- Channel value updates
- Failsafe mode (no RC frames sent)
- Non-RC frame interleaving
- Queue management for multiple queued frames
"""

import unittest
from unittest.mock import Mock, patch
from crsf_state_machine import CRSFStateMachine
from crsf_protocol import (
    CRSF_FRAMETYPE_RC_CHANNELS,
    CRSF_FRAMETYPE_PING_DEVICES,
    CRSF_FRAMETYPE_PARAMETER_READ,
    unpack_channels,
    convert_channel_crsf_to_1000_2000
)


class TestCRSFStateMachine(unittest.TestCase):
    """Test suite for CRSF State Machine"""

    def setUp(self):
        """Set up test fixtures"""
        self.state_machine = CRSFStateMachine(rc_interval_ms=4)
        self.sent_frames = []
        self.mock_time = 0.004  # Start at 4ms so first call will send immediately
        
        # Create mock serial port with Arduino-style interface
        self.mock_serial = Mock()
        self.mock_serial.available.return_value = 0
        self.mock_serial.read.return_value = -1
        self.mock_serial.write.side_effect = lambda frame: self.sent_frames.append(frame)
        
        self.state_machine.set_serial(self.mock_serial)

    def tearDown(self):
        """Clean up after tests"""
        self.sent_frames.clear()
    
    def advance_time(self, ms):
        """Advance mocked time by specified milliseconds"""
        self.mock_time += ms / 1000.0

    @patch('crsf_state_machine.time.time')
    def test_rc_frames_sent_at_250hz(self, mock_time):
        """Test that RC frames are sent at 250Hz (4ms intervals)"""
        mock_time.return_value = self.mock_time
        
        # Run for 100ms at 4ms intervals (should send 25 frames)
        # First run() will send immediately, then need to advance time for subsequent frames
        for i in range(25):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)
        
        # Should have sent exactly 25 frames
        self.assertEqual(len(self.sent_frames), 25, "Should send exactly 25 frames in 100ms at 250Hz")
        
        # Verify all frames are RC frames
        for frame in self.sent_frames:
            self.assertEqual(frame[2], CRSF_FRAMETYPE_RC_CHANNELS, "All frames should be RC type")

    @patch('crsf_state_machine.time.time')
    def test_channel_values_update(self, mock_time):
        """Test that channel values are correctly updated and sent"""
        mock_time.return_value = self.mock_time
        
        # Set specific channel values (in 1000-2000 range)
        test_channels = [1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800, 1900, 2000, 1500, 1500, 1500, 1500, 1500]
        self.state_machine.update_channels(test_channels, channels_are_1000_2000=True)
        
        # Send an RC frame
        mock_time.return_value = self.mock_time
        self.state_machine.run()
        
        # Should have sent one frame
        self.assertEqual(len(self.sent_frames), 1)
        
        # Extract and verify channel values from the frame
        frame = self.sent_frames[0]
        self.assertEqual(frame[2], CRSF_FRAMETYPE_RC_CHANNELS)
        
        # Unpack channels from frame payload (skip address, length, type)
        packed_channels = frame[3:25]
        unpacked = unpack_channels(packed_channels)
        
        # Convert back to 1000-2000 range for comparison
        unpacked_1000_2000 = [convert_channel_crsf_to_1000_2000(ch) for ch in unpacked]
        
        # Check that channels match expected values
        for i, (expected, actual) in enumerate(zip(test_channels, unpacked_1000_2000)):
            self.assertEqual(expected, actual, 
                                   msg=f"Channel {i+1} mismatch: expected {expected}, got {actual}")

    @patch('crsf_state_machine.time.time')
    def test_no_rc_frames_in_failsafe(self, mock_time):
        """Test that RC frames are not sent when in failsafe mode"""
        mock_time.return_value = self.mock_time
        
        # Enable failsafe
        self.state_machine.set_failsafe(True)
        
        # Try to send frames over 50ms
        for _ in range(13):  # 13 * 4ms = 52ms
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)
        
        # Should have sent no frames (failsafe with no queued frames)
        self.assertEqual(len(self.sent_frames), 0, "No RC frames should be sent in failsafe")

    @patch('crsf_state_machine.time.time')
    def test_non_rc_frames_sent_in_failsafe(self, mock_time):
        """Test that non-RC frames are sent in failsafe mode"""
        mock_time.return_value = self.mock_time
        
        # Enable failsafe
        self.state_machine.set_failsafe(True)
        
        # Queue multiple ping frames
        self.state_machine.queue_ping()
        self.state_machine.queue_ping()
        self.state_machine.queue_ping()
        
        # Run state machine to send queued frames
        for _ in range(3):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)
        
        # Should have sent 3 ping frames
        self.assertEqual(len(self.sent_frames), 3, "Should send all queued frames in failsafe")
        
        # Verify all are ping frames
        for frame in self.sent_frames:
            self.assertEqual(frame[2], CRSF_FRAMETYPE_PING_DEVICES, "All frames should be ping type")

    @patch('crsf_state_machine.time.time')
    def test_interleaved_frames_not_in_failsafe(self, mock_time):
        """Test that non-RC and RC frames alternate when not in failsafe"""
        mock_time.return_value = self.mock_time
        
        # Queue 3 ping frames
        self.state_machine.queue_ping()
        self.state_machine.queue_ping()
        self.state_machine.queue_ping()
        
        # Run state machine to send frames (should alternate: RC, Ping, RC, Ping, RC, Ping)
        for _ in range(6):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)
        
        # Should have sent exactly 6 frames (3 RC + 3 Ping)
        self.assertEqual(len(self.sent_frames), 6, "Should send exactly 6 frames (3 RC + 3 Ping)")
        
        # Verify alternating pattern: RC, Ping, RC, Ping, RC, Ping
        expected_pattern = [
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
        ]
        
        actual_pattern = [frame[2] for frame in self.sent_frames]
        self.assertEqual(actual_pattern, expected_pattern, 
                        "Frames should alternate: RC, Ping, RC, Ping, RC, Ping")

    @patch('crsf_state_machine.time.time')
    def test_multiple_queued_frames_cycled_correctly(self, mock_time):
        """Test that multiple queued frames are sent in order"""
        mock_time.return_value = self.mock_time
        
        # Queue 5 different frame types
        self.state_machine.queue_ping()
        self.state_machine.queue_param_request(0xEE, 1)  # Param request
        self.state_machine.queue_ping()
        self.state_machine.queue_param_request(0xEE, 2)
        self.state_machine.queue_ping()
        
        # Run state machine (should send: RC, Ping, RC, Param, RC, Ping, RC, Param, RC, Ping)
        for _ in range(10):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)
        
        # Should have sent exactly 10 frames total (5 RC + 5 queued)
        self.assertEqual(len(self.sent_frames), 10, "Should send exactly 10 frames")
        
        # Extract frame types
        frame_types = [frame[2] for frame in self.sent_frames]
        
        # Verify pattern
        expected_types = [
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
        
        self.assertEqual(frame_types, expected_types, 
                        "Frames should be sent in correct alternating order")

    @patch('crsf_state_machine.time.time')
    def test_queue_full_handling(self, mock_time):
        """Test that queue handles overflow correctly"""
        mock_time.return_value = self.mock_time
        
        # Queue is limited to 10 frames
        # Try to queue 15 frames
        results = []
        for i in range(15):
            result = self.state_machine.queue_ping()
            results.append(result)
        
        # First 10 should succeed, last 5 should fail
        self.assertEqual(results[:10], [True] * 10, "First 10 queues should succeed")
        self.assertEqual(results[10:], [False] * 5, "Remaining queues should fail")
        
        # Verify queue size
        stats = self.state_machine.get_stats()
        self.assertEqual(stats['queued_frames'], 10, "Queue should be at max capacity")

    @patch('crsf_state_machine.time.time')
    def test_rc_frames_after_queue_empty(self, mock_time):
        """Test that RC frames resume normally after queue is empty"""
        mock_time.return_value = self.mock_time
        
        # Queue 2 frames
        self.state_machine.queue_ping()
        self.state_machine.queue_ping()
        
        # Run state machine to clear queue and send more RC frames
        for _ in range(8):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)
        
        # Should have sent: RC, Ping, RC, Ping, RC, RC, RC, RC
        self.assertEqual(len(self.sent_frames), 8, "Should send 8 frames total")
        
        # First 4 should be RC, Ping, RC, Ping
        first_four_types = [frame[2] for frame in self.sent_frames[:4]]
        expected_first_four = [
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
            CRSF_FRAMETYPE_RC_CHANNELS,
            CRSF_FRAMETYPE_PING_DEVICES,
        ]
        self.assertEqual(first_four_types, expected_first_four)
        
        # Rest should all be RC frames
        remaining_types = [frame[2] for frame in self.sent_frames[4:]]
        self.assertTrue(all(t == CRSF_FRAMETYPE_RC_CHANNELS for t in remaining_types),
                       "All remaining frames should be RC type")

    @patch('crsf_state_machine.time.time')
    def test_stats_tracking(self, mock_time):
        """Test that statistics are tracked correctly"""
        mock_time.return_value = self.mock_time
        
        # Send some frames
        for _ in range(5):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)
        
        stats = self.state_machine.get_stats()
        
        # Check stats structure
        self.assertIn('rc_frames_sent', stats)
        self.assertIn('last_frame_time', stats)
        self.assertIn('failsafe', stats)
        self.assertIn('queued_frames', stats)
        
        # Verify RC frames sent count
        self.assertEqual(stats['rc_frames_sent'], len(self.sent_frames))
        self.assertFalse(stats['failsafe'])
        self.assertEqual(stats['queued_frames'], 0)

    @patch('crsf_state_machine.time.time')
    def test_reset_clears_state(self, mock_time):
        """Test that reset clears all state correctly"""
        mock_time.return_value = self.mock_time
        
        # Queue some frames and send some
        self.state_machine.queue_ping()
        self.state_machine.queue_ping()
        
        for _ in range(3):
            mock_time.return_value = self.mock_time
            self.state_machine.run()
            self.advance_time(4)
        
        # Reset
        self.state_machine.reset()
        
        # Check that state is cleared
        stats = self.state_machine.get_stats()
        self.assertEqual(stats['rc_frames_sent'], 0, "RC frame count should be reset")
        self.assertEqual(stats['queued_frames'], 0, "Queue should be empty")
        self.assertEqual(stats['last_frame_time'], 0.0, "Last frame time should be reset")
        
        # Channels should be centered
        self.assertEqual(self.state_machine.channels, [992] * 16, "Channels should be centered")


if __name__ == '__main__':
    unittest.main()
