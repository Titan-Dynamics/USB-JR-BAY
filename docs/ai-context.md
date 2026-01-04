# ESP32-CRSF-emulator - AI Context Guide

## Project Overview

This project implements a **CRSF (Crossfire) protocol handler** for ESP32-S3 that emulates an EdgeTX radio communicating with an ExpressLRS (ELRS) TX module. The communication happens over a **single-wire half-duplex UART** on GPIO5.

### Purpose
- Act as a bridge between PC (via USB) and ELRS TX module
- Send RC channel data to ELRS module (16 channels, 11-bit each)
- Receive and forward telemetry (link stats, etc.)
- Maintain precise timing synchronization with the module
- SHandle one-wire half-duplex UART timing to avoid collisions

### Hardware
- **MCU**: ESP32-S3 (Seeed XIAO ESP32S3)
- **Module**: ExpressLRS TX module
- **Connection**: GPIO5 (single-wire half-duplex UART)
- **Debug / PC connection**: USB CDC Serial

---

## Directory Structure

```
ESP32-CRSF-emulator/
├── docs/
│   ├── ai-context.md            # This file
│   └── CRSF_ONEWIRE_PROTOCOL.md # Complete protocol and UART handling documentation
├── include/                     # Public headers
│   ├── crsf_protocol.h         # Protocol constants, CRC, frame builders
│   ├── crsf_uart.h             # Half-duplex UART management
│   ├── module_sync.h           # Timing synchronization
│   ├── rc_channels.h           # RC channel value management
│   ├── crsf_parser.h           # Frame reception state machine (module)
│   ├── cdc_parser.h            # CDC frame reception (PC control)
│   └── crsf_task.h             # Main CRSF task class
├── src/                         # Implementation files
│   ├── main.cpp                # Arduino setup/loop entry point
│   ├── crsf_protocol.cpp       # CRC and frame building
│   ├── crsf_uart.cpp           # ESP32 GPIO matrix half-duplex
│   ├── module_sync.cpp         # Timing correction algorithm
│   ├── rc_channels.cpp         # Channel value conversion
│   ├── crsf_parser.cpp         # Frame reception & dispatch (module)
│   ├── cdc_parser.cpp          # Frame reception & dispatch (PC)
│   └── crsf_task.cpp           # Main CRSF task implementation
├── test/                        # Unit and integration tests
│   ├── test_cdc_parser/        # CDC parser tests
│   └── test_crsf_task/         # CRSF task integration tests
├── 
└── platformio.ini              # Build configuration

```

---

## Key Concepts

### 1. Half-Duplex One-Wire UART

**Critical constraint**: Only ONE device can transmit at a time on the shared wire.

**Timing coordination**:
- Radio sends RC frame at start of each period (~4ms if running at 250Hz)
- Module responds with telemetry in the remaining window
- **No hardware handshake** - purely timing-based turn-taking

**ESP32 Implementation** (`crsf_uart.cpp`):
- Uses GPIO matrix to route both TX and RX signals to same pin
- Switches between modes by reconfiguring GPIO and matrix routing
- Disconnects RX when transmitting to avoid receiving own bytes
- Waits for `uart_ll_is_tx_idle()` before switching back to RX

### 2. Timing Synchronization

The ELRS module sends **RADIO_ID frames (0x3A)** every 200ms containing:
- **Refresh Rate**: Desired RC frame interval (e.g., 4000µs = 250Hz)
- **Input Lag**: Timing offset (how early/late last frame was)

The esp32 adjusts its next frame period using EdgeTX's algorithm:
```cpp
adjustedPeriod = refreshRate + inputLag  // Apply correction
inputLag -= (adjustedPeriod - refreshRate)  // Consume lag
```

This creates a **negative feedback loop** that keeps frames synchronized.

### 3. CRSF Frame Structure

```
+--------+--------+--------+-- ... --+--------+
| ADDR   | LEN    | TYPE   | PAYLOAD | CRC    |
+--------+--------+--------+-- ... --+--------+
   1B       1B       1B      0-60B      1B
```

- **ADDR**: Destination (0xEE=module, 0xEA=radio, 0xC8=sync)
- **LEN**: Type + Payload + CRC (excludes ADDR and LEN itself)
- **TYPE**: Frame type ID (0x16=RC, 0x14=LinkStats, 0x3A=Sync, etc.)
- **CRC**: CRC8 with polynomial 0xD5

### 4. RC Channel Encoding

16 channels × 11 bits = 176 bits = 22 bytes (packed)
- **Center value**: 992 (corresponds to 1500µs PWM)
- **Range**: 0-1984 (CRSF units)
- **Standard range**: 191-1792 (maps to 1000-2000µs PWM)

### 5. Failsafe Mechanism

**Purpose**: Prevent RC frame transmission when PC control is lost

**Implementation**:
- `CDCParser` tracks timestamp of last received RC frame
- Failsafe activates if no RC frame received for >100ms
- Failsafe is **active by default** until first RC frame received
- `CRSFTask` checks failsafe status before transmitting RC frames

**Behavior**:
- When failsafe is **active**: No RC frames are transmitted to module
- When failsafe is **inactive**: Normal RC frame transmission occurs
- Non-RC frames (ping, parameter queries) don't affect failsafe status
- RC frame transmission resumes immediately upon failsafe recovery

**Timeout**: 100ms (configurable via `CDCParser::FAILSAFE_TIMEOUT_US`)

---

## Class Responsibilities

### Core Classes

| Class | File | Purpose |
|-------|------|---------|
| `CRSFUart` | crsf_uart.{h,cpp} | Manages half-duplex UART, TX/RX switching |
| `CRSFParser` | crsf_parser.{h,cpp} | Receives bytes from module, assembles frames, dispatches via callbacks |
| `CDCParser` | cdc_parser.{h,cpp} | Receives bytes from PC CDC serial, parses RC frames, updates channels, manages failsafe |
| `ModuleSync` | module_sync.{h,cpp} | Processes timing updates, calculates adjusted period |
| `RCChannels` | rc_channels.{h,cpp} | Manages 16 channels, converts µs ↔ CRSF values |
| `CRSFTask` | crsf_task.{h,cpp} | Main task coordination: timing, TX/RX switching, frame scheduling, failsafe enforcement |

### Callback Architecture

The system uses callbacks for decoupling:

| Callback | Purpose | Set By | Called By |
|----------|---------|--------|-----------|
| `CRSFToCDCCallback` | Forward frames to PC | `main.cpp` | `CRSFParser` |
| `CRSFTimingSyncCallback` | Update timing sync | `main.cpp` | `CRSFParser` |
| `ModuleSyncCallback` | Timing update notification | N/A (unused) | `ModuleSync` |
| `CDCForwardCallback` | Forward CDC frames to module | `main.cpp` | `CDCParser` |

**Key Design Principle**: Classes don't depend on each other directly in constructors. Instead, they use callbacks set at runtime to decouple dependencies.

### Helper Functions

| Function | File | Purpose |
|----------|------|---------|
| `crsf_crc8()` | crsf_protocol.cpp | CRC calculation |
| `crsf_pack_channels()` | crsf_protocol.cpp | Pack 16×11bit into 22 bytes |
| `crsf_unpack_channels()` | crsf_protocol.cpp | Unpack 22 bytes into 16×11bit channels |
| `crsf_build_rc_frame()` | crsf_protocol.cpp | Build complete RC frame |
| `crsf_build_ping_frame()` | crsf_protocol.cpp | Build device discovery frame |

---

## Threading Model

**Single-threaded** Arduino loop model:
- `loop()` calls `crsfTask.run()` continuously
- `CRSFTask` manages all timing, frame scheduling, and UART mode switching
- No OS threads, no mutexes needed
- All timing is non-blocking (check `micros()` / `millis()`)

### CRSFTask Architecture

The `CRSFTask` class encapsulates the main task logic as a proper object-oriented component:

**Responsibilities:**
- Timing coordination for RC frame transmission
- UART TX/RX mode switching
- Processing incoming frames from module
- Managing queued output frames (ping, parameter queries, etc.)
- Enforcing failsafe by preventing RC transmission when CDC link is lost

**Initialization:**
```cpp
CRSFTask crsfTask(crsfUart, parser, cdcParser, moduleSync, rcChannels);
```

**Main Loop Integration:**
```cpp
void loop() {
    crsfTask.run();  // Execute one iteration
    handleSerialCommands();
}
```

**Dependencies injected at construction:**
- `CRSFUart` - UART interface for communication
- `CRSFParser` - Frame parser for incoming data
- `CDCParser` - CDC parser for PC communication and failsafe status
- `ModuleSync` - Timing synchronization
- `RCChannels` - RC channel data

**State management:**
All timing state (last frame time, frame count, etc.) is encapsulated within the class instance, making it testable and maintainable.

---

## Error Handling

**Strategy**: Log errors, continue operation
- CRC failures: Increment counter, discard frame
- Invalid frame length: Reset parser state
- No sync received: Use default period (4ms)
- UART init failure: Halt with error message
- CDC link lost: Activate failsafe, stop RC transmission

**No exceptions** - bare metal embedded environment

---

## Build & Run

### Build
```bash
platformio.exe run --environment seeed_xiao_esp32s3
```

### Upload
```bash
platformio.exe run --target upload --environment seeed_xiao_esp32s3
```

### Monitor
```bash
platformio.exe device monitor --environment seeed_xiao_esp32s3
```

### Testing
```bash
# Run native unit tests (requires GCC installed)
platformio.exe test --environment native
```

---

## Adding New Features

### Frame Processing Changes

The ESP32 forwards most frames to PC rather than processing them locally:

**To add handling for a new frame type:**

1. **If it needs local processing** (like RADIO_ID timing frames):
   - Add parsing logic to `CRSFParser::processFrame()`
   - Extract data from payload
   - Call appropriate callback with parsed data

2. **If it should be forwarded to PC** (most telemetry):
   - No changes needed! CRSFParser automatically forwards all non-RADIO_ID frames to CDC

3. **Example - hypothetical local frame processing**:
   ```cpp
   // In crsf_parser.cpp
   if (type == CRSF_FRAMETYPE_MY_NEW_TYPE) {
       parseMyNewFrame(payload, payloadLen);
       return;  // Don't forward if processed locally
   }
   ```

### PC Control via USB CDC Serial:

**RC Channel Control**:
- PC sends CRSF RC frames (type 0x16) via USB CDC serial
- `CDCParser` receives and validates frames
- Channel values are automatically updated in `RCChannels`
- Updated values persist and are sent to module in subsequent RC frames
- Failsafe mechanism ensures RC transmission stops if CDC link is lost

**Failsafe Implementation**:

The failsafe mechanism prevents RC frame transmission when the PC control link is lost:

1. **Tracking**: `CDCParser` tracks the timestamp of the last received RC frame
2. **Timeout**: If no RC frame received for >100ms, failsafe activates
3. **Default State**: Failsafe is **active by default** until first RC frame received
4. **Enforcement**: `CRSFTask` checks `cdcParser.isFailsafe()` before sending RC frames
5. **Recovery**: RC transmission resumes immediately when new CDC RC frame is received

Key methods:
- `CDCParser::isFailsafe()` - Returns true if failsafe is active
- `CDCParser::getLastRCFrameTime()` - Returns timestamp of last RC frame
- `CDCParser::FAILSAFE_TIMEOUT_US` - Configurable timeout (default 100ms)

This ensures the module doesn't continue transmitting with stale channel data if the PC application crashes or USB disconnects.

**Bidirectional CRSF Forwarding**:
The ESP32 acts as a transparent bridge for device discovery and configuration:

**PC → ESP32 → Module** (forwarded frame types):
- `PING_DEVICES (0x28)` - Device discovery requests
- `PARAMETER_READ (0x2C)` - Parameter query requests
- `PARAMETER_WRITE (0x2D)` - Parameter update requests
- `COMMAND (0x32)` - Command frames

**Module → ESP32 → PC** (forwarded frame types):
- `DEVICE_INFO (0x29)` - Device discovery responses
- `PARAMETER_SETTINGS_ENTRY (0x2B)` - Parameter value responses

This allows PC-based configuration tools (like ELRS Configurator) to communicate with the TX module and receiver through the ESP32 bridge.

**Key Architecture Points**:
- ESP32 acts as a transparent bridge for most CRSF frames
- Only RADIO_ID (timing sync) frames are processed locally
- All other frames flow through to CDC without parsing
- This keeps the ESP32 firmware simple and focused on RC transmission

**Adding custom PC commands**:
1. **Modify `handleSerialCommands()`** in `main.cpp`
2. **Add new command characters** (single-byte commands like 'd', 's', etc.)
3. **Keep commands simple** to avoid conflicts with CRSF frame bytes

### Add new frame builder:

1. **Declare in** `crsf_protocol.h`
2. **Implement in** `crsf_protocol.cpp`
3. **Call from** `main.cpp` via `queueOutputFrame()`

---

## Key Invariants

1. **Single transmitter**: Only one device TX at a time on the wire
2. **Frame timing**: Module MUST respond within the period window
3. **CRC required**: All frames must have valid CRC or get discarded
4. **Sync valid**: After receiving first RADIO_ID frame with SUBCMD=0x10
5. **No local telemetry parsing**: ESP32 forwards all telemetry to PC
6. **Callback-based coupling**: Classes communicate via callbacks, not direct dependencies
7. **Failsafe active by default**: No RC frames transmitted until first CDC RC frame received
8. **Failsafe timeout**: 100ms since last CDC RC frame (configurable)

---

## Architecture Considerations

### Main.cpp Scope
- **Keep main.cpp lightweight** - focused on Arduino boilerplate (setup/loop)
- Domain-specific logic belongs in dedicated classes/files
- main.cpp should only contain:
  - Configuration constants
  - Global object instantiation
  - Simple helper functions for Arduino loop coordination
  - setup() and loop() functions

### Separation of Concerns
- **Module communication** → CRSFParser (parses frames from TX module)
- **PC communication** → CDCParser (parses frames from USB CDC serial)
- **Channel management** → RCChannels (stores and converts channel values)
- **Timing** → ModuleSync (timing synchronization with module)
- **Failsafe** → CDCParser (tracks RC frame reception, provides failsafe status)
- **Frame forwarding** → CRSFParser forwards all telemetry to PC via callback
- **Task coordination** → CRSFTask (main loop logic, timing, UART switching, failsafe enforcement)

**Important**: The ESP32 does NOT parse telemetry frames (link stats, GPS, battery, etc.). All telemetry frames are forwarded directly to the PC via CDC for processing. The ESP32 only handles:
1. RC channel transmission (with failsafe protection)
2. Timing synchronization (RADIO_ID frames)
3. Frame forwarding to/from PC
4. Failsafe monitoring and enforcement

This separation makes the codebase easier to maintain, test, and understand.

### Testing Strategy

**Unit Testing with Mock Injection:**
The `CRSFTask` class is designed for testability:
- All dependencies are injected via constructor
- Mock implementations can be substituted for testing
- Tests verify timing behavior, frame scheduling, and state transitions
- Integration tests use real implementations of all supporting classes

**Callback-Based Architecture:**
- `CRSFParser` has no hard dependencies - only callbacks
- `ModuleSync` receives updates via callback from `CRSFParser`
- Easy to test in isolation by setting up test callbacks
- Decoupled design makes unit testing straightforward

**Test Execution:**
```bash
platformio.exe test -e native
```

**Conditional Compilation:**
The production code uses `#ifndef UNIT_TEST` guards to allow test files to inject mock implementations while keeping the production build clean.

---

## Common Pitfalls

### ❌ Don't:
- Call `crsfUart.transmit()` while already transmitting
- Forget to call `crsfUart.switchToRX()` after TX complete
- Block in `loop()` (no `delay()` except in `setup()`)
- Modify channel values from interrupts (single-threaded model)
- Expect RC frames to be sent without CDC RC frames being received

### ✅ Do:
- Always check `isTXComplete()` before switching to RX
- Use `queueOutputFrame()` to replace RC frames with LUA/param frames
- Keep processing fast - `crsfTask.run()` must run frequently
- Use callbacks for cross-module communication (e.g., CDC ↔ Module forwarding)
- Send CDC RC frames regularly (at least every 100ms) to keep failsafe inactive

---

## Debug Tips

1. **Serial output**: All debug goes to USB Serial @ 115200
2. **Statistics**: Type `s` to see frame counts, timing, link quality
3. **Logic analyzer**: GPIO5 shows the actual one-wire traffic
4. **Timing issues**: Check that sync frames are being received
5. **Failsafe status**: Check `cdcParser.isFailsafe()` to see if RC transmission is blocked
6. **RC frame timestamps**: Use `cdcParser.getLastRCFrameTime()` to debug CDC frame reception

---

## Future Extensions

- [x] USB CDC UART bridge for PC → RC channels (implemented via CDCParser)
- [x] Bidirectional CRSF forwarding (device discovery, parameter read/write)
- [x] Telemetry forwarding to PC (all frames forwarded via CDC)
- [x] Failsafe mechanism (stops RC transmission if CDC link lost >100ms)
- [ ] WiFi web interface for configuration and/or wireless PC comms
- [ ] OTA firmware updates

---

---

## Frontend GUI Architecture (tools/feeder/)

The **Feeder GUI** is a PyQt5-based desktop application that serves as the PC-side controller interface. It reads joystick/gamepad input, manages channel mappings, and sends CRSF frames to the ESP32 via USB CDC serial.

### Directory Structure

```
tools/feeder/
├── feeder.py              # Main GUI application and window
├── serial_interface.py    # Serial communication thread
├── crsf_protocol.py       # CRSF protocol implementation
├── device_parameters.py   # Device parameter parsing
├── joystick_handler.py    # Joystick input handling
├── channel_ui.py          # Channel configuration widgets
├── config_manager.py      # Configuration persistence
└── version.py             # Version information
```

### Key Components

#### 1. Main GUI (feeder.py)

**Purpose**: Main application window, UI orchestration, and state management

**Main Class**: `Main(QtWidgets.QWidget)`
- **Responsibilities**:
  - Channel configuration UI (16 channels in scrollable column)
  - Joystick visualization (Mode 1/Mode 2 stick diagrams)
  - Link statistics display (RSSI, LQ, SNR, TX power)
  - Console logging (collapsible debug output)
  - CSV data logging (10Hz telemetry + channels)
  - Device discovery and parameter management
  - Module configuration tab (TX module parameters)

**Key Features**:
- **Dark Theme**: Custom Qt stylesheet with dark title bar on Windows
- **Display Modes**:
  - "Channels" - Shows all 16 channel bars
  - "Mode 1 Sticks + Channels" - Left: CH4/CH2, Right: CH1/CH3
  - "Mode 2 Sticks + Channels" - Left: CH4/CH3, Right: CH1/CH2
- **Toggle Groups**: Channels can be grouped so only one toggle per group is active
- **CSV Logging**: Captures channels + link stats at 10Hz, buffers 600 lines before flush
- **Failsafe Indicator**: Shows ESP32 connection status (red when disconnected)

**Update Loop** (`tick()` @ 60Hz):
- Read joystick input via `JoystickHandler`
- Compute channel values from mappings
- Enforce toggle group exclusivity
- Update visualizers and progress bars
- Send channels to `SerialThread` (decoupled)
- Monitor TX heartbeat (link stats timeout)
- Log to CSV if enabled

#### 2. Serial Interface (serial_interface.py)

**Purpose**: Manages all serial communication with ESP32 in a background thread

**Main Class**: `SerialThread(QtCore.QObject)`
- **Responsibilities**:
  - Serial port connection/reconnection
  - CRSF frame parsing and building
  - Device discovery via ping/info frames
  - Parameter loading (chunked reads with retry)
  - Telemetry extraction (link stats)
  - Channel transmission at 250Hz (4ms period)
  - Auto-discovery state machine

**Signals** (PyQt):
- `telemetry(dict)` - Link statistics updates
- `debug(str)` - Debug/log messages
- `channels_update(list)` - RC channel values (when ESP32 echoes back)
- `connection_status(bool)` - Serial port connected/disconnected
- `device_discovered(int, dict)` - Device info received
- `device_parameters_loaded(int, dict)` - Parameters fully loaded
- `device_parameters_progress(int, int, int)` - Loading progress
- `device_parameter_field_updated(int, int, dict)` - Single field updated

**Channel Transmission**:
- Main GUI calls `send_channels(ch16)` to update the shared buffer
- Serial thread sends frames at fixed 250Hz (4000µs period)
- Uses `perf_counter()` for precise timing
- Non-RC frames (pings, param reads) are queued and sent in place of next RC frame (mimics ESP32 behavior)

**Device Discovery**:
- Auto-triggered when first RX frame is received from TX
- Sends `DEVICE_PING` to both 0xEE and 0xEA addresses
- Waits 500ms after first RX before sending pings (module initialization delay)
- Parses `DEVICE_INFO` responses with DeviceInfo class
- Automatically loads parameters for TX modules (not receivers)
- Supports chunked parameter reads with retry on corruption

**Parameter Loading**:
- Builds descending load queue: `[N, N-1, ..., 2, 1]`
- Sends `PARAMETER_READ` with chunk index
- Assembles multi-chunk responses
- Validates chunks using Lua-style `expectChunksRemain` logic
- Retries up to 3 times on parse/validation failures
- Emits per-field updates for live UI refresh

#### 3. CRSF Protocol (crsf_protocol.py)

**Purpose**: CRSF protocol implementation (constants, CRC, frame building)

**Key Functions**:
- `crc8_d5(data)` - CRC8 calculation with polynomial 0xD5
- `us_to_crsf_val(us)` - Convert 1000-2000µs → 172-1811 (11-bit CRSF)
- `crsf_val_to_us(val)` - Convert 11-bit CRSF → 1000-2000µs
- `build_crsf_channels_frame(ch16)` - Pack 16 channels into 22-byte RC frame
- `build_crsf_frame(ftype, payload)` - Generic frame builder
- `unpack_crsf_channels(payload)` - Unpack 22-byte RC frame to 16 channels

**Protocol Constants**:
- `CRSF_ADDRESS_FLIGHT_CONTROLLER = 0xC8` - FC address (sync byte)
- `CRSF_ADDRESS_CRSF_TRANSMITTER = 0xEE` - TX module
- `CRSF_ADDRESS_RADIO = 0xEA` - Handset
- `CRSF_ADDRESS_ELRS_LUA = 0xEF` - Lua script destination
- Frame types: `0x16` (RC), `0x14` (Link Stats), `0x28` (Ping), `0x29` (Device Info), `0x2B` (Param Entry), `0x2C` (Param Read), `0x2D` (Param Write)

#### 4. Device Parameters (device_parameters.py)

**Purpose**: Parse and validate ELRS device parameter fields

**DeviceParameterParser Class**:
- `parse_field_blob(data)` - Parse raw parameter field data into dictionary
  - Supports field types: 0-8 (numeric), 9 (selection), 11 (folder/info), 12 (string), 13 (command)
  - Extracts: parent, type, hidden, name, values, min/max/default, unit
  - Handles signed/unsigned conversion
  - Reads null-terminated strings and semicolon-separated option lists

- `validate_parsed_field(parsed, field_id)` - Detect corruption
  - Checks for valid field type (0-13)
  - Validates selection fields have values list
  - Ensures field names are printable
  - Returns False if field should be re-read

**DeviceInfo Class**:
- Container for device information (name, serial, hw/sw version, param count)
- `parse_device_info_payload(payload)` - Parse `DEVICE_INFO` CRSF frame
  - Returns tuple: (dst, src, name, serial, hw_ver, sw_ver, n_params, proto_ver)
  - Handles variable-length null-terminated device name

#### 5. Joystick Handler (joystick_handler.py)

**Purpose**: High-frequency joystick reading and channel computation (250Hz+)

**JoystickHandler Class** (extends `QtCore.QObject`):
- **Runs in dedicated background thread for maximum performance**
- **Responsibilities**:
  - Joystick detection with hotplug support (pygame 2.x)
  - Input reading at 250Hz+ (no sleeps in hot path)
  - Channel value computation from joystick input
  - Toggle group enforcement
  - Dual-rate signal emission (250Hz for SerialThread, 60Hz for GUI)

- **Initialization**:
  - Uses pygame for joystick access
  - Supports hotplug events (`JOYDEVICEADDED`, `JOYDEVICEREMOVED`)
  - Periodic scan for older pygame versions (2s interval via QTimer)
  - Thread created but not started until `start_thread()` called

- **High-Frequency Thread** (`_run_loop()` @ 250Hz+):
  - Reads joystick via `read()` method (axes, buttons)
  - Computes all 16 channels via `_compute_channels_from_joystick()`
  - Enforces toggle groups if configured
  - Emits `computed_channels` signal at full speed (250Hz+)
  - Emits `gui_update` signal throttled to 60Hz
  - **NO sleep calls when joystick connected** (maximum performance)
  - 10ms sleep only when joystick disconnected (prevents CPU spin)

- **Auto-reconnection**:
  - Detects `JOYDEVICEADDED` and `JOYDEVICEREMOVED` events
  - Automatically reconnects when joystick is plugged in
  - Handles disconnect gracefully (resets to scanning state)

- **Input Reading** (`read()` method):
  - Returns `(axes, buttons)` tuple
  - Axes: List of floats (-1.0 to 1.0), includes POV hat as last 2 axes
  - Buttons: List of ints (0 or 1)
  - Handles joystick errors during read (auto-disconnect)

**Signals**:
- `status(str)` - Joystick connection status ("Scanning...", joystick name, errors)
- `computed_channels(list, list, list)` - Fresh channels, axes, buttons @ 250Hz+ for SerialThread
- `gui_update(list, list, list)` - Throttled channels, axes, buttons @ 60Hz for GUI display

**Methods**:
- `set_channel_rows(rows)` - Receives ChannelRow objects for computation
- `set_toggle_group_enforcer(enforcer)` - Sets toggle group enforcement function
- `start_thread()` - Starts background thread (call after channel rows set)
- `stop_thread()` - Graceful shutdown with 1s timeout join

#### 6. Channel UI (channel_ui.py)

**Purpose**: UI components for channel configuration and mapping

**ChannelRow Class**:
- Individual channel configuration widget (2-row layout)
- **Top Row**: Name, Source, Index, Map button, Progress bar, Value display
- **Bottom Row**: Min, Mid, Max, Reverse, Toggle, Rotary, Expo controls

**Channel Modes**:
1. **Axis Mode** (`src="axis"`):
   - Maps joystick axis to output range with expo curve
   - Formula: `out = center + (axis_val ^ expo) * (max - center)`
   - Supports reverse (invert)
   - Shows expo slider (1.0 - 5.0)

2. **Button Mode** (`src="button"`):
   - **Direct**: Button press → max, release → min
   - **Toggle**: Button press cycles on/off state
   - **Rotary**: Button press cycles through N stops (3-6)
   - Supports toggle groups (only one active per group)

3. **Multi-Button Mode** (`src="multi"`):
   - Maps multiple buttons to different output values
   - Configurable via dialog (button index → value mapping)
   - Supports default button for initial state
   - Live highlight shows which button is pressed

4. **None** (`src="none"`):
   - Channel unmapped, outputs minimum value

**MultiButtonDialog**:
- Modal dialog for configuring multi-button mappings
- Add/remove button mappings
- Set output value per button
- Mark one button as default
- Live detection of button presses during config

**Mapping Flow**:
1. User clicks "Map" button
2. GUI enters mapping mode (button shows "...")
3. User moves axis or presses button
4. GUI detects largest change (axis: >0.35 delta, button: 0→1 transition)
5. Channel updates to detected source/index
6. 5-second timeout if no input detected

#### 7. Config Manager (config_manager.py)

**Purpose**: Configuration persistence and serial port enumeration

**ConfigManager Class**:
- Loads/saves `calib.json` file
- Default configuration includes:
  - Serial port (platform-dependent: COM1 on Windows, /dev/ttyACM0 on Linux)
  - Display mode ("Channels", "Mode 1 Sticks + Channels", "Mode 2 Sticks + Channels")
  - 16 channel configurations (source, index, min/center/max, reverse, toggle, rotary, expo)

**Functions**:
- `get_available_ports()` - Enumerate serial ports using pyserial
  - Returns list of tuples: `[(port, description), ...]`
  - Sorted alphabetically for consistent ordering

### Data Flow

```
[Joystick]
    ↓ (pygame)
[JoystickHandler.read()] → (axes, buttons)
    ↓
[Main.tick() @ 60Hz]
    ↓
[ChannelRow.compute()] → channel values (16x)
    ↓
[Toggle Group Enforcement]
    ↓
[SerialThread.send_channels()] → updates shared buffer
    ↓
[SerialThread TX Loop @ 250Hz]
    ↓
[build_crsf_channels_frame()] → CRSF frame
    ↓
[Serial.write()] → USB CDC → ESP32
```

```
[ESP32 via USB CDC]
    ↓
[SerialThread RX Loop]
    ↓
[CRSF Frame Parser]
    ↓
├─ LINK_STATISTICS → telemetry signal → Link Stats UI
├─ DEVICE_INFO → device_discovered signal → Module Config Tab
├─ PARAMETER_SETTINGS_ENTRY → field parsing → UI update
└─ RC_CHANNELS_PACKED → channels_update signal → Channel Bars
```

### Module Configuration Tab

**Purpose**: Configure TX module parameters (packet rate, power, etc.)

**UI Generation**:
- Dynamically populated from device parameter fields
- Supports field types:
  - Type 9 (selection): QComboBox with options
  - Type 0-8 (numeric): QComboBox with min/max/step range
  - Type 11 (folder): QGroupBox container
  - Type 12 (string info): QLabel (read-only)
  - Type 13 (command): QPushButton

**Parameter Writes**:
- User changes dropdown → `_on_param_changed(fid, value)`
- Builds `PARAMETER_WRITE` payload: `[device_id, ELRS_LUA, fid, value]`
- Queued to serial thread (sent in place of next RC frame)
- Tracked in `_pending_param_writes` dict to prevent UI overwrite
- Special handling for RF Band changes (triggers Packet Rate reload)

**Parameter Refresh**:
- "Refresh" button triggers full device reload
- Clears existing fields and fetched set
- Rebuilds load queue from N down to 1
- Shows progress bar during reload

### CSV Logging

**Purpose**: Record telemetry and channel data for analysis

**Log Format** (CSV):
- Timestamp (YYYY-MM-DD HH:MM:SS.mmm)
- Channels 1-16 (microseconds, only logged if mapped)
- Link stats: 1RSS, 2RSS, LQ, RSNR, RFMD, TPWR, TRSS, TLQ, TSNR

**Logging Behavior**:
- Checkbox in top bar enables/disables logging
- Creates timestamped file in `logs/` directory
- Buffers up to 600 lines before flushing to disk
- Samples at 10Hz (100ms interval)
- Flushes buffer on app close or logging disabled

### Error Handling

**Serial Disconnects**:
- Auto-reconnect with 500ms retry interval
- Resets discovery state on disconnect
- Clears device info and parameters
- Updates UI to show "Disconnected" status

**Joystick Disconnects**:
- Stops sending RC frames (failsafe on ESP32)
- Shows "Scanning for controller..." status
- Auto-reconnects when joystick plugged back in

**Parameter Read Failures**:
- Validates chunk sequence numbers (Lua-style)
- Retries up to 3 times on corruption
- Logs errors to console
- Marks field as fetched even if failed (prevents infinite loops)

**TX Module Timeout**:
- Monitors link stats reception
- If no stats for >2s, marks TX as disconnected
- Resets discovery state
- Sends new pings when stats resume

### Threading Model

**Main Thread** (Qt Event Loop):
- GUI rendering and event handling
- Joystick input reading (60Hz)
- Channel computation
- UI updates (visualizers, bars, labels)

**Serial Thread** (Background):
- CRSF frame TX/RX at 250Hz
- Device discovery state machine
- Parameter loading queue processing
- Emits Qt signals for cross-thread communication

**Thread Safety**:
- Channel buffer protected by `channels_lock` mutex
- Pending command queue protected by `_pending_cmd_lock` mutex
- Qt signals used for all cross-thread communication
- No shared mutable state between threads

### Build and Distribution

**Dependencies**:
- Python 3.7+
- PyQt5 (GUI framework)
- pygame (joystick input)
- pyserial (serial port access)

**Installation**:
```bash
pip install pyqt5 pygame pyserial
```

**Running**:
```bash
python tools/feeder/feeder.py
```

**PyInstaller Bundle**:
- Creates standalone executable
- Includes icon.ico for Windows

---

## References

- [ExpressLRS GitHub](https://github.com/ExpressLRS/ExpressLRS)
- [EdgeTX GitHub](https://github.com/EdgeTX/edgetx)
- [CRSF Protocol Spec](https://github.com/crsf-wg/crsf/wiki)
- ESP32-S3 Technical Reference Manual (GPIO Matrix, UART)

---

## To Fix

- [ ] Switching the serial port combo often breaks
- [ ] Refresh button on port combo should be enabled all the time otherwise cant discover new / changed ports without a reboot of the GUI
- [x] Packet rate doesn't load in param reads
- [x] Empty param values don't get handled properly (v4.0 pkt params dont load correctly)
- [x] Params gets pull with wrong origin address, and 2x addresses
- [x] Need to handle cmd responses
- [x] CSV logging error: 'Main' object has no attribute 'last_log_time'
- [x] Seem to be getting DEVICE_INFO twice on device ping
- [ ] Triggering wifi causes endless param read loop