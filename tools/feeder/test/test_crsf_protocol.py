"""
Unit tests for CRSF Protocol

Tests cover:
- Link statistics parsing
- RC channels parsing
- Frame decoder (byte-by-byte decoding)
- CRC validation
- Edge cases and error handling
"""

import unittest
from crsf_protocol import (
    parse_link_statistics, parse_rc_channels,
    CRSFFrameDecoder, crsf_crc8,
    CRSF_FRAMETYPE_LINK_STATISTICS,
    CRSF_FRAMETYPE_RC_CHANNELS
)


class TestLinkStatisticsParsing(unittest.TestCase):
    """Test suite for link statistics parsing"""

    def test_valid_link_statistics(self):
        """Test parsing valid link statistics payload"""
        # Example link stats payload: 10 bytes
        # RSSI1=100, RSSI2=105, LQ=95, SNR=8, Antenna=0, RFMode=2, TxPwr=20, DRSSI=110, DLQ=98, DSNR=10
        payload = bytes([100, 105, 95, 8, 0, 2, 20, 110, 98, 10])
        
        stats = parse_link_statistics(payload)
        
        self.assertIsNotNone(stats)
        self.assertEqual(stats['uplink_rssi_1'], 100)
        self.assertEqual(stats['uplink_rssi_2'], 105)
        self.assertEqual(stats['uplink_link_quality'], 95)
        self.assertEqual(stats['uplink_snr'], 8)
        self.assertEqual(stats['active_antenna'], 0)
        self.assertEqual(stats['rf_mode'], 2)
        self.assertEqual(stats['uplink_tx_power'], 20)
        self.assertEqual(stats['downlink_rssi'], 110)
        self.assertEqual(stats['downlink_link_quality'], 98)
        self.assertEqual(stats['downlink_snr'], 10)

    def test_negative_snr_values(self):
        """Test that signed SNR values are correctly parsed"""
        # SNR values as signed bytes: -5 (251 unsigned) and -10 (246 unsigned)
        payload = bytes([100, 105, 95, 251, 0, 2, 20, 110, 98, 246])
        
        stats = parse_link_statistics(payload)
        
        self.assertIsNotNone(stats)
        self.assertEqual(stats['uplink_snr'], -5)
        self.assertEqual(stats['downlink_snr'], -10)

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


if __name__ == '__main__':
    unittest.main()
