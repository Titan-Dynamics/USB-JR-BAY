"""
Device Parameter Parsing and Validation

This module handles parsing and validation of ELRS device parameters
received via CRSF protocol.
"""


class DeviceParameterParser:
    """Parser for ELRS device parameter fields."""

    @staticmethod
    def parse_field_blob(data: bytes) -> dict:
        """Parse a single field blob (full assembled data for a parameter) into a dictionary.

        Args:
            data: Raw parameter field data bytes

        Returns:
            Dictionary with parsed field information including:
            - parent, type, hidden, name
            - values (for selection fields)
            - min/max/default/unit (for numeric fields)
            - status, info (for command fields)
        """
        out = {}
        if len(data) < 3:
            out["raw"] = data.hex()
            return out

        def read_cstr(buf, off):
            """Read null-terminated C string from buffer."""
            s = []
            while off < len(buf) and buf[off] != 0:
                s.append(buf[off])
                off += 1
            try:
                val = bytes(s).decode(errors="ignore")
            except Exception:
                val = ""
            return val, off + 1 if off < len(buf) and buf[off] == 0 else off

        def read_opts(buf, off):
            """Read semicolon-separated option list.

            IMPORTANT: Keep empty values in the list to preserve index mapping.
            The value index in the raw data counts ALL entries including empty ones.
            Empty entries should be filtered during display, not during parsing.
            """
            vals = []
            cur = []
            while off < len(buf) and buf[off] != 0:
                b = buf[off]
                off += 1
                if b == 59:  # ';'
                    s = bytes(cur).decode(errors="ignore") if cur else ""
                    # Keep empty values to preserve index mapping (like ELRS LUA and scan.js)
                    vals.append(s.strip())
                    cur = []
                else:
                    cur.append(b)
            # final
            if cur:
                s = bytes(cur).decode(errors="ignore")
                # Keep empty values to preserve index mapping
                vals.append(s.strip())
            return vals, (off + 1 if off < len(buf) and buf[off] == 0 else off)

        def read_uint(buf, off, size):
            """Read unsigned integer of given byte size (big-endian)."""
            v = 0
            for i in range(size):
                if off + i < len(buf):
                    v = (v << 8) + buf[off + i]
            return v

        offset = 0
        parent = data[offset]
        offset += 1
        ftype = data[offset] & 0x7F
        hidden = bool(data[offset] & 0x80)
        offset += 1
        out["parent"] = parent
        out["type"] = ftype
        out["hidden"] = hidden

        # Name string
        name, offset = read_cstr(data, offset)
        out["name"] = name

        # Text selection (type 9)
        if ftype == 9:
            vals, offset = read_opts(data, offset)
            out["values"] = vals
            # selection index is next byte if present
            if offset < len(data):
                out["value"] = data[offset]
                offset += 1
            # unit may follow
            if offset < len(data) and data[offset] != 0:
                unit, offset = read_cstr(data, offset)
                out["unit"] = unit

        # Numeric values: determine size and signedness
        if 0 <= ftype <= 8:
            # size in bytes: floor(ftype / 2) + 1
            size = (ftype // 2) + 1
            is_signed = (ftype % 2) == 1
            # value at current offset, followed by min/max/default
            if offset + size <= len(data):
                out["value"] = read_uint(data, offset, size)
            if offset + size * 2 <= len(data):
                out["min"] = read_uint(data, offset + size, size)
            if offset + size * 3 <= len(data):
                out["max"] = read_uint(data, offset + size * 2, size)
            if offset + size * 4 <= len(data):
                out["default"] = read_uint(data, offset + size * 3, size)

            # convert unsigned to signed range if required
            if is_signed:
                def signed_val(v, s):
                    """Convert unsigned to signed 2's complement."""
                    band = 1 << (s * 8 - 1)
                    if v & band:
                        v = v - (1 << (s * 8))
                    return v

                if "value" in out:
                    out["value"] = signed_val(out["value"], size)
                if "min" in out:
                    out["min"] = signed_val(out["min"], size)
                if "max" in out:
                    out["max"] = signed_val(out["max"], size)
                if "default" in out:
                    out["default"] = signed_val(out["default"], size)

        # Float type (8)
        if ftype == 8:
            if offset + 4 <= len(data):
                out["value_raw"] = read_uint(data, offset, 4)
            if offset + 8 <= len(data):
                out["min"] = read_uint(data, offset + 4, 4)
            if offset + 12 <= len(data):
                out["max"] = read_uint(data, offset + 8, 4)
            if offset + 16 <= len(data):
                out["default"] = read_uint(data, offset + 12, 4)
            if offset + 21 <= len(data):
                out["prec"] = data[offset + 16]
            if offset + 25 <= len(data):
                out["step"] = read_uint(data, offset + 17, 4)
            # convert raw values using precision
            if "prec" in out and out["prec"] > 0:
                div = 10 ** out["prec"]
                try:
                    out["value"] = out.get("value_raw", 0) / div
                    if "min" in out:
                        out["min"] = out["min"] / div
                    if "max" in out:
                        out["max"] = out["max"] / div
                    if "default" in out:
                        out["default"] = out["default"] / div
                except Exception:
                    pass

        # String type (type 12/13)
        if ftype == 12 or ftype == 13:
            s, offset = read_cstr(data, offset)
            out["value"] = s
            # optional maxlen after string
            if offset < len(data):
                out["maxlen"] = data[offset]
                offset += 1

        # Command type (13)
        if ftype == 13:
            if offset + 1 <= len(data):
                out["status"] = data[offset]
                offset += 1
            if offset + 1 <= len(data):
                out["timeout"] = data[offset]
                offset += 1
            if offset < len(data):
                s, offset = read_cstr(data, offset)
                out["info"] = s

        # Generic fallback: last byte as status if present
        if len(data) > 0:
            out["status"] = data[-1]

        return out

    @staticmethod
    def validate_parsed_field(parsed: dict, field_id: int) -> bool:
        """Validate a parsed field to detect corruption.

        Args:
            parsed: Parsed field dictionary
            field_id: Field ID for logging

        Returns:
            True if the field appears valid, False if it should be re-read
        """
        try:
            # Check if parse returned raw data only (indicates parse failure)
            if "raw" in parsed and len(parsed) == 1:
                return False

            # Validate field type is in expected range (0-13 for known types)
            ftype = parsed.get('type')
            if ftype is None:
                return False
            if not (0 <= ftype <= 13):
                return False

            # For type 9 (combo/select), validate we have values list
            if ftype == 9:
                values = parsed.get('values', [])
                if not isinstance(values, list) or len(values) == 0:
                    return False
                # Check that value index is within bounds
                val_idx = parsed.get('value')
                if val_idx is not None and val_idx < 0:
                    return False

            # For type 11 (folder/info), ensure we have a name
            if ftype == 11:
                name = parsed.get('name', '')
                if not name or len(name) == 0:
                    return False

            # Basic sanity check: all fields should have a name
            name = parsed.get('name', '')
            if not name or len(name) == 0:
                return False

            # Check for obviously corrupted names (non-printable chars)
            if not all(32 <= ord(c) < 127 or c in '\n\r\t' for c in name):
                return False

            return True
        except Exception:
            return False


class DeviceInfo:
    """Container for ELRS device information."""

    def __init__(self, src: int):
        """Initialize device info.

        Args:
            src: Device source address
        """
        self.src = src
        self.name = ""
        self.serial = ""
        self.hw_ver = ""
        self.sw_ver = ""
        self.n_params = 0
        self.proto_ver = 0
        self.fields = {}
        self.fetched = set()
        self.loaded = False

    def to_dict(self) -> dict:
        """Convert to dictionary for backwards compatibility."""
        return {
            "name": self.name,
            "serial": self.serial,
            "hw_ver": self.hw_ver,
            "sw_ver": self.sw_ver,
            "n_params": self.n_params,
            "proto_ver": self.proto_ver,
            "fields": self.fields,
            "fetched": self.fetched,
            "loaded": self.loaded,
        }

    @staticmethod
    def parse_device_info_payload(payload: bytes) -> tuple:
        """Parse CRSF DEVICE_INFO payload.

        Args:
            payload: Raw DEVICE_INFO payload bytes

        Returns:
            Tuple of (dst, src, name, serial, hw_ver, sw_ver, n_params, proto_ver)

        Raises:
            ValueError: If payload is too short or invalid
        """
        if len(payload) < 4:
            raise ValueError("Device info payload too short")

        dst = payload[0]
        src = payload[1]

        # Read name (null-terminated string)
        idx = 2
        name_bytes = []
        while idx < len(payload) and payload[idx] != 0:
            name_bytes.append(payload[idx])
            idx += 1
        name = bytes(name_bytes).decode(errors="ignore")
        idx += 1  # skip null
        offset_after_name = idx

        # Read serial (4 bytes)
        if offset_after_name + 4 <= len(payload):
            serial = payload[offset_after_name:offset_after_name + 4]
        else:
            serial = b""

        # Read hardware version (4 bytes)
        if offset_after_name + 8 <= len(payload):
            hw_ver = payload[offset_after_name + 4:offset_after_name + 8]
        else:
            hw_ver = b""

        # Read software version (3 bytes)
        if offset_after_name + 11 <= len(payload):
            sw_maj = payload[offset_after_name + 8]
            sw_min = payload[offset_after_name + 9]
            sw_rev = payload[offset_after_name + 10]
        else:
            sw_maj = sw_min = sw_rev = 0

        # Read n_params at offset+12
        if offset_after_name + 12 < len(payload):
            fields_count = payload[offset_after_name + 12]
        else:
            fields_count = 0

        # Read protocol version at offset+13
        if offset_after_name + 13 < len(payload):
            proto_ver = payload[offset_after_name + 13]
        else:
            proto_ver = 0

        return (
            dst,
            src,
            name,
            serial.hex(),
            hw_ver.hex(),
            f"{sw_maj}.{sw_min}.{sw_rev}",
            fields_count,
            proto_ver
        )
