"""
Unit tests for CRSF Protocol

Tests cover:
- Link statistics parsing
- RC channels parsing
- Frame decoder (byte-by-byte decoding)
- CRC validation
- Edge cases and error handling
"""

import struct
import unittest
from crsf_protocol import (
    parse_link_statistics, parse_rc_channels, parse_handset_timing,
    CRSFFrameDecoder, crsf_crc8,
    CRSF_FRAMETYPE_LINK_STATISTICS,
    CRSF_FRAMETYPE_RC_CHANNELS,
    CRSF_SUBCMD_TIMING,
    convert_channel_1000_2000_to_crsf,
    CRSF_CHANNEL_MIN, CRSF_CHANNEL_MAX,
)


class TestLinkStatisticsParsing(unittest.TestCase):
    """Test suite for link statistics parsing"""

    def test_valid_link_statistics(self):
        """Test parsing valid link statistics payload.

        RSSI bytes are interpreted as signed int8 (actual dBm, already negative).
        Byte 237 → -19 dBm, byte 242 → -14 dBm, byte 232 → -24 dBm.
        """
        # RSSI1=-19, RSSI2=-14, LQ=95, SNR=8, Ant=0, RFMode=2, TxPwr=20, DRSSI=-24, DLQ=98, DSNR=10
        payload = bytes([237, 242, 95, 8, 0, 2, 20, 232, 98, 10])

        stats = parse_link_statistics(payload)

        self.assertIsNotNone(stats)
        self.assertEqual(stats['uplink_rssi_1'], -19)
        self.assertEqual(stats['uplink_rssi_2'], -14)
        self.assertEqual(stats['uplink_link_quality'], 95)
        self.assertEqual(stats['uplink_snr'], 8)
        self.assertEqual(stats['active_antenna'], 0)
        self.assertEqual(stats['rf_mode'], 2)
        self.assertEqual(stats['uplink_tx_power'], 20)
        self.assertEqual(stats['downlink_rssi'], -24)
        self.assertEqual(stats['downlink_link_quality'], 98)
        self.assertEqual(stats['downlink_snr'], 10)

    def test_negative_snr_values(self):
        """Test that signed SNR values are correctly parsed."""
        # SNR: -5 (251 unsigned), -10 (246 unsigned)
        # RSSI: -19 (237 unsigned), -14 (242 unsigned), -24 (232 unsigned)
        payload = bytes([237, 242, 95, 251, 0, 2, 20, 232, 98, 246])

        stats = parse_link_statistics(payload)

        self.assertIsNotNone(stats)
        self.assertEqual(stats['uplink_snr'], -5)
        self.assertEqual(stats['downlink_snr'], -10)
        self.assertEqual(stats['uplink_rssi_1'], -19)
        self.assertEqual(stats['downlink_rssi'], -24)

    def test_invalid_payload_too_short(self):
        """Test that short payloads return None"""
        payload = bytes([100, 105, 95])  # Only 3 bytes, need 10
        
        stats = parse_link_statistics(payload)
        
        self.assertIsNone(stats)

    def test_empty_payload(self):
        """Test that empty payload returns None"""
        payload = bytes()
        
        stats = parse_link_statistics(payload)
        
        self.assertIsNone(stats)


class TestRCChannelsParsing(unittest.TestCase):
    """Test suite for RC channels parsing"""

    def test_valid_rc_channels_center(self):
        """Test parsing RC channels with all center values (992)"""
        # 992 in 11-bit = 0x3E0
        # Packed representation for 16 channels all at 992
        # This is complex bit packing, so using known good values
        packed = bytes([
            0xE0, 0x03, 0x1F, 0xF8, 0xC0, 0x07, 0x3E, 0xF0,
            0x81, 0x0F, 0x7C, 0xE0, 0x03, 0x1F, 0xF8, 0xC0,
            0x07, 0x3E, 0xF0, 0x81, 0x0F, 0x7C
        ])
        
        channels = parse_rc_channels(packed)
        
        self.assertIsNotNone(channels)
        self.assertEqual(len(channels), 16)
        # All channels should be 992 (center)
        for ch in channels:
            self.assertEqual(ch, 992)

    def test_valid_rc_channels_min_max(self):
        """Test parsing RC channels with min (192) and max (1792) values"""
        # Channel 0 = 192 (0x0C0), Channel 1 = 1792 (0x700)
        # Remaining channels at center (992 = 0x3E0)
        # Correct packed representation from pack_channels([192, 1792] + [992]*14)
        packed = bytes([
            0xC0, 0x00, 0x38, 0xF8, 0xC0, 0x07, 0x3E, 0xF0,
            0x81, 0x0F, 0x7C, 0xE0, 0x03, 0x1F, 0xF8, 0xC0,
            0x07, 0x3E, 0xF0, 0x81, 0x0F, 0x7C
        ])
        
        channels = parse_rc_channels(packed)
        
        self.assertIsNotNone(channels)
        self.assertEqual(len(channels), 16)
        self.assertEqual(channels[0], 192)
        self.assertEqual(channels[1], 1792)

    def test_invalid_payload_wrong_length(self):
        """Test that wrong length payloads return None"""
        payload = bytes([0] * 20)  # Should be 22 bytes
        
        channels = parse_rc_channels(payload)
        
        self.assertIsNone(channels)

    def test_empty_payload(self):
        """Test that empty payload returns None"""
        payload = bytes()
        
        channels = parse_rc_channels(payload)
        
        self.assertIsNone(channels)


class TestCRSFFrameDecoder(unittest.TestCase):
    """Test suite for CRSF frame decoder"""

    def setUp(self):
        """Set up test fixtures"""
        self.decoder = CRSFFrameDecoder()

    def test_decode_valid_link_stats_frame(self):
        """Test decoding a valid link statistics frame"""
        # Valid link stats frame: address=0xEA, len=12, type=0x14, 10 bytes payload, CRC
        payload = bytes([100, 105, 95, 8, 0, 2, 20, 110, 98, 10])
        
        # Build frame manually
        frame_without_crc = bytes([0xEA, 0x0C, 0x14]) + payload
        crc = crsf_crc8(frame_without_crc[2:])  # CRC over type + payload
        frame = frame_without_crc + bytes([crc])
        
        result = None
        for byte in frame:
            decoded = self.decoder.push_byte(byte)
            if decoded:
                result = decoded
        
        self.assertIsNotNone(result)
        self.assertEqual(result['address'], 0xEA)
        self.assertEqual(result['type'], CRSF_FRAMETYPE_LINK_STATISTICS)
        self.assertEqual(len(result['payload']), 10)
        self.assertTrue(result['valid'])
        self.assertEqual(result['payload'], payload)

    def test_invalid_crc_detected(self):
        """Test that invalid CRC is detected"""
        # Frame with intentionally wrong CRC (correct CRC would be 0x97)
        frame = bytes([
            0xEE, 0x03, 0x16,  # Address, Length=3 (type + payload + CRC), Type
            0xAA,              # Single payload byte
            0xFF               # Wrong CRC (intentionally incorrect)
        ])
        
        result = None
        for byte in frame:
            decoded = self.decoder.push_byte(byte)
            if decoded:
                result = decoded
        
        self.assertIsNotNone(result)
        self.assertFalse(result['valid'])

    def test_invalid_address_ignored(self):
        """Test that invalid address bytes are ignored"""
        # Send invalid address bytes
        invalid_bytes = bytes([0x01, 0x02, 0x03, 0x99])
        
        for byte in invalid_bytes:
            result = self.decoder.push_byte(byte)
            self.assertIsNone(result)
        
        # Decoder should still be in WAIT_ADDRESS state
        self.assertEqual(self.decoder.state, 'WAIT_ADDRESS')

    def test_length_too_large_resets(self):
        """Test that length > 64 causes reset"""
        # Valid address, but length too large
        self.decoder.push_byte(0xEE)  # Valid address
        result = self.decoder.push_byte(0xFF)  # Length 255 (> 64)
        
        self.assertIsNone(result)
        # Should have reset
        self.assertEqual(self.decoder.state, 'WAIT_ADDRESS')

    def test_length_too_small_resets(self):
        """Test that length < 2 causes reset"""
        # Valid address, but length too small
        self.decoder.push_byte(0xEE)  # Valid address
        result = self.decoder.push_byte(0x01)  # Length 1 (< 2)
        
        self.assertIsNone(result)
        # Should have reset
        self.assertEqual(self.decoder.state, 'WAIT_ADDRESS')

    def test_multiple_frames_in_sequence(self):
        """Test decoding multiple frames back-to-back"""
        # Two simple frames
        frame1_payload = bytes([0xAA])
        frame1_without_crc = bytes([0xEE, 0x03, 0x14]) + frame1_payload
        crc1 = crsf_crc8(frame1_without_crc[2:])
        frame1 = frame1_without_crc + bytes([crc1])
        
        frame2_payload = bytes([0xBB])
        frame2_without_crc = bytes([0xEA, 0x03, 0x14]) + frame2_payload
        crc2 = crsf_crc8(frame2_without_crc[2:])
        frame2 = frame2_without_crc + bytes([crc2])
        
        frames = frame1 + frame2
        
        decoded_frames = []
        for byte in frames:
            result = self.decoder.push_byte(byte)
            if result:
                decoded_frames.append(result)
        
        self.assertEqual(len(decoded_frames), 2)
        self.assertEqual(decoded_frames[0]['payload'], frame1_payload)
        self.assertEqual(decoded_frames[1]['payload'], frame2_payload)

    def test_decoder_reset(self):
        """Test manual decoder reset"""
        # Start decoding a frame
        self.decoder.push_byte(0xEE)  # Address
        self.decoder.push_byte(0x03)  # Length
        
        # Now reset
        self.decoder.reset()
        
        self.assertEqual(self.decoder.state, 'WAIT_ADDRESS')
        self.assertEqual(len(self.decoder.buffer), 0)
        self.assertEqual(self.decoder.expected_length, 0)

    def test_all_valid_address_bytes_accepted(self):
        """Test that all valid address bytes are accepted"""
        valid_addresses = [0x00, 0xC8, 0xEA, 0xEC, 0xEE, 0xEF]
        
        for addr in valid_addresses:
            decoder = CRSFFrameDecoder()
            decoder.push_byte(addr)
            self.assertEqual(decoder.state, 'WAIT_LENGTH')
            self.assertEqual(decoder.buffer[0], addr)

    def test_partial_frame_then_valid(self):
        """Test that decoder recovers after partial invalid frame"""
        # Send partial invalid frame
        self.decoder.push_byte(0xEE)
        self.decoder.push_byte(0xFF)  # Invalid length, will reset
        
        # Now send valid frame
        payload = bytes([0xAA])
        frame_without_crc = bytes([0xEE, 0x03, 0x14]) + payload
        crc = crsf_crc8(frame_without_crc[2:])
        frame = frame_without_crc + bytes([crc])
        
        result = None
        for byte in frame:
            decoded = self.decoder.push_byte(byte)
            if decoded:
                result = decoded
        
        self.assertIsNotNone(result)
        self.assertTrue(result['valid'])


class TestChannelClamp(unittest.TestCase):
    """Test suite for channel clamping at the new 172/1811 rails."""

    def test_clamp_at_min_172(self):
        """Input below the formula rail is clamped to CRSF_CHANNEL_MIN (172)."""
        # 900µs → formula gives ((900-1500)*8/5+992) = 32, which is < 172
        result = convert_channel_1000_2000_to_crsf(900)
        self.assertEqual(result, CRSF_CHANNEL_MIN)  # 172

    def test_clamp_at_max_1811(self):
        """Input above the formula rail is clamped to CRSF_CHANNEL_MAX (1811)."""
        # 2100µs → formula gives ((2100-1500)*8/5+992) = 1952, which is > 1811
        result = convert_channel_1000_2000_to_crsf(2100)
        self.assertEqual(result, CRSF_CHANNEL_MAX)  # 1811

    def test_1000us_in_range(self):
        """1000µs maps to 192, which is within the new [172, 1811] range."""
        result = convert_channel_1000_2000_to_crsf(1000)
        self.assertEqual(result, 192)
        self.assertGreaterEqual(result, CRSF_CHANNEL_MIN)

    def test_2000us_in_range(self):
        """2000µs maps to 1792, which is within the new [172, 1811] range."""
        result = convert_channel_1000_2000_to_crsf(2000)
        self.assertEqual(result, 1792)
        self.assertLessEqual(result, CRSF_CHANNEL_MAX)


class TestParseHandsetTiming(unittest.TestCase):
    """Test suite for parse_handset_timing."""

    def _build_payload(self, rate_tenth_us: int, offset_tenth_us: int) -> bytes:
        return bytes([CRSF_SUBCMD_TIMING]) + \
               struct.pack('>I', rate_tenth_us) + \
               struct.pack('>i', offset_tenth_us)

    def test_valid_positive_offset(self):
        """Parse a valid timing frame with a positive offset."""
        rate_tenth_us = 40000    # 4000µs = 250 Hz
        offset_tenth_us = 5000   # +500µs

        payload = self._build_payload(rate_tenth_us, offset_tenth_us)
        result = parse_handset_timing(payload)

        self.assertIsNotNone(result)
        rate_us, offset_us = result
        self.assertAlmostEqual(rate_us, 4000.0, places=6)
        self.assertAlmostEqual(offset_us, 500.0, places=6)

    def test_valid_negative_offset(self):
        """Parse a valid timing frame with a negative offset."""
        rate_tenth_us = 20000    # 2000µs = 500 Hz
        offset_tenth_us = -3000  # -300µs

        payload = self._build_payload(rate_tenth_us, offset_tenth_us)
        result = parse_handset_timing(payload)

        self.assertIsNotNone(result)
        rate_us, offset_us = result
        self.assertAlmostEqual(rate_us, 2000.0, places=6)
        self.assertAlmostEqual(offset_us, -300.0, places=6)

    def test_wrong_subtype_returns_none(self):
        """Payload with wrong subType byte returns None."""
        payload = bytes([0x05]) + struct.pack('>I', 40000) + struct.pack('>i', 0)
        self.assertIsNone(parse_handset_timing(payload))

    def test_short_payload_returns_none(self):
        """Payload shorter than 9 bytes returns None."""
        payload = bytes([CRSF_SUBCMD_TIMING, 0x00, 0x00])  # Only 3 bytes
        self.assertIsNone(parse_handset_timing(payload))

    def test_empty_payload_returns_none(self):
        """Empty payload returns None."""
        self.assertIsNone(parse_handset_timing(b''))

    def test_zero_offset(self):
        """Zero offset is parsed correctly."""
        payload = self._build_payload(10000, 0)  # 1000µs = 1kHz, no offset
        result = parse_handset_timing(payload)
        self.assertIsNotNone(result)
        rate_us, offset_us = result
        self.assertAlmostEqual(rate_us, 1000.0, places=6)
        self.assertAlmostEqual(offset_us, 0.0, places=6)


if __name__ == '__main__':
    unittest.main()
