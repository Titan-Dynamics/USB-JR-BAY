"""
CRSF Protocol Implementation

This module contains all CRSF protocol-related functionality including:
- Protocol constants and addresses
- CRC8 calculation
- Frame building and packing
- Channel value conversion
"""

# CRSF Protocol Constants
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
CRSF_ADDRESS_RADIO = 0xEA
CRSF_ADDRESS_ELRS_LUA = 0xEF


def crc8_d5(data: bytes) -> int:
    """Calculate CRC8 with polynomial 0xD5 for CRSF frames."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0xD5) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def us_to_crsf_val(us: int) -> int:
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


def crsf_val_to_us(val: int) -> int:
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

    Args:
        ch16: List of 16 channel values in microseconds (1000-2000)

    Returns:
        Full CRSF frame: [addr][len][type][payload...][crc]
    """
    # Pack channels as 11-bit values into 22 bytes (LSB-first)
    bits = 0
    bit_pos = 0
    out = bytearray()
    for v in ch16:
        # Convert microsecond pulses to CRSF 11-bit units
        vv = us_to_crsf_val(int(v))
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

    Args:
        ftype: Frame type byte
        payload: Frame payload bytes

    Returns:
        Full frame: [sync=0xC8][len][type][payload...][crc]
        len is type+payload+crc (minimum 1+0+1 = 2)
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
