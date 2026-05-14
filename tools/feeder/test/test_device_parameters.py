"""
Unit tests for device_parameters — one test per type per the web table.
All tests use hand-crafted byte blobs matching what ELRS firmware sends.
"""

import struct
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from device_parameters import (
    parse_parameter,
    parse_device_info,
    PARAM_TYPE_UINT8,
    PARAM_TYPE_INT8,
    PARAM_TYPE_TEXT_SELECTION,
    PARAM_TYPE_FOLDER,
    PARAM_TYPE_INFO,
    PARAM_TYPE_COMMAND,
    PARAM_TYPE_STRING,
    PARAM_HIDDEN_BIT,
    PARAM_TYPE_MASK,
    ELRS_SERIAL_NUMBER,
)


def _uint8_blob(parent, type_byte, name, value, min_, max_, default, unit=''):
    """Build a minimal UINT8 or INT8 blob."""
    return (
        bytes([parent, type_byte])
        + name.encode() + b'\x00'
        + bytes([value & 0xFF, min_ & 0xFF, max_ & 0xFF, default & 0xFF])
        + unit.encode() + b'\x00'
    )


class TestParseUint8(unittest.TestCase):
    def test_parse_uint8_field(self):
        blob = _uint8_blob(0, PARAM_TYPE_UINT8, 'TX Power', 10, 0, 100, 10, 'mW')
        p = parse_parameter(3, blob)
        self.assertIsNotNone(p)
        self.assertEqual(p['number'],       3)
        self.assertEqual(p['parentFolder'], 0)
        self.assertEqual(p['type'],         PARAM_TYPE_UINT8)
        self.assertFalse(p['hidden'])
        self.assertEqual(p['name'],         'TX Power')
        self.assertEqual(p['value'],        10)
        self.assertEqual(p['min'],          0)
        self.assertEqual(p['max'],          100)
        self.assertEqual(p['defaultValue'], 10)
        self.assertEqual(p['unit'],         'mW')
        self.assertIsNone(p['options'])


class TestParseInt8(unittest.TestCase):
    def test_parse_int8_field(self):
        # -5 as two's complement byte = 0xFB
        val   = struct.pack('b', -5)[0]
        min_  = struct.pack('b', -100)[0]
        max_  = struct.pack('b', 100)[0]
        dflt  = struct.pack('b', 0)[0]
        blob = (
            bytes([0, PARAM_TYPE_INT8])
            + b'Offset\x00'
            + bytes([val, min_, max_, dflt])
            + b'\x00'
        )
        p = parse_parameter(7, blob)
        self.assertIsNotNone(p)
        self.assertEqual(p['type'],  PARAM_TYPE_INT8)
        self.assertEqual(p['value'], -5)
        self.assertEqual(p['min'],   -100)
        self.assertEqual(p['max'],   100)
        self.assertEqual(p['defaultValue'], 0)

    def test_signedness_of_0xFB(self):
        """Regression: 0xFB must be -5 (signed), not 251 (unsigned)."""
        blob = bytes([0, PARAM_TYPE_INT8]) + b'X\x00' + bytes([0xFB, 0x80, 0x7F, 0x00]) + b'\x00'
        p = parse_parameter(1, blob)
        self.assertEqual(p['value'], -5)
        self.assertEqual(p['min'],   -128)
        self.assertEqual(p['max'],   127)


class TestParseTextSelection(unittest.TestCase):
    def test_parse_text_selection_field(self):
        # Options with an empty slot at index 2
        opts = b'50Hz;100Hz;;250Hz\x00'
        blob = bytes([0, PARAM_TYPE_TEXT_SELECTION]) + b'Packet Rate\x00' + opts + bytes([1, 0, 3, 0]) + b'\x00'
        p = parse_parameter(1, blob)
        self.assertIsNotNone(p)
        self.assertEqual(p['type'], PARAM_TYPE_TEXT_SELECTION)
        # Indices are preserved including empty slot
        self.assertEqual(p['options'], ['50Hz', '100Hz', '', '250Hz'])
        self.assertEqual(p['value'],   1)
        self.assertEqual(p['max'],     3)

    def test_empty_option_index_preserved(self):
        """Empty option slots are preserved in the list (UI skips them at render time)."""
        opts = b'A;;C\x00'
        blob = bytes([0, PARAM_TYPE_TEXT_SELECTION]) + b'X\x00' + opts + bytes([2, 0, 2, 0]) + b'\x00'
        p = parse_parameter(1, blob)
        self.assertEqual(p['options'], ['A', '', 'C'])


class TestParseFolder(unittest.TestCase):
    def test_parse_folder_field(self):
        blob = bytes([0, PARAM_TYPE_FOLDER]) + b'WiFi\x00'
        p = parse_parameter(5, blob)
        self.assertIsNotNone(p)
        self.assertEqual(p['type'],    PARAM_TYPE_FOLDER)
        self.assertEqual(p['name'],    'WiFi')
        self.assertIsNone(p['value'])
        self.assertIsNone(p['options'])
        self.assertIsNone(p['min'])


class TestParseInfo(unittest.TestCase):
    def test_parse_info_field(self):
        blob = bytes([0, PARAM_TYPE_INFO]) + b'Firmware Version\x00' + b'v1.2.3\x00'
        p = parse_parameter(2, blob)
        self.assertIsNotNone(p)
        self.assertEqual(p['type'],  PARAM_TYPE_INFO)
        self.assertEqual(p['value'], 'v1.2.3')


class TestParseCommand(unittest.TestCase):
    def test_parse_command_field(self):
        blob = bytes([0, PARAM_TYPE_COMMAND]) + b'Bind\x00' + bytes([0, 50]) + b'Bind\x00'
        p = parse_parameter(9, blob)
        self.assertIsNotNone(p)
        self.assertEqual(p['type'],    PARAM_TYPE_COMMAND)
        self.assertEqual(p['status'],  0)
        self.assertEqual(p['timeout'], 50)
        self.assertEqual(p['value'],   'Bind')


class TestParseString(unittest.TestCase):
    def test_parse_string_field(self):
        blob = bytes([0, PARAM_TYPE_STRING]) + b'TX Name\x00' + b'MyTX\x00'
        p = parse_parameter(4, blob)
        self.assertIsNotNone(p)
        self.assertEqual(p['type'],  PARAM_TYPE_STRING)
        self.assertEqual(p['value'], 'MyTX')


class TestHiddenFlag(unittest.TestCase):
    def test_hidden_bit_detected(self):
        type_byte = PARAM_TYPE_TEXT_SELECTION | PARAM_HIDDEN_BIT
        opts = b'A;B\x00'
        blob = bytes([0, type_byte]) + b'HiddenParam\x00' + opts + bytes([0, 0, 1, 0]) + b'\x00'
        p = parse_parameter(1, blob)
        self.assertIsNotNone(p)
        self.assertTrue(p['hidden'])
        # type should use PARAM_TYPE_MASK (0x3F), NOT 0x7F
        self.assertEqual(p['type'], PARAM_TYPE_TEXT_SELECTION)

    def test_type_mask_is_0x3F_not_0x7F(self):
        """Regression test: mask must be 0x3F. 0x49 & 0x3F = 9 (TEXT_SELECTION)."""
        type_byte = 0x49  # bit 6 set (reserved) + TEXT_SELECTION
        opts = b'X;Y\x00'
        blob = bytes([0, type_byte]) + b'P\x00' + opts + bytes([0, 0, 1, 0]) + b'\x00'
        p = parse_parameter(1, blob)
        self.assertIsNotNone(p)
        # With 0x3F mask: 0x49 & 0x3F = 0x09 = TEXT_SELECTION
        self.assertEqual(p['type'], PARAM_TYPE_TEXT_SELECTION)
        # With wrong 0x7F mask: 0x49 & 0x7F = 0x49 = 73 (wrong)
        self.assertNotEqual(p['type'], 0x49)


class TestParseUnknownType(unittest.TestCase):
    def test_unknown_type_returns_dict_not_none(self):
        """Unknown types must not crash — return dict with value=None."""
        blob = bytes([0, 0x42]) + b'Mystery\x00'
        p = parse_parameter(1, blob)
        self.assertIsNotNone(p)
        self.assertEqual(p['type'], 0x02)  # 0x42 & 0x3F = 0x02
        self.assertIsNone(p['value'])

    def test_truly_unknown_type(self):
        blob = bytes([0, 0x3F]) + b'X\x00'
        p = parse_parameter(1, blob)
        self.assertIsNotNone(p)
        self.assertIsNone(p['value'])


class TestParseTooShort(unittest.TestCase):
    def test_empty_returns_none(self):
        self.assertIsNone(parse_parameter(1, b''))

    def test_one_byte_returns_none(self):
        self.assertIsNone(parse_parameter(1, b'\x00'))

    def test_two_bytes_returns_none(self):
        self.assertIsNone(parse_parameter(1, b'\x00\x00'))

    def test_three_bytes_parses_minimal(self):
        # 3 bytes = parent, type, NUL name — should not crash
        blob = bytes([0, PARAM_TYPE_FOLDER, 0])
        p = parse_parameter(1, blob)
        self.assertIsNotNone(p)


class TestParseDeviceInfo(unittest.TestCase):
    def _build_device_info(self, name, serial_number, n_params=10):
        payload = (
            name.encode() + b'\x00'
            + struct.pack('>I', serial_number)
            + struct.pack('>I', 0x00000001)  # hardwareId
            + struct.pack('>I', 0x00000002)  # firmwareId
            + bytes([n_params, 0])           # parametersTotal, parameterVersion
        )
        return payload

    def test_parse_device_info_elrs(self):
        payload = self._build_device_info('TitanLRS TX', ELRS_SERIAL_NUMBER, 47)
        d = parse_device_info(payload)
        self.assertIsNotNone(d)
        self.assertEqual(d['name'],            'TitanLRS TX')
        self.assertEqual(d['serialNumber'],    ELRS_SERIAL_NUMBER)
        self.assertTrue(d['isElrs'])
        self.assertEqual(d['parametersTotal'], 47)

    def test_parse_device_info_non_elrs(self):
        payload = self._build_device_info('SomeRX', 0xDEADBEEF, 12)
        d = parse_device_info(payload)
        self.assertIsNotNone(d)
        self.assertFalse(d['isElrs'])
        self.assertEqual(d['serialNumber'], 0xDEADBEEF)
        self.assertEqual(d['parametersTotal'], 12)

    def test_parse_device_info_truncated(self):
        self.assertIsNone(parse_device_info(b''))
        self.assertIsNone(parse_device_info(b'TX\x00\x45\x4C'))  # too short

    def test_elrs_serial_number_is_ascii_ELRS(self):
        """ELRS_SERIAL_NUMBER must be 0x454C5253 = ASCII 'ELRS'."""
        self.assertEqual(ELRS_SERIAL_NUMBER, 0x454C5253)
        self.assertEqual(struct.pack('>I', ELRS_SERIAL_NUMBER), b'ELRS')


if __name__ == '__main__':
    unittest.main()
