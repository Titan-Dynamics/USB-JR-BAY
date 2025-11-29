# ELRS Feeder - Desktop Joystick to CRSF Bridge

A PyQt5-based desktop application that bridges joystick inputs to ExpressLRS (ELRS) transmitter modules via CRSF protocol over USB serial. This application allows you to control RC aircraft using a USB joystick connected to your computer, forwarding channel data through an ESP32 to an ELRS TX module.

## Table of Contents

- [Overview](#overview)
- [System Architecture](#system-architecture)
- [How It Works](#how-it-works)
- [Module Architecture](#module-architecture)
- [Installation](#installation)
- [Usage](#usage)
- [Configuration](#configuration)
- [Protocol Details](#protocol-details)
- [Development](#development)

## Overview

The ELRS Feeder application provides:

- **Joystick Input**: Reads axes and buttons from USB joysticks (supports hotplug with pygame 2+)
- **Channel Mapping**: Configure 16 channels with flexible source mapping (axes, buttons, constants)
- **CRSF Protocol**: Implements full CRSF protocol for communication with ELRS modules
- **Device Management**: Auto-discovery and parameter configuration for ELRS devices
- **Telemetry Display**: Real-time link statistics (RSSI, LQ, SNR)
- **Parameter UI**: Configure TX module parameters directly from the GUI
- **Persistent Config**: Saves channel mappings and settings to `calib.json`

### Key Features

- Automatic joystick detection and reconnection
- Configurable channel modes: axis mapping, button toggles, rotary encoders, constants
- Real-time telemetry display (signal strength, link quality, packet rates)
- Auto-discovery of ELRS devices on the serial bus
- Full ELRS parameter read/write support
- Failsafe behavior when joystick disconnects

## System Architecture

```
┌──────────────┐       USB        ┌──────────────┐      USB CDC      ┌──────────────┐
│   Joystick   │ ───────────────> │ Feeder.py    │ ────────────────> │   ESP32-S3   │
│ (HID Device) │                  │ (This App)   │  CRSF Channels   │ (Transparent │
└──────────────┘                  └──────────────┘                   │  Forwarder)  │
                                         ▲                            └──────┬───────┘
                                         │                                   │
                                         │ Telemetry & Parameters            │ UART
                                         └───────────────────────────────────┤ 1.87 Mbaud
                                                                             │ CRSF
                                                                      ┌──────▼───────┐
                                                                      │  ELRS TX     │
                                                                      │  Module      │
                                                                      └──────────────┘
```

### Data Flow

1. **Input**: Joystick axes/buttons → Python pygame
2. **Processing**: Channel mapping logic → 16-channel CRSF frame
3. **Serial TX**: CRSF channels frame → ESP32 via USB CDC serial
4. **ESP32**: Transparent serial forwarder (no processing)
5. **Output**: ESP32 UART → ELRS TX module at 1.87 Mbaud CRSF
6. **Feedback**: TX module telemetry → ESP32 → Feeder GUI

## How It Works

### ESP32 Interface

The ESP32 acts as a **transparent serial bridge**:
- **No CRSF processing on ESP32** - it simply forwards bytes bidirectionally
- Python app sends CRSF frames over USB CDC serial
- ESP32 forwards these frames to TX module UART (5.25 Mbaud)
- TX module telemetry/responses flow back through ESP32 to Python app
- This architecture keeps the ESP32 firmware simple and makes the Python app the "CRSF master"

### Communication Protocol

The application communicates with the ESP32 using raw serial at the configured baud rate (default 5250000 for CRSF compatibility, though the ESP32 transparently forwards at whatever rate). Two formats are supported (see parent README.md):

1. **CRSF Binary Frames** (used by this app):
   - Standard CRSF protocol frames
   - Includes RC channels, device info, parameters, telemetry
   - CRC-protected with CRSF CRC8-D5

### Channel Update Rate

- Uses mixer sync frames from ELRS to set channel update rate
- Only sends channels when joystick is connected
- Stops sending if no joystick updates for >1 second (failsafe)

### File Structure

```
tools/feeder/
├── feeder.py              # Main application (1,096 lines)
├── crsf_protocol.py       # CRSF protocol implementation (148 lines)
├── device_parameters.py   # Device parameter parsing (331 lines)
├── serial_interface.py    # Serial communication (1,022 lines)
├── joystick_handler.py    # Joystick input handling (160 lines)
├── channel_ui.py          # Channel UI components (380 lines)
├── config_manager.py      # Configuration persistence (80 lines)
├── calib.json            # User configuration (auto-generated)
├── requirements.txt       # Python dependencies
└── README.md             # This file
```

### Module Responsibilities

#### 1. `crsf_protocol.py` - CRSF Protocol Implementation

Pure protocol implementation with no dependencies on other modules.

**Key Functions:**
- `crc8_d5(data)` - CRSF CRC8 calculation with 0xD5 polynomial
- `build_crsf_frame(ftype, payload, dest, origin)` - Build generic CRSF frame
- `build_crsf_channels_frame(channels)` - Build RC channels frame (11-bit packed)
- `us_to_crsf_val(us)` - Convert microseconds (1000-2000) to CRSF (172-1811)
- `crsf_val_to_us(val)` - Convert CRSF value back to microseconds
- `unpack_crsf_channels(payload)` - Unpack 11-bit channel data

**Constants:**
- Frame types (RC_CHANNELS, LINK_STATISTICS, DEVICE_INFO, etc.)
- Address definitions (FLIGHT_CONTROLLER, TRANSMITTER, ELRS_LUA, etc.)
- Protocol parameters (CRSF channel range 172-1811)

**Reusability**: This module is standalone and can be reused in any CRSF-related project.

#### 2. `device_parameters.py` - Device Parameter Parsing

Handles parsing and validation of ELRS device parameter fields.

**Classes:**

- **`DeviceParameterParser`**: Parses parameter field binary data
  - `parse_field_blob(data)` - Parse parameter field from binary
  - `validate_parsed_field(parsed)` - Validate parsed field structure
  - Handles multiple field types: UINT8, INT8, FLOAT, TEXT_SELECTION, STRING, FOLDER, INFO, COMMAND

- **`DeviceInfo`**: Container for device information
  - Stores device name, serial number, hardware/software versions, parameter count
  - `parse_device_info_payload(payload)` - Parse DEVICE_INFO frame

**Field Types Supported:**
- Numeric: UINT8, INT8, FLOAT (with min/max/default)
- Selection: TEXT_SELECTION (dropdown options)
- Text: STRING (editable text)
- Organizational: FOLDER, INFO (UI structure)
- Action: COMMAND (trigger device actions)

#### 3. `serial_interface.py` - Serial Communication Thread

The heart of serial communication and CRSF frame handling.

**Class: `SerialThread`** (extends `QtCore.QObject`)

**Responsibilities:**
- Serial port connection/reconnection with auto-retry
- CRSF frame parsing from incoming serial stream
- Device discovery via PING frames
- Parameter loading (chunked reads with retry logic)
- Parameter writing with validation
- Telemetry extraction from LINK_STATISTICS frames
- Channel transmission (rate-limited to SEND_HZ)
- Timing synchronization with TX module

**Signals (Qt):**
- `telemetry` - Link statistics dict (RSSI, LQ, SNR, etc.)
- `debug` - Debug/log messages for UI console
- `channels_update` - Received channel values (16 × microseconds)
- `sync_update` - Timing sync data (interval, offset, source)
- `connection_status` - Serial connected/disconnected
- `device_discovered` - Device responded to ping (src, device_info)
- `device_parameters_loaded` - All parameters fetched (src, device_info)
- `device_parameters_progress` - Loading progress (src, fetched, total)
- `device_parameter_field_updated` - Single parameter changed (src, field_id, data)

**Key Methods:**
- `run()` - Main thread loop (read serial, parse frames, send channels)
- `_connect()` - Connect to serial port
- `_parse_frame(frame_type, payload, src)` - Handle incoming CRSF frames
- `set_channels(channels)` - Update channel values to transmit
- `write_device_field(src, field_id, value)` - Write parameter to device

**Protocol Handling:**
- Auto-discovery: Sends PING frames, listens for DEVICE_INFO responses
- Parameter loading: Sequential chunked reads with timeout/retry
- Failsafe: Stops sending channels if joystick inactive >1s

#### 4. `joystick_handler.py` - Joystick Input

Manages joystick detection, reading, and hotplug support.

**Class: `JoystickHandler`**

**Responsibilities:**
- Auto-detect available joysticks
- Hotplug support (pygame 2+) - detect connect/disconnect
- Read axes and button states
- Normalize axis values to -1.0 to +1.0 range

**Key Methods:**
- `scan()` - Scan for joysticks and connect to first found
- `poll()` - Read current joystick state, return (axes[], buttons[])
- `is_connected()` - Check if joystick still connected
- `disconnect()` - Clean up joystick resources

**Features:**
- Automatic reconnection on hotplug
- Returns empty lists when disconnected (failsafe)
- Thread-safe operation

#### 5. `channel_ui.py` - Channel Configuration UI

PyQt5 widgets for channel mapping configuration.

**Class: `ChannelRow`** (extends `QtWidgets.QWidget`)

**Responsibilities:**
- Single channel configuration row in UI
- Source selection: Axis, Button, or Constant
- Mode configuration for buttons: Toggle or Rotary
- Min/max/trim adjustment for axes
- Invert axis option
- Real-time channel value display and servo bar visualization

**Key Methods:**
- `set_joy_state(axes, buttons)` - Update from joystick state
- `get_ch_us()` - Calculate current channel value in microseconds
- `to_cfg()` - Serialize to config dict
- `from_cfg(cfg)` - Deserialize from config dict

**Function: `map_axis_to_range(val, min_us, max_us, trim)`**
- Maps axis value (-1.0 to +1.0) to channel microseconds
- Applies trim offset and respects min/max limits

**Source Types:**
- `Axis X` - Map joystick axis with calibration
- `Button N` - Binary on/off (1000/2000 μs)
- `Toggle N` - Button press toggles state
- `Rotary N` - Button increments value (3-position: 1000/1500/2000)
- `Const` - Fixed value (e.g., always 1500 μs)

#### 6. `config_manager.py` - Configuration Persistence

Handles loading/saving of application configuration.

**Class: `ConfigManager`**

**Methods:**
- `load(filepath)` - Load configuration from JSON file
- `save(filepath, config)` - Save configuration to JSON file
- Returns default config if file missing/corrupt

**Function: `get_available_ports()`**
- Enumerate available serial ports
- Returns list of port names (e.g., ['COM3', 'COM4'])

**Configuration Structure:**
```json
{
  "serial_port": "COM3",
  "channels": [
    {
      "src": "Axis 0",
      "mode": "normal",
      "min": 1000,
      "max": 2000,
      "trim": 0,
      "invert": false
    },
    // ... 15 more channels
  ]
}
```

**Constants:**
- `DEFAULT_PORT` - Default serial port selection
- `DEFAULT_BAUD` - Baud rate (5250000)
- `CHANNELS` - Number of channels (16)
- `DEFAULT_CFG` - Default configuration template

#### 7. `feeder.py` - Main Application

The main GUI application that integrates all modules.

**Class: `Main`** (extends `QtWidgets.QWidget`)

**Responsibilities:**
- Main window and UI layout
- Tab interface: Channels, Telemetry, Parameters, Config
- Serial port selection and refresh
- Joystick status display
- Channel mapping UI (16 × ChannelRow instances)
- Telemetry display (link stats, GPS, battery)
- Device parameter UI (auto-generated from device fields)
- Configuration persistence (load/save via ConfigManager)
- Coordinate all modules (SerialThread, JoystickHandler, ChannelRow, etc.)

**Key Features:**
- **Channels Tab**: Configure all 16 channels with visual feedback
- **Telemetry Tab**: Real-time link statistics with color-coded quality
- **Parameters Tab**: Browse and modify TX module settings
- **Config Tab**: Serial port selection and connection status
- **Joystick Learn**: Click "Learn" button to auto-map axis/button to channel

**Threading:**
- Main UI runs on Qt event loop
- SerialThread runs in separate Python thread
- JoystickHandler polled from main thread timer
- Qt signals/slots for thread-safe communication

## Installation

### Requirements

- **Python**: 3.9 or later
- **Operating System**: Windows, Linux, or macOS
- **Hardware**: USB joystick, ESP32-S3, ELRS TX module (see parent README)

### Install Dependencies

```bash
cd tools/feeder
pip install -r requirements.txt
```

**Dependencies:**
- `pyserial>=3.5` - Serial port communication
- `pygame>=2.5` - Joystick input handling
- `pyqt5>=5.0` - GUI framework

### Verify Installation

Test that all modules load correctly:

```bash
python -m py_compile feeder.py crsf_protocol.py device_parameters.py serial_interface.py joystick_handler.py channel_ui.py config_manager.py
```

If no errors, all modules are valid Python.

## Usage

### Basic Startup

```bash
cd tools/feeder
python feeder.py
```

### First Run Setup

1. **Connect Hardware:**
   - Plug in ESP32 to computer via USB
   - Connect ESP32 to ELRS TX module (see parent README for wiring)
   - Power TX module (e.g., 2S battery ~8.4V)
   - Connect USB joystick to computer

2. **Configure Serial Port:**
   - Select COM port from dropdown (e.g., COM3, /dev/ttyUSB0)
   - Click "Refresh" if port not visible
   - Green "Connected" indicator appears when successful

3. **Verify Joystick:**
   - Top-left shows joystick status
   - Move joystick axes - should detect automatically
   - If not detected, replug joystick

4. **Map Channels:**
   - Go to "Channels" tab
   - For each channel, select source (e.g., "Axis 0" for throttle)
   - Use "Map" button to auto-detect axis/button movements
   - Adjust min/max/trim as needed
   - Invert axis if needed

5. **Test Link:**
   - Should see link statistics updating (RSSI, LQ, etc.)
   - Move joystick - verify servo bars move on "Channels" tab

6. **Configure Configuration (Optional):**
   - Go to "Configuration" tab
   - Wait for device discovery (~1 second)
   - Browse/modify TX module settings

### Creating a Standalone Executable

You can create a single-file executable using PyInstaller:

```bash
pyinstaller --onefile --windowed --icon="icon.ico" --name="USB JR Bay" feeder.py
```

This creates `dist/USB JR Bay.exe` (or equivalent on Linux/macOS) which can run without Python installed.

## Configuration

### Configuration File: `calib.json`

Auto-generated on first run and saved on exit. Contains:

- Serial port selection
- Per-channel configuration:
  - Source (axis/button/constant)
  - Mode (normal/toggle/rotary)
  - Min/max/trim values
  - Invert flag

**Example:**
```json
{
  "serial_port": "COM3",
  "channels": [
    {"src": "Axis 0", "mode": "normal", "min": 1000, "max": 2000, "trim": 0, "invert": false},
    {"src": "Axis 1", "mode": "normal", "min": 1000, "max": 2000, "trim": 0, "invert": true},
    {"src": "Toggle 0", "mode": "toggle", "min": 1000, "max": 2000, "trim": 0, "invert": false},
    {"src": "Const", "mode": "normal", "min": 1500, "max": 1500, "trim": 0, "invert": false}
  ]
}
```

### Channel Source Types

- **`Axis N`**: Map to joystick axis (0-based index)
  - Supports min/max/trim/invert
  - Full resolution analog control

- **`Button N`**: Map to joystick button (0-based index)
  - Binary: pressed=2000μs, released=1000μs
  - No min/max/trim (always full range)

- **`Toggle N`**: Button acts as toggle switch
  - First press → 2000μs, second press → 1000μs
  - Retains state across button releases

- **`Rotary N`**: Button increments through values
  - 3-position: 1000μs → 1500μs → 2000μs → 1000μs
  - Press to advance, cycles continuously

- **`Const`**: Fixed constant value
  - Set via min/max (both same value)
  - Useful for fixed channels

## Protocol Details

### CRSF Protocol Overview

ELRS uses the Crossfire (CRSF) protocol for RC channels, telemetry, and configuration.

**Frame Structure:**
```
[DEST] [LEN] [TYPE] [PAYLOAD...] [CRC8]
```

- **DEST**: Destination address (1 byte)
- **LEN**: Length of TYPE + PAYLOAD + CRC (1 byte)
- **TYPE**: Frame type identifier (1 byte)
- **PAYLOAD**: Variable length data
- **CRC8**: CRC-8 with 0xD5 polynomial over TYPE + PAYLOAD

### Key Frame Types

- `0x16` - RC_CHANNELS_PACKED: 16 channels, 11-bit packed (22 bytes payload)
- `0x14` - LINK_STATISTICS: Telemetry (RSSI, LQ, SNR, etc.)
- `0x28` - DEVICE_PING: Request device info
- `0x29` - DEVICE_INFO: Device details response
- `0x2B` - PARAMETER_SETTINGS_ENTRY: Single parameter field data
- `0x2C` - PARAMETER_READ: Request parameter field
- `0x2D` - PARAMETER_WRITE: Write parameter field

### Addresses

- `0xC8` - Flight controller (dest for RC channels)
- `0xEA` - Transmitter
- `0xEE` - Transmitter
- `0xEE` - ELRS LUA (parameter interface)

### Channel Encoding

Channels are 11-bit values packed into 22 bytes:

- **Range**: 172 (988μs) to 1811 (2012μs)
- **Center**: 992 (1500μs)
- **Packing**: Big-endian bit packing, LSB first

Conversion formulas:
```python
# Microseconds to CRSF (1000-2000 μs → 172-1811)
crsf_val = int(((us - 1000) / 1000) * 1639 + 172)

# CRSF to Microseconds
us = int(((crsf_val - 172) / 1639) * 1000 + 1000)
```

### Device Parameter Protocol

Parameters use a chunked read protocol:

1. **Discovery**: Send DEVICE_PING, receive DEVICE_INFO with parameter count
2. **Read Loop**: For each parameter index (0 to count-1):
   - Send PARAMETER_READ with field_id
   - Receive PARAMETER_SETTINGS_ENTRY chunks
   - Reassemble chunks into complete field blob
   - Parse field blob into structured data
3. **Write**: Send PARAMETER_WRITE with field_id + new value

**Chunk Handling:**
- Large parameters split across multiple frames
- Each chunk has `chunks_remain` counter
- Timeout/retry logic for lost chunks (100ms timeout)
- CRC validation on complete field blob

## Development

### Architecture Design Principles

The architecture follows these principles:

1. **Single Responsibility**: Each module has one clear purpose
2. **Separation of Concerns**: UI, protocol, I/O, and business logic separated
3. **DRY (Don't Repeat Yourself)**: Common code extracted to modules
4. **Loose Coupling**: Modules interact via well-defined interfaces
5. **High Cohesion**: Related functionality grouped together
