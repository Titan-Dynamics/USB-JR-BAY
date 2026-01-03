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

## References

- [ExpressLRS GitHub](https://github.com/ExpressLRS/ExpressLRS)
- [EdgeTX GitHub](https://github.com/EdgeTX/edgetx)
- [CRSF Protocol Spec](https://github.com/crsf-wg/crsf/wiki)
- ESP32-S3 Technical Reference Manual (GPIO Matrix, UART)
