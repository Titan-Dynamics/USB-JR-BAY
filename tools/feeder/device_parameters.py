"""
ELRS Parameter Parsing — pure stdlib, no Qt, no serial.

Mirrors CrsfParams.parseParameter and handleDeviceInfo from scan.js.
All functions are pure and unit-testable without Qt or a serial port.
"""

import struct
from typing import Any, Dict, List, Optional, Tuple

# Parameter type codes — mirror scan.js:907-918 exactly.
PARAM_TYPE_UINT8          = 0x00
PARAM_TYPE_INT8           = 0x01
PARAM_TYPE_UINT16         = 0x02  # defined but not emitted by ELRS firmware
PARAM_TYPE_INT16          = 0x03  # defined but not emitted by ELRS firmware
PARAM_TYPE_FLOAT          = 0x08  # defined but not emitted by ELRS firmware
PARAM_TYPE_TEXT_SELECTION = 0x09
PARAM_TYPE_STRING         = 0x0A
PARAM_TYPE_FOLDER         = 0x0B
PARAM_TYPE_INFO           = 0x0C
PARAM_TYPE_COMMAND        = 0x0D
PARAM_HIDDEN_BIT          = 0x80
PARAM_TYPE_MASK           = 0x3F  # NOT 0x7F — bit 6 is reserved, bit 7 is hidden

ELRS_SERIAL_NUMBER = 0x454C5253  # ASCII 'ELRS' big-endian


def _read_cstr(data: bytes, offset: int) -> Tuple[str, int]:
    """Read NUL-terminated string from data starting at offset.

    Returns (decoded_string, next_offset) where next_offset skips past the NUL.
    If no NUL is found before the end of data, returns whatever was read and
    next_offset = len(data). Uses errors='replace' so corrupt bytes don't crash.

    Mirrors CRSF.readString (scan.js:980-988).
    """
    end = offset
    while end < len(data) and data[end] != 0:
        end += 1
    s = data[offset:end].decode('utf-8', errors='replace')
    return s, end + 1  # +1 to skip the NUL (or len+1 if no NUL found, harmless)


def parse_parameter(number: int, data: bytes) -> Optional[Dict[str, Any]]:
    """Parse a fully-assembled parameter blob into a dict.

    Pure port of CrsfParams.parseParameter (scan.js:1287-1365).
    Returns None if data is shorter than 3 bytes.

    The returned dict keys match the web object so the UI port can be a
    near-1:1 copy:

        number        int    parameter ID
        parentFolder  int    parent folder ID (0 = root)
        type          int    PARAM_TYPE_* (after masking with 0x3F)
        hidden        bool   (typeByte & 0x80) != 0
        name          str
        value         Any    type-dependent (see below)
        options       list   only for TEXT_SELECTION; None otherwise
        min           int    only for numeric/selection; None otherwise
        max           int    only for numeric/selection; None otherwise
        defaultValue  int    only for numeric/selection; None otherwise
        unit          str    default ''
        status        int    only for COMMAND; None otherwise
        timeout       int    only for COMMAND; None otherwise
    """
    if len(data) < 3:
        return None

    parent_folder = data[0]
    type_byte     = data[1]
    param_type    = type_byte & PARAM_TYPE_MASK
    hidden        = bool(type_byte & PARAM_HIDDEN_BIT)

    offset = 2
    name, offset = _read_cstr(data, offset)

    result: Dict[str, Any] = {
        'number':       number,
        'parentFolder': parent_folder,
        'type':         param_type,
        'hidden':       hidden,
        'name':         name,
        'value':        None,
        'options':      None,
        'min':          None,
        'max':          None,
        'defaultValue': None,
        'unit':         '',
        'status':       None,
        'timeout':      None,
    }

    if param_type == PARAM_TYPE_UINT8:
        if offset + 4 > len(data):
            return result
        result['value']        = data[offset]
        result['min']          = data[offset + 1]
        result['max']          = data[offset + 2]
        result['defaultValue'] = data[offset + 3]
        offset += 4
        unit, offset = _read_cstr(data, offset)
        result['unit'] = unit

    elif param_type == PARAM_TYPE_INT8:
        if offset + 4 > len(data):
            return result
        result['value']        = struct.unpack('b', bytes([data[offset]]))[0]
        result['min']          = struct.unpack('b', bytes([data[offset + 1]]))[0]
        result['max']          = struct.unpack('b', bytes([data[offset + 2]]))[0]
        result['defaultValue'] = struct.unpack('b', bytes([data[offset + 3]]))[0]
        offset += 4
        unit, offset = _read_cstr(data, offset)
        result['unit'] = unit

    elif param_type == PARAM_TYPE_TEXT_SELECTION:
        # Options is a NUL-terminated string of ';'-separated labels.
        # Preserve all indices including empty slots — the UI skips empty ones
        # at render time so that option indices remain stable.
        opts_str, offset = _read_cstr(data, offset)
        result['options'] = opts_str.split(';')
        if offset + 4 > len(data):
            return result
        result['value']        = data[offset]
        result['min']          = data[offset + 1]
        result['max']          = data[offset + 2]
        result['defaultValue'] = data[offset + 3]
        offset += 4
        unit, offset = _read_cstr(data, offset)
        result['unit'] = unit

    elif param_type == PARAM_TYPE_FOLDER:
        pass  # nothing more to read — folder has only name

    elif param_type == PARAM_TYPE_INFO:
        value, offset = _read_cstr(data, offset)
        result['value'] = value

    elif param_type == PARAM_TYPE_COMMAND:
        if offset + 2 > len(data):
            return result
        result['status']  = data[offset]
        result['timeout'] = data[offset + 1]
        offset += 2
        value, offset = _read_cstr(data, offset)
        result['value'] = value

    elif param_type == PARAM_TYPE_STRING:
        value, offset = _read_cstr(data, offset)
        result['value'] = value

    # Unhandled types (UINT16, INT16, FLOAT): return dict with value=None.
    # The UI renders "Unsupported type N" and blocks writes — matches web behaviour.

    return result


def parse_device_info(payload: bytes) -> Optional[Dict[str, Any]]:
    """Parse a DEVICE_INFO payload with dest+origin already stripped.

    Port of the DEVICE_INFO field walk in handleDeviceInfo (scan.js:1156-1191).
    The caller strips payload[0:2] (dest, origin) before calling this function.

    Layout after stripping:
        [0..k]      name (NUL-terminated)
        [k+1..k+4]  serialNumber  (uint32 big-endian)
        [k+5..k+8]  hardwareId    (uint32 big-endian)
        [k+9..k+12] firmwareId    (uint32 big-endian)
        [k+13]      parametersTotal  (uint8)
        [k+14]      parameterVersion (uint8)

    Returns None on too-short payload.
    """
    if len(payload) < 3:
        return None

    name, offset = _read_cstr(payload, 0)

    # Need 4+4+4+1+1 = 14 bytes after the name
    if offset + 14 > len(payload):
        return None

    serial_number = struct.unpack('>I', payload[offset:offset + 4])[0]; offset += 4
    hardware_id   = struct.unpack('>I', payload[offset:offset + 4])[0]; offset += 4
    firmware_id   = struct.unpack('>I', payload[offset:offset + 4])[0]; offset += 4
    parameters_total  = payload[offset]
    parameter_version = payload[offset + 1]

    return {
        'name':             name,
        'serialNumber':     serial_number,
        'hardwareId':       hardware_id,
        'firmwareId':       firmware_id,
        'parametersTotal':  parameters_total,
        'parameterVersion': parameter_version,
        'isElrs':           serial_number == ELRS_SERIAL_NUMBER,
    }
