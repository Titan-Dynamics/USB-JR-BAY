"""
CRSF Protocol Module

This module contains all CRSF protocol constants, structures, and frame
serialization/deserialization functions. It is decoupled from UART operations
and fully testable.

Based on the EdgeTX/ELRS CRSF protocol implementation.
"""

import struct
from typing import List, Tuple, Optional


# =============================================================================
# Protocol Constants
# =============================================================================

# CRSF Addresses
CRSF_ADDRESS_BROADCAST = 0x00
CRSF_ADDRESS_RADIO = 0xEA
CRSF_ADDRESS_RADIO_TRANSMITTER = 0xEA  # alias for CRSF_ADDRESS_RADIO
CRSF_ADDRESS_MODULE = 0xEE
CRSF_ADDRESS_CRSF_TRANSMITTER = 0xEE   # alias for CRSF_ADDRESS_MODULE
CRSF_SYNC_BYTE = 0xC8
CRSF_ADDRESS_ELRS_LUA = 0xEF
CRSF_ADDRESS_RECEIVER = 0xEC

# CRSF Frame Types
CRSF_FRAMETYPE_LINK_STATISTICS = 0x14
CRSF_FRAMETYPE_RC_CHANNELS = 0x16
CRSF_FRAMETYPE_PING_DEVICES = 0x28
CRSF_FRAMETYPE_DEVICE_INFO = 0x29
CRSF_FRAMETYPE_REQUEST_SETTINGS = 0x2A
CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY = 0x2B
CRSF_FRAMETYPE_PARAMETER_READ = 0x2C
CRSF_FRAMETYPE_PARAMETER_WRITE = 0x2D
CRSF_FRAMETYPE_ELRS_STATUS = 0x2E
CRSF_FRAMETYPE_COMMAND = 0x32
CRSF_FRAMETYPE_RADIO_ID = 0x3A

# CRSF Subcommands
CRSF_SUBCMD_TIMING = 0x10
CRSF_SUBCMD_MODEL_SELECT = 0x05

# Frame Size Limits
CRSF_MAX_FRAME_SIZE = 64
CRSF_RC_CHANNELS_PACKED_LEN = 22
CRSF_RC_FRAME_LEN = 26

# Channel Constants (from CRSF spec)
CRSF_CHANNEL_CENTER = 992  # 1500µs
CRSF_CHANNEL_MIN = 172     # ELRS canonical 11-bit min (~988µs)
CRSF_CHANNEL_MAX = 1811    # ELRS canonical 11-bit max (~2012µs)

# =============================================================================
# Link Statistics Enum Lookup Tables
# =============================================================================

# CRSF uplink_tx_power index → display string
# Mapping from TitanLRS POWERMGNT.cpp powerToCrsfPower()
CRSF_TX_POWER_NAMES = {
    0: "0mW",
    1: "10mW",
    2: "25mW",
    3: "100mW",
    4: "500mW",
    5: "1000mW",
    6: "2000mW",
    7: "250mW",
    8: "50mW",
}

# expresslrs_RFrates_e → display string
# Labels match the LUA packet-rate strings in TitanLRS TXModuleParameters.cpp
# (STR_LUA_PACKETRATES), with band appended. Prefixes: D=DVDA, F=FLRC, K=FSK, DK=FSK DVDA.
# "Full" = 8-channel (OTA8) variant. Band suffixes added here for the link-stats display.
CRSF_RF_MODE_NAMES = {
    # 900 MHz (subGhz) — SX127X and LR1121 900
    0:   "25Hz subGhz",
    1:   "50Hz subGhz",
    2:   "100Hz subGhz",
    3:   "100Hz Full subGhz",
    4:   "150Hz subGhz",
    5:   "200Hz subGhz",
    6:   "200Hz Full subGhz",
    7:   "250Hz subGhz",
    8:   "333Hz Full subGhz",
    9:   "500Hz subGhz",
    10:  "D50Hz subGhz",
    11:  "K1000 Full subGhz",
    # 2.4 GHz — SX128X and LR1121 2G4
    20:  "25Hz 2.4GHz",
    21:  "50Hz 2.4GHz",
    22:  "100Hz 2.4GHz",
    23:  "100Hz Full 2.4GHz",
    24:  "150Hz 2.4GHz",
    25:  "200Hz 2.4GHz",
    26:  "200Hz Full 2.4GHz",
    27:  "250Hz 2.4GHz",
    28:  "333Hz Full 2.4GHz",
    29:  "500Hz 2.4GHz",
    30:  "D250 2.4GHz",
    31:  "D500 2.4GHz",
    32:  "F500 2.4GHz",
    33:  "F1000 2.4GHz",
    34:  "DK250 2.4GHz",
    35:  "DK500 2.4GHz",
    36:  "K1000 2.4GHz",
    # X-Band — dual-band LR1121 (900 + 2.4 GHz simultaneous)
    100: "X100Hz Full",
    101: "X150Hz",
}


def rf_mode_name(value: int) -> str:
    """Return a human-readable label for an expresslrs_RFrates_e value."""
    return CRSF_RF_MODE_NAMES.get(value, str(value))


def tx_power_name(value: int) -> str:
    """Return a human-readable label for a CRSF uplink_tx_power index."""
    return CRSF_TX_POWER_NAMES.get(value, str(value))


# =============================================================================
# CRC8 Lookup Table (Polynomial 0xD5)
# =============================================================================

_CRC8_LUT = bytes([
    0x00, 0xD5, 0x7F, 0xAA, 0xFE, 0x2B, 0x81, 0x54,
    0x29, 0xFC, 0x56, 0x83, 0xD7, 0x02, 0xA8, 0x7D,
    0x52, 0x87, 0x2D, 0xF8, 0xAC, 0x79, 0xD3, 0x06,
    0x7B, 0xAE, 0x04, 0xD1, 0x85, 0x50, 0xFA, 0x2F,
    0xA4, 0x71, 0xDB, 0x0E, 0x5A, 0x8F, 0x25, 0xF0,
    0x8D, 0x58, 0xF2, 0x27, 0x73, 0xA6, 0x0C, 0xD9,
    0xF6, 0x23, 0x89, 0x5C, 0x08, 0xDD, 0x77, 0xA2,
    0xDF, 0x0A, 0xA0, 0x75, 0x21, 0xF4, 0x5E, 0x8B,
    0x9D, 0x48, 0xE2, 0x37, 0x63, 0xB6, 0x1C, 0xC9,
    0xB4, 0x61, 0xCB, 0x1E, 0x4A, 0x9F, 0x35, 0xE0,
    0xCF, 0x1A, 0xB0, 0x65, 0x31, 0xE4, 0x4E, 0x9B,
    0xE6, 0x33, 0x99, 0x4C, 0x18, 0xCD, 0x67, 0xB2,
    0x39, 0xEC, 0x46, 0x93, 0xC7, 0x12, 0xB8, 0x6D,
    0x10, 0xC5, 0x6F, 0xBA, 0xEE, 0x3B, 0x91, 0x44,
    0x6B, 0xBE, 0x14, 0xC1, 0x95, 0x40, 0xEA, 0x3F,
    0x42, 0x97, 0x3D, 0xE8, 0xBC, 0x69, 0xC3, 0x16,
    0xEF, 0x3A, 0x90, 0x45, 0x11, 0xC4, 0x6E, 0xBB,
    0xC6, 0x13, 0xB9, 0x6C, 0x38, 0xED, 0x47, 0x92,
    0xBD, 0x68, 0xC2, 0x17, 0x43, 0x96, 0x3C, 0xE9,
    0x94, 0x41, 0xEB, 0x3E, 0x6A, 0xBF, 0x15, 0xC0,
    0x4B, 0x9E, 0x34, 0xE1, 0xB5, 0x60, 0xCA, 0x1F,
    0x62, 0xB7, 0x1D, 0xC8, 0x9C, 0x49, 0xE3, 0x36,
    0x19, 0xCC, 0x66, 0xB3, 0xE7, 0x32, 0x98, 0x4D,
    0x30, 0xE5, 0x4F, 0x9A, 0xCE, 0x1B, 0xB1, 0x64,
    0x72, 0xA7, 0x0D, 0xD8, 0x8C, 0x59, 0xF3, 0x26,
    0x5B, 0x8E, 0x24, 0xF1, 0xA5, 0x70, 0xDA, 0x0F,
    0x20, 0xF5, 0x5F, 0x8A, 0xDE, 0x0B, 0xA1, 0x74,
    0x09, 0xDC, 0x76, 0xA3, 0xF7, 0x22, 0x88, 0x5D,
    0xD6, 0x03, 0xA9, 0x7C, 0x28, 0xFD, 0x57, 0x82,
    0xFF, 0x2A, 0x80, 0x55, 0x01, 0xD4, 0x7E, 0xAB,
    0x84, 0x51, 0xFB, 0x2E, 0x7A, 0xAF, 0x05, 0xD0,
    0xAD, 0x78, 0xD2, 0x07, 0x53, 0x86, 0x2C, 0xF9
])


# =============================================================================
# CRC Functions
# =============================================================================

def crsf_crc8(data: bytes) -> int:
    """
    Calculate CRC8 with polynomial 0xD5.

    Args:
        data: Bytes to calculate CRC over

    Returns:
        CRC8 value (0-255)
    """
    crc = 0
    for byte in data:
        crc = _CRC8_LUT[crc ^ byte]
    return crc


def validate_crc(frame: bytes) -> bool:
    """
    Validate the CRC of a CRSF frame.

    Args:
        frame: Complete CRSF frame including CRC

    Returns:
        True if CRC is valid, False otherwise
    """
    if len(frame) < 3:
        return False
    
    # CRC is over type + payload (exclude address, length, and CRC itself)
    payload_len = frame[1]
    if len(frame) < payload_len + 2:
        return False
    
    expected_crc = crsf_crc8(frame[2:2 + payload_len - 1])
    actual_crc = frame[2 + payload_len - 1]
    
    return expected_crc == actual_crc


# =============================================================================
# Channel Packing/Unpacking (11-bit per channel)
# =============================================================================

def pack_channels(channels: List[int]) -> bytes:
    """
    Pack 16 RC channel values (11-bit each) into 22 bytes.

    Args:
        channels: List of 16 channel values in CRSF ticks (192-1792)

    Returns:
        22 bytes of packed channel data

    Raises:
        ValueError: If channels list is not exactly 16 values
    """
    if len(channels) != 16:
        raise ValueError(f"Expected 16 channels, got {len(channels)}")

    # Clamp values to valid CRSF range (11-bit allows 0-2047, but valid range is 192-1792)
    ch = [max(CRSF_CHANNEL_MIN, min(CRSF_CHANNEL_MAX, c)) for c in channels]

    packed = bytearray(22)
    packed[0] = ch[0] & 0xFF
    packed[1] = ((ch[0] >> 8) | (ch[1] << 3)) & 0xFF
    packed[2] = ((ch[1] >> 5) | (ch[2] << 6)) & 0xFF
    packed[3] = (ch[2] >> 2) & 0xFF
    packed[4] = ((ch[2] >> 10) | (ch[3] << 1)) & 0xFF
    packed[5] = ((ch[3] >> 7) | (ch[4] << 4)) & 0xFF
    packed[6] = ((ch[4] >> 4) | (ch[5] << 7)) & 0xFF
    packed[7] = (ch[5] >> 1) & 0xFF
    packed[8] = ((ch[5] >> 9) | (ch[6] << 2)) & 0xFF
    packed[9] = ((ch[6] >> 6) | (ch[7] << 5)) & 0xFF
    packed[10] = (ch[7] >> 3) & 0xFF
    packed[11] = ch[8] & 0xFF
    packed[12] = ((ch[8] >> 8) | (ch[9] << 3)) & 0xFF
    packed[13] = ((ch[9] >> 5) | (ch[10] << 6)) & 0xFF
    packed[14] = (ch[10] >> 2) & 0xFF
    packed[15] = ((ch[10] >> 10) | (ch[11] << 1)) & 0xFF
    packed[16] = ((ch[11] >> 7) | (ch[12] << 4)) & 0xFF
    packed[17] = ((ch[12] >> 4) | (ch[13] << 7)) & 0xFF
    packed[18] = (ch[13] >> 1) & 0xFF
    packed[19] = ((ch[13] >> 9) | (ch[14] << 2)) & 0xFF
    packed[20] = ((ch[14] >> 6) | (ch[15] << 5)) & 0xFF
    packed[21] = (ch[15] >> 3) & 0xFF

    return bytes(packed)


def unpack_channels(packed: bytes) -> List[int]:
    """
    Unpack 22 bytes into 16 RC channel values (11-bit each).

    Args:
        packed: 22 bytes of packed channel data

    Returns:
        List of 16 channel values in CRSF ticks (typically 192-1792)

    Raises:
        ValueError: If packed data is not exactly 22 bytes
    """
    if len(packed) != 22:
        raise ValueError(f"Expected 22 bytes, got {len(packed)}")

    channels = [0] * 16
    channels[0] = (packed[0] | (packed[1] << 8)) & 0x07FF
    channels[1] = ((packed[1] >> 3) | (packed[2] << 5)) & 0x07FF
    channels[2] = ((packed[2] >> 6) | (packed[3] << 2) | (packed[4] << 10)) & 0x07FF
    channels[3] = ((packed[4] >> 1) | (packed[5] << 7)) & 0x07FF
    channels[4] = ((packed[5] >> 4) | (packed[6] << 4)) & 0x07FF
    channels[5] = ((packed[6] >> 7) | (packed[7] << 1) | (packed[8] << 9)) & 0x07FF
    channels[6] = ((packed[8] >> 2) | (packed[9] << 6)) & 0x07FF
    channels[7] = ((packed[9] >> 5) | (packed[10] << 3)) & 0x07FF
    channels[8] = (packed[11] | (packed[12] << 8)) & 0x07FF
    channels[9] = ((packed[12] >> 3) | (packed[13] << 5)) & 0x07FF
    channels[10] = ((packed[13] >> 6) | (packed[14] << 2) | (packed[15] << 10)) & 0x07FF
    channels[11] = ((packed[15] >> 1) | (packed[16] << 7)) & 0x07FF
    channels[12] = ((packed[16] >> 4) | (packed[17] << 4)) & 0x07FF
    channels[13] = ((packed[17] >> 7) | (packed[18] << 1) | (packed[19] << 9)) & 0x07FF
    channels[14] = ((packed[19] >> 2) | (packed[20] << 6)) & 0x07FF
    channels[15] = ((packed[20] >> 5) | (packed[21] << 3)) & 0x07FF

    return channels


# =============================================================================
# Channel Value Conversion
# =============================================================================

def convert_channel_1000_2000_to_crsf(value: int) -> int:
    """
    Convert channel value from 1000-2000µs range to CRSF ticks.

    Per CRSF spec: US_TO_TICKS(x) = ((x - 1500) * 8 / 5 + 992)

    Args:
        value: Channel value in 1000-2000µs range

    Returns:
        Channel value in CRSF ticks (192-1792)
    """
    # CRSF spec formula: ((µs - 1500) * 8 / 5 + 992)
    crsf_value = int(((value - 1500) * 8 / 5) + 992)
    return max(CRSF_CHANNEL_MIN, min(CRSF_CHANNEL_MAX, crsf_value))


def convert_channel_crsf_to_1000_2000(value: int) -> int:
    """
    Convert channel value from CRSF ticks to 1000-2000µs range.

    Per CRSF spec: TICKS_TO_US(x) = ((x - 992) * 5 / 8 + 1500)

    Args:
        value: Channel value in CRSF ticks

    Returns:
        Channel value in 1000-2000µs range
    """
    # CRSF spec formula: ((ticks - 992) * 5 / 8 + 1500)
    us_value = int(((value - 992) * 5 / 8) + 1500)
    return max(1000, min(2000, us_value))


# =============================================================================
# Frame Building Functions
# =============================================================================

def build_rc_frame(channels: List[int]) -> bytes:
    """
    Build a complete RC channels frame.

    Args:
        channels: List of 16 channel values in CRSF ticks (192-1792)

    Returns:
        Complete 26-byte RC frame with CRC

    Raises:
        ValueError: If channels list is not exactly 16 values
    """
    if len(channels) != 16:
        raise ValueError(f"Expected 16 channels, got {len(channels)}")

    frame = bytearray(26)
    frame[0] = CRSF_SYNC_BYTE  # 0xC8: matches what USBHandset::forwardMessage writes
    frame[1] = CRSF_RC_CHANNELS_PACKED_LEN + 2  # Length: Type + Payload + CRC
    frame[2] = CRSF_FRAMETYPE_RC_CHANNELS  # Type: RC Channels (0x16)

    # Pack channel data
    packed = pack_channels(channels)
    frame[3:25] = packed

    # Calculate and append CRC
    frame[25] = crsf_crc8(frame[2:25])

    return bytes(frame)


def build_ping_frame(target_addr: int = CRSF_ADDRESS_BROADCAST,
                     origin_addr: int = CRSF_ADDRESS_RADIO) -> bytes:
    """
    Build a device ping frame for discovery.

    Args:
        target_addr: Destination — 0x00 (broadcast) for scan, or a specific
            device address (e.g. 0xEE) to direct the ping.
        origin_addr: Our address — 0xEA for scan / non-ELRS targets,
            0xEF (ADDR_ELRS_LUA) once we have identified an ELRS TX.

    Returns:
        Complete 6-byte ping frame with CRC
    """
    frame = bytearray(6)
    frame[0] = CRSF_SYNC_BYTE
    frame[1] = 4  # Length: Type + Payload (2 bytes) + CRC
    frame[2] = CRSF_FRAMETYPE_PING_DEVICES
    frame[3] = target_addr
    frame[4] = origin_addr
    frame[5] = crsf_crc8(frame[2:5])

    return bytes(frame)


def build_param_read(device_addr: int, origin_addr: int,
                     param_index: int, chunk_number: int = 0) -> bytes:
    """
    Build a CRSF_FRAMETYPE_PARAMETER_READ (0x2C) frame.

    Frame layout (extended):
        [SYNC, length, 0x2C, device_addr, origin_addr,
         param_index, chunk_number, CRC]

    origin_addr must be 0xEF (ADDR_ELRS_LUA) when the destination is an ELRS
    TX module, else 0xEA. The LUA state machine tracks this per-device based
    on serialNumber == 'ELRS'.
    """
    frame = bytearray(8)
    frame[0] = CRSF_SYNC_BYTE
    frame[1] = 6  # Length: Type + Dest + Origin + Payload (2) + CRC
    frame[2] = CRSF_FRAMETYPE_PARAMETER_READ
    frame[3] = device_addr
    frame[4] = origin_addr
    frame[5] = param_index
    frame[6] = chunk_number
    frame[7] = crsf_crc8(frame[2:7])

    return bytes(frame)


def build_param_request(device_addr: int, param_index: int, chunk_number: int = 0) -> bytes:
    """Deprecated alias for build_param_read with origin hardcoded to CRSF_ADDRESS_RADIO."""
    return build_param_read(device_addr, CRSF_ADDRESS_RADIO, param_index, chunk_number)


def build_param_write(device_addr: int, origin_addr: int,
                      param_index: int, value: int) -> bytes:
    """
    Build a CRSF_FRAMETYPE_PARAMETER_WRITE (0x2D) frame.

    Frame layout:
        [SYNC, length, 0x2D, device_addr, origin_addr,
         param_index, value, CRC]

    value is one byte:
        UINT8 / TEXT_SELECTION: the new numeric value / option index.
        INT8: caller passes signed; masked with 0xFF on wire.
        COMMAND: status byte (1=START, 4=CONFIRMED, 5=CANCEL, 6=POLL).

    Multi-byte writes (UINT16, INT16, FLOAT, STRING) are not implemented —
    ELRS firmware does not emit those types for user-facing parameters.
    """
    frame = bytearray(8)
    frame[0] = CRSF_SYNC_BYTE
    frame[1] = 6  # Length: Type + Dest + Origin + Payload (2) + CRC
    frame[2] = CRSF_FRAMETYPE_PARAMETER_WRITE
    frame[3] = device_addr
    frame[4] = origin_addr
    frame[5] = param_index
    frame[6] = value & 0xFF
    frame[7] = crsf_crc8(frame[2:7])

    return bytes(frame)


def parse_param_entry_header(payload: bytes) -> Optional[dict]:
    """
    Parse a CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY (0x2B) payload as it
    comes out of CRSFFrameDecoder (extended frame, so dest+orig are still
    at payload[0..1]).

    Layout:
        [0]    dst               (us; 0xEA or 0xEF)
        [1]    src               (the device; 0xEE for TX)
        [2]    field_id
        [3]    chunks_remaining
        [4:]   chunk_data        (one chunk of the field blob)

    Returns dict with dst, src, field_id, chunks_remaining, chunk_data,
    or None on too-short input.
    """
    if len(payload) < 4:
        return None
    return {
        'dst':              payload[0],
        'src':              payload[1],
        'field_id':         payload[2],
        'chunks_remaining': payload[3],
        'chunk_data':       bytes(payload[4:]),
    }


# =============================================================================
# Frame Parsing Functions
# =============================================================================

def parse_link_statistics(payload: bytes) -> Optional[dict]:
    """
    Parse a link statistics frame payload.

    Args:
        payload: Frame payload (without address, length, type, or CRC)

    Returns:
        Dictionary with link statistics or None if invalid
        Keys: uplink_rssi_1, uplink_rssi_2, uplink_link_quality, uplink_snr,
              active_antenna, rf_mode, uplink_tx_power, downlink_rssi,
              downlink_link_quality, downlink_snr
    """
    if len(payload) < 10:
        return None

    return {
        'uplink_rssi_1': struct.unpack('b', bytes([payload[0]]))[0],  # Signed dBm (already negative)
        'uplink_rssi_2': struct.unpack('b', bytes([payload[1]]))[0],  # Signed dBm (already negative)
        'uplink_link_quality': payload[2],  # 0-100%
        'uplink_snr': struct.unpack('b', bytes([payload[3]]))[0],  # Signed SNR in dB
        'active_antenna': payload[4],  # 0 or 1
        'rf_mode': payload[5],  # RF mode/rate
        'uplink_tx_power': payload[6],  # TX power in mW/index
        'downlink_rssi': struct.unpack('b', bytes([payload[7]]))[0],  # Signed dBm (already negative)
        'downlink_link_quality': payload[8],  # 0-100%
        'downlink_snr': struct.unpack('b', bytes([payload[9]]))[0],  # Signed SNR in dB
    }


def parse_rc_channels(payload: bytes) -> Optional[List[int]]:
    """
    Parse an RC channels frame payload.

    Args:
        payload: Frame payload (22 bytes of packed channel data)

    Returns:
        List of 16 channel values (0-1984) or None if invalid
    """
    if len(payload) != 22:
        return None

    return unpack_channels(payload)


def parse_handset_timing(payload: bytes) -> Optional[Tuple[float, float]]:
    """
    Parse the body of a CRSF_FRAMETYPE_RADIO_ID (0x3A) timing frame.

    Expects the payload with dest+orig already stripped. Call as:
        parse_handset_timing(frame['payload'][2:])

    Layout after stripping:
      [0]      subType (0x10 = CRSF_SUBCMD_TIMING)
      [1:5]    rate    big-endian uint32, 10ths of µs
      [5:9]    offset  big-endian  int32, 10ths of µs

    Returns (rate_us, offset_us) in microseconds, or None on mismatch.
    Positive offset means the packet arrived early — delay next send.
    """
    if len(payload) < 9:
        return None
    if payload[0] != CRSF_SUBCMD_TIMING:
        return None
    rate_tenth_us = struct.unpack('>I', payload[1:5])[0]
    offset_tenth_us = struct.unpack('>i', payload[5:9])[0]
    return (rate_tenth_us / 10.0, offset_tenth_us / 10.0)


# =============================================================================
# Frame Decoder (Stateful byte-by-byte parser)
# =============================================================================

class CRSFFrameDecoder:
    """
    Stateful CRSF frame decoder that processes bytes one at a time.
    
    Usage:
        decoder = CRSFFrameDecoder()
        for byte in serial_data:
            frame = decoder.push_byte(byte)
            if frame is not None:
                # Process complete frame
                print(f"Frame type: {frame['type']}")
    """
    
    def __init__(self):
        """Initialize the decoder state machine."""
        self.reset()
    
    def reset(self):
        """Reset decoder to initial state."""
        self.state = 'WAIT_ADDRESS'
        self.buffer = bytearray()
        self.expected_length = 0
    
    def push_byte(self, byte: int) -> Optional[dict]:
        """
        Push a single byte into the decoder.
        
        Args:
            byte: Single byte (0-255)
        
        Returns:
            Dictionary with decoded frame if complete, None otherwise.
            Frame dict contains:
                - 'address': Destination address
                - 'length': Payload length (type + payload + CRC)
                - 'type': Frame type
                - 'payload': Payload bytes (without type and CRC)
                - 'crc': CRC byte
                - 'valid': True if CRC is valid
                - 'raw': Complete raw frame bytes
        """
        if self.state == 'WAIT_ADDRESS':
            # Looking for a valid address byte
            # Valid addresses: 0x00, 0xC8, 0xEA, 0xEC, 0xEE, 0xEF
            if byte in (0x00, 0xC8, 0xEA, 0xEC, 0xEE, 0xEF):
                self.buffer = bytearray([byte])
                self.state = 'WAIT_LENGTH'
            return None
        
        elif self.state == 'WAIT_LENGTH':
            # Length byte (type + payload + CRC)
            self.buffer.append(byte)
            self.expected_length = byte
            
            # Sanity check: length must be at least 2 (type + CRC) and < max frame size
            if self.expected_length < 2 or self.expected_length > CRSF_MAX_FRAME_SIZE:
                self.reset()
                return None
            
            self.state = 'READ_FRAME'
            return None
        
        elif self.state == 'READ_FRAME':
            # Accumulate frame bytes
            self.buffer.append(byte)
            
            # Check if we have the complete frame (address + length + expected_length bytes)
            if len(self.buffer) >= 2 + self.expected_length:
                # Frame complete - parse and validate
                frame = self._parse_complete_frame()
                self.reset()
                return frame
            
            return None
        
        # Should never reach here
        self.reset()
        return None
    
    def _parse_complete_frame(self) -> Optional[dict]:
        """
        Parse a complete frame from the buffer.
        
        Returns:
            Frame dictionary or None if invalid
        """
        if len(self.buffer) < 4:  # Minimum: address + length + type + CRC
            return None
        
        address = self.buffer[0]
        length = self.buffer[1]
        frame_type = self.buffer[2]
        
        # Extract payload (everything between type and CRC)
        payload_start = 3
        payload_end = 2 + length - 1  # -1 because CRC is last byte
        crc_pos = 2 + length - 1
        
        if crc_pos >= len(self.buffer):
            return None
        
        payload = bytes(self.buffer[payload_start:payload_end])
        crc = self.buffer[crc_pos]
        
        # Validate CRC (over type + payload)
        expected_crc = crsf_crc8(self.buffer[2:crc_pos])
        crc_valid = (expected_crc == crc)
        
        return {
            'address': address,
            'length': length,
            'type': frame_type,
            'payload': payload,
            'crc': crc,
            'valid': crc_valid,
            'raw': bytes(self.buffer)
        }
