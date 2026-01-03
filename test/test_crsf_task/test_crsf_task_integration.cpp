/**
 * @file test_crsf_task_integration.cpp
 * @brief Integration tests for CRSFTask class
 *
 * Tests the complete CRSFTask behavior including:
 * - RC frame transmission timing
 * - Module sync frame processing and frequency updates
 * - UART mode switching (TX/RX transitions)
 * - Frame parsing from TX UART
 * - Edge cases and state transitions
 */

#include <unity.h>
#include <stdint.h>
#include <string.h>

// Include mocks before production code
#include "Arduino.h"
#include "uart_mock.h"
#include "timing_mock.h"

// Include production code headers (exclude crsf_uart.h since we use mock)
#include "crsf_protocol.h"
#include "module_sync.h"
#include "rc_channels.h"
#include "crsf_parser.h"
#include "cdc_parser.h"

// Include production code implementations
#include "../../src/crsf_protocol.cpp"
#include "../../src/module_sync.cpp"
#include "../../src/rc_channels.cpp"
#include "../../src/crsf_parser.cpp"
#include "../../src/cdc_parser.cpp"

// Forward declare callback types
typedef void (*CRSFTimingSyncCallback)(int32_t refreshRateUs, int32_t inputLagUs);

// Forward declare the CRSFTask class (we'll include implementation after)
class CRSFTask {
public:
    CRSFTask(CRSFUart& uart, CRSFParser& parser, CDCParser& cdcParser,
             ModuleSync& moduleSync, RCChannels& channels);
    void run();
    bool queueOutputFrame(const uint8_t* data, uint8_t length);
    uint32_t getRCFramesSent() const { return rcFramesSent; }
    uint32_t getLastRCFrameTime() const { return lastRCFrameTime; }

private:
    bool isTimeToSendRC() const;
    void sendRCFrame();

    CRSFUart& uart;
    CRSFParser& parser;
    CDCParser& cdcParser;
    ModuleSync& moduleSync;
    RCChannels& channels;
    uint32_t lastRCFrameTime;
    uint32_t rcFramesSent;
    uint8_t outputFrameBuffer[CRSF_MAX_FRAME_SIZE];
    uint8_t outputFrameLength;
    bool outputFramePending;
};

// Now include the production implementation
#include "../../src/crsf_task.cpp"

// ============================================================================
// Test Globals - Mock Objects
// ============================================================================

static CRSFUart crsfUart;
static ModuleSync moduleSync;
static RCChannels rcChannels;
static CRSFParser parser;
static CDCParser cdcParser(rcChannels);

// Test instance - created in setUp()
static CRSFTask* crsfTask = nullptr;

// Helper functions for callbacks
void forwardModuleToCDC(const uint8_t* frame, uint8_t length) {
    if (Serial) {
        Serial.write(frame, length);
    }
}

bool forwardCDCToModule(const uint8_t* frame, uint8_t length) {
    if (crsfTask) {
        return crsfTask->queueOutputFrame(frame, length);
    }
    return false;
}

// ============================================================================
// Test Setup and Teardown
// ============================================================================

/**
 * Helper to inject RC frame into CDC parser to clear failsafe
 */
void clearFailsafe() {
    uint16_t testChannels[16];
    for (int i = 0; i < 16; i++) {
        testChannels[i] = CRSF_CHANNEL_CENTER;
    }
    
    uint8_t rcFrame[26];
    rcFrame[0] = 0xEE;
    rcFrame[1] = 0x18;
    rcFrame[2] = 0x16;
    crsf_pack_channels(testChannels, &rcFrame[3]);
    rcFrame[25] = crsf_crc8(&rcFrame[2], 23);
    
    for (uint8_t i = 0; i < 26; i++) {
        cdcParser.processByte(rcFrame[i]);
    }
}

void setUp(void) {
    // Reset all state
    timingMock.reset();
    
    // Set initial time before clearing failsafe
    timingMock.setMicros(1000);

    // Reset UART
    crsfUart.~CRSFUart();
    new (&crsfUart) CRSFUart();
    crsfUart.begin(5, 1870000);

    moduleSync.~ModuleSync();
    new (&moduleSync) ModuleSync();

    rcChannels.~RCChannels();
    new (&rcChannels) RCChannels();

    // Recreate parsers using placement new
    parser.~CRSFParser();
    new (&parser) CRSFParser();

    cdcParser.~CDCParser();
    new (&cdcParser) CDCParser(rcChannels);

    // Setup callbacks
    cdcParser.setForwardCallback(forwardCDCToModule);
    parser.setToCDCCallback(forwardModuleToCDC);
    parser.setTimingSyncCallback([](int32_t refreshRateUs, int32_t inputLagUs) {
        moduleSync.updateTiming(refreshRateUs, inputLagUs);
    });

    // Create CRSFTask instance with mock objects
    if (crsfTask) {
        delete crsfTask;
    }
    crsfTask = new CRSFTask(crsfUart, parser, cdcParser, moduleSync, rcChannels);

    Serial.begin(115200);
    Serial.resetStats();
    
    // Clear failsafe for tests that expect RC transmission
    clearFailsafe();
}

void tearDown(void) {
    if (crsfTask) {
        delete crsfTask;
        crsfTask = nullptr;
    }
}

// ============================================================================
// Test Cases
// ============================================================================

/**
 * Test: RC frames are sent at correct default frequency (4ms = 250Hz)
 *
 * Verifies that without any sync frames, RC frames are sent every 4000us
 */
void test_rc_frames_sent_at_default_frequency(void) {
    // Arrange
    setUp();

    // Act & Assert: Run crsfTask multiple times and verify timing
    for (int i = 0; i < 5; i++) {
        uint32_t expectedFramesSent = i + 1;

        // Advance time by 4000us (one period)
        timingMock.advanceMicros(CRSF_DEFAULT_PERIOD_US);

        // Run crsfTask
        crsfTask->run();

        // Verify frame was sent
        TEST_ASSERT_EQUAL_UINT32(expectedFramesSent, crsfTask->getRCFramesSent());
        TEST_ASSERT_EQUAL_UINT32(expectedFramesSent, crsfUart.getTxCallCount());

        // Verify frame type is RC_CHANNELS (0x16)
        const uint8_t* frame = crsfUart.getLastTxFrame();
        TEST_ASSERT_EQUAL_HEX8(0xEE, frame[0]);  // Destination: Module
        TEST_ASSERT_EQUAL_HEX8(0x16, frame[2]);  // Type: RC_CHANNELS

        // Wait for TX to complete and switch to RX
        timingMock.advanceMicros(200);  // Simulate transmission time
        crsfTask->run();  // Process TX completion
        TEST_ASSERT_EQUAL_UINT32(expectedFramesSent, crsfUart.getSwitchToRXCallCount());
    }
}

/**
 * Test: RC frequency is updated after receiving RADIO_ID sync frame
 *
 * Verifies that after receiving a RADIO_ID frame with timing info,
 * the RC frame period is adjusted accordingly
 */
void test_rc_frequency_updated_by_sync_frame(void) {
    // Arrange
    setUp();

    // Send first RC frame at default period (4000us)
    timingMock.advanceMicros(CRSF_DEFAULT_PERIOD_US);
    crsfTask->run();
    TEST_ASSERT_EQUAL_UINT32(1, crsfTask->getRCFramesSent());

    // Wait for TX complete and switch to RX
    timingMock.advanceMicros(200);
    crsfTask->run();

    // Manually build a RADIO_ID timing frame from the module
    uint8_t syncFrame[11];
    
    // Payload is: [DEST][ORIG][SUBCMD][RATE(4 bytes)][OFFSET(4 bytes)] = 11 bytes
    syncFrame[0] = 0xEA;  // Address: Radio
    syncFrame[1] = 0x0D;  // Length: Type(1) + Payload(11) + CRC(1) = 13 (0x0D)
    syncFrame[2] = 0x3A;  // Type: RADIO_ID
    // Payload starts at index 3
    syncFrame[3] = 0xEA;  // Dest: Radio
    syncFrame[4] = 0xEE;  // Origin: Module
    syncFrame[5] = 0x10;  // Subcommand: Timing (0x10)
    // Refresh rate: 2000us * 10 = 20000 (in 100ns units) = 0x4E20 (32-bit big-endian)
    syncFrame[6] = 0x00;
    syncFrame[7] = 0x00;
    syncFrame[8] = 0x4E;  // 0x4E20 = 20000 (100ns units) = 2000us
    syncFrame[9] = 0x20;
    // Offset: 0 (32-bit big-endian)
    syncFrame[10] = 0x00;
    syncFrame[11] = 0x00;
    syncFrame[12] = 0x00;
    syncFrame[13] = 0x00;
    // CRC over Type + Payload (12 bytes)
    syncFrame[14] = crsf_crc8(&syncFrame[2], 12);

    // Inject sync frame into UART RX buffer
    crsfUart.injectRxData(syncFrame, 15);

    // Act: Process the sync frame
    crsfTask->run();  // This should parse the sync frame

    // Assert: Verify sync was processed
    TEST_ASSERT_TRUE(moduleSync.isValid());
    TEST_ASSERT_EQUAL_INT32(2000, moduleSync.getRefreshRate());

    // Now verify next RC frame is sent at 2000us interval (not 4000us)
    timingMock.advanceMicros(2000);  // Advance by new period
    crsfTask->run();
    TEST_ASSERT_EQUAL_UINT32(2, crsfTask->getRCFramesSent());  // Second frame sent

    // Verify that if we only advance 1999us, no frame is sent yet
    timingMock.advanceMicros(200);  // TX complete
    crsfTask->run();
    timingMock.advanceMicros(1799);  // Total: 1999us since last frame
    crsfTask->run();
    TEST_ASSERT_EQUAL_UINT32(2, crsfTask->getRCFramesSent());  // Still only 2 frames

    // Now advance 1 more us to hit 2000us
    timingMock.advanceMicros(1);
    crsfTask->run();
    TEST_ASSERT_EQUAL_UINT32(3, crsfTask->getRCFramesSent());  // Third frame sent
}

/**
 * Test: UART correctly switches from TX to RX after transmission complete
 *
 * Verifies the state machine transitions properly:
 * 1. Start transmitting (TX mode)
 * 2. Wait for TX complete
 * 3. Automatically switch to RX mode
 */
void test_uart_switches_from_tx_to_rx_after_complete(void) {
    // Arrange
    setUp();

    // Act: Trigger RC frame send
    timingMock.advanceMicros(CRSF_DEFAULT_PERIOD_US);
    crsfTask->run();

    // Assert: Should be transmitting
    TEST_ASSERT_TRUE(crsfUart.isTransmitting());
    TEST_ASSERT_EQUAL_UINT32(1, crsfUart.getTxCallCount());
    TEST_ASSERT_EQUAL_UINT32(0, crsfUart.getSwitchToRXCallCount());

    // Act: Run crsfTask before TX is complete
    timingMock.advanceMicros(50);  // Not enough time for TX to complete
    crsfTask->run();

    // Assert: Still transmitting, no switch to RX yet
    TEST_ASSERT_TRUE(crsfUart.isTransmitting());
    TEST_ASSERT_EQUAL_UINT32(0, crsfUart.getSwitchToRXCallCount());

    // Act: Advance time enough for TX to complete
    // 26 bytes * 10 bits/byte * 1000000 / 1870000 baud ≈ 139us
    timingMock.advanceMicros(150);  // Plenty of time for TX complete
    crsfTask->run();

    // Assert: Should have switched to RX
    TEST_ASSERT_FALSE(crsfUart.isTransmitting());
    TEST_ASSERT_EQUAL_UINT32(1, crsfUart.getSwitchToRXCallCount());
}

/**
 * Test: Frames from TX UART are parsed correctly during RX periods
 *
 * Verifies that when not transmitting, incoming frames are processed
 */
void test_frames_from_module_are_parsed(void) {
    // Arrange
    setUp();

    // Send an RC frame and wait for TX complete
    timingMock.advanceMicros(CRSF_DEFAULT_PERIOD_US);
    crsfTask->run();
    timingMock.advanceMicros(200);
    crsfTask->run();  // Switch to RX

    TEST_ASSERT_FALSE(crsfUart.isTransmitting());

    // Manually build a LINK_STATISTICS frame from module
    // Frame: [ADDR][LEN][TYPE][UPLINK_RSSI_ANT1][UPLINK_RSSI_ANT2][UPLINK_LQ][UPLINK_SNR]
    //        [ACTIVE_ANT][RF_MODE][UPLINK_TX_POWER][DOWNLINK_RSSI][DOWNLINK_LQ][DOWNLINK_SNR][CRC]
    // Note: RSSI payload values are NEGATED by parser, so payload should contain -(dBm value)
    // To get -120 dBm, payload should have 120 (parser negates it: -(120) = -120)
    uint8_t linkStatsFrame[14];
    linkStatsFrame[0] = 0xEA;  // Destination: Radio
    linkStatsFrame[1] = 0x0C;  // Length: Type(1) + Payload(10) + CRC(1) = 12 (0x0C)
    linkStatsFrame[2] = 0x14;  // Type: LINK_STATISTICS
    linkStatsFrame[3] = 120;   // Uplink RSSI Ant1
    linkStatsFrame[4] = 118;   // Uplink RSSI Ant2
    linkStatsFrame[5] = 100;   // Uplink LQ (100%)
    linkStatsFrame[6] = 10;    // Uplink SNR
    linkStatsFrame[7] = 0;     // Active antenna
    linkStatsFrame[8] = 2;     // RF Mode
    linkStatsFrame[9] = 1;     // Uplink TX power (enum based)
    linkStatsFrame[10] = 115;  // Downlink RSSI
    linkStatsFrame[11] = 98;   // Downlink LQ
    linkStatsFrame[12] = 8;    // Downlink SNR
    linkStatsFrame[13] = crsf_crc8(&linkStatsFrame[2], 11);  // CRC

    // Inject link stats frame into RX buffer
    crsfUart.injectRxData(linkStatsFrame, 14);

    // Act: Process incoming data
    crsfTask->run();

    // Assert: Verify frame was parsed and forwarded to CDC
    TEST_ASSERT_EQUAL_UINT32(1, parser.getFramesReceived());
    TEST_ASSERT_EQUAL_UINT32(0, parser.getCRCErrors());
    
    // Verify frame was forwarded to CDC (Serial.write should be called)
    TEST_ASSERT_EQUAL_UINT32(1, Serial.getWriteCallCount());
}

/**
 * Test: Queued frames (like ping) take priority over RC frames
 *
 * Verifies that when a frame is queued, it's sent instead of the RC frame
 */
void test_queued_frames_sent_instead_of_rc(void) {
    // Arrange
    setUp();

    // Build a ping frame to queue
    uint8_t pingFrame[6];
    pingFrame[0] = 0xC8;
    pingFrame[1] = 0x04;
    pingFrame[2] = 0x28;  // PING_DEVICES
    pingFrame[3] = 0x00;
    pingFrame[4] = 0xEA;
    pingFrame[5] = crsf_crc8(&pingFrame[2], 3);

    // Queue the ping frame
    bool queued = crsfTask->queueOutputFrame(pingFrame, 6);
    TEST_ASSERT_TRUE(queued);

    // Act: Wait for RC period and run crsfTask
    timingMock.advanceMicros(CRSF_DEFAULT_PERIOD_US);
    crsfTask->run();

    // Assert: Ping frame should have been sent (not RC frame)
    TEST_ASSERT_EQUAL_UINT32(0, crsfTask->getRCFramesSent());  // No RC frames sent yet
    TEST_ASSERT_EQUAL_UINT32(1, crsfUart.getTxCallCount());  // But TX was called

    // Verify transmitted frame is the ping frame
    const uint8_t* txFrame = crsfUart.getLastTxFrame();
    TEST_ASSERT_EQUAL_UINT8(6, crsfUart.getLastTxLength());
    TEST_ASSERT_EQUAL_HEX8(0x28, txFrame[2]);  // PING_DEVICES type

    // Now verify next period sends RC frame
    timingMock.advanceMicros(200);  // TX complete
    crsfTask->run();
    timingMock.advanceMicros(CRSF_DEFAULT_PERIOD_US);
    crsfTask->run();

    TEST_ASSERT_EQUAL_UINT32(1, crsfTask->getRCFramesSent());  // Now RC frame is sent
    const uint8_t* rcFrame = crsfUart.getLastTxFrame();
    TEST_ASSERT_EQUAL_HEX8(0x16, rcFrame[2]);  // RC_CHANNELS type
}

/**
 * Test: No frames are sent while still transmitting
 *
 * Edge case: ensures we don't try to send multiple frames simultaneously
 */
void test_no_frame_sent_while_transmitting(void) {
    // Arrange
    setUp();

    // Start transmitting
    timingMock.advanceMicros(CRSF_DEFAULT_PERIOD_US);
    crsfTask->run();
    TEST_ASSERT_TRUE(crsfUart.isTransmitting());
    uint32_t txCount1 = crsfUart.getTxCallCount();

    // Act: Advance time but don't complete TX yet
    uint32_t txCountBefore = crsfUart.getTxCallCount();

    // Advance enough time that period has passed, but TX not complete yet
    timingMock.advanceMicros(100);  // Not enough for TX to complete
    crsfTask->run();  // This should NOT start a new transmission

    // Assert: No new transmission should have started
    uint32_t txCountAfter = crsfUart.getTxCallCount();
    TEST_ASSERT_EQUAL_UINT32(txCountBefore, txCountAfter);  // TX count unchanged
    TEST_ASSERT_TRUE(crsfUart.isTransmitting());  // Still transmitting
}

/**
 * Test: Multiple sync frames update frequency correctly
 *
 * Edge case: verify that multiple sync frames properly update timing
 */
void test_multiple_sync_frames_update_frequency(void) {
    // Arrange
    setUp();

    // Helper function to build sync frame
    // NOTE: refreshRateUs is in microseconds, but must be converted to 100ns units
    auto buildSyncFrame = [](uint8_t* frame, uint32_t refreshRateUs) {
        uint32_t rate100ns = refreshRateUs * 10;  // Convert us to 100ns units
        frame[0] = 0xEA;
        frame[1] = 0x0D;  // Length
        frame[2] = 0x3A;  // RADIO_ID
        frame[3] = 0xEA;  // Dest
        frame[4] = 0xEE;  // Origin
        frame[5] = 0x10;  // Subcmd
        frame[6] = (rate100ns >> 24) & 0xFF;
        frame[7] = (rate100ns >> 16) & 0xFF;
        frame[8] = (rate100ns >> 8) & 0xFF;
        frame[9] = rate100ns & 0xFF;
        frame[10] = 0x00;  // Offset
        frame[11] = 0x00;
        frame[12] = 0x00;
        frame[13] = 0x00;
        frame[14] = crsf_crc8(&frame[2], 12);
    };

    // Send first sync: 3000us (333Hz)
    uint8_t sync1[15];
    buildSyncFrame(sync1, 3000);
    crsfUart.injectRxData(sync1, 15);
    crsfTask->run();
    TEST_ASSERT_EQUAL_INT32(3000, moduleSync.getRefreshRate());

    // Send RC frame at 3000us
    timingMock.advanceMicros(3000);
    crsfTask->run();
    TEST_ASSERT_EQUAL_UINT32(1, crsfTask->getRCFramesSent());

    // TX complete and RX
    timingMock.advanceMicros(200);
    crsfTask->run();

    // Send second sync: 2500us (400Hz)
    uint8_t sync2[15];
    buildSyncFrame(sync2, 2500);
    crsfUart.injectRxData(sync2, 15);
    crsfTask->run();
    TEST_ASSERT_EQUAL_INT32(2500, moduleSync.getRefreshRate());

    // Verify next frame sent at 2500us (not 3000us)
    timingMock.advanceMicros(2500);
    crsfTask->run();
    TEST_ASSERT_EQUAL_UINT32(2, crsfTask->getRCFramesSent());
}

/**
 * Helper to inject RC frame into CDC parser
 */
void injectCDCRCFrame(const uint16_t* channels) {
    uint8_t rcFrame[26];
    rcFrame[0] = 0xEE;
    rcFrame[1] = 0x18;
    rcFrame[2] = 0x16;
    crsf_pack_channels(channels, &rcFrame[3]);
    rcFrame[25] = crsf_crc8(&rcFrame[2], 23);
    
    for (uint8_t i = 0; i < 26; i++) {
        cdcParser.processByte(rcFrame[i]);
    }
}

/**
 * Test: No RC frames sent when failsafe is active
 */
void test_no_rc_frames_when_failsafe_active(void) {
    // Reset and don't clear failsafe
    tearDown();
    timingMock.reset();
    crsfUart.~CRSFUart();
    new (&crsfUart) CRSFUart();
    crsfUart.begin(5, 1870000);
    cdcParser.~CDCParser();
    new (&cdcParser) CDCParser(rcChannels);
    crsfTask = new CRSFTask(crsfUart, parser, cdcParser, moduleSync, rcChannels);
    
    TEST_ASSERT_TRUE(cdcParser.isFailsafe());
    
    timingMock.setMicros(0);
    crsfTask->run();
    
    timingMock.setMicros(5000);
    crsfTask->run();
    
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, crsfTask->getRCFramesSent(),
        "No RC frames should be sent while in failsafe");
}

/**
 * Test: RC frames start after first CDC frame
 */
void test_rc_frames_start_after_cdc_frame(void) {
    // Reset and don't clear failsafe initially
    tearDown();
    timingMock.reset();
    timingMock.setMicros(1000);  // Set initial time
    
    crsfUart.~CRSFUart();
    new (&crsfUart) CRSFUart();
    crsfUart.begin(5, 1870000);
    cdcParser.~CDCParser();
    new (&cdcParser) CDCParser(rcChannels);
    crsfTask = new CRSFTask(crsfUart, parser, cdcParser, moduleSync, rcChannels);
    
    uint16_t testChannels[16];
    for (int i = 0; i < 16; i++) {
        testChannels[i] = CRSF_CHANNEL_CENTER;
    }
    
    injectCDCRCFrame(testChannels);
    
    TEST_ASSERT_FALSE(cdcParser.isFailsafe());
    
    // Advance time past first period
    timingMock.advanceMicros(CRSF_DEFAULT_PERIOD_US);
    crsfTask->run();
    TEST_ASSERT_EQUAL_UINT32(1, crsfTask->getRCFramesSent());
}

/**
 * Test: RC transmission stops when failsafe activates
 */
void test_rc_transmission_stops_on_failsafe(void) {
    // This test uses the default setUp (with failsafe cleared)
    uint16_t testChannels[16];
    for (int i = 0; i < 16; i++) {
        testChannels[i] = CRSF_CHANNEL_CENTER;
    }
    
    // Advance time and send first frame
    timingMock.advanceMicros(CRSF_DEFAULT_PERIOD_US);
    crsfTask->run();
    TEST_ASSERT_EQUAL_UINT32(1, crsfTask->getRCFramesSent());
    
    // Advance past failsafe timeout (>100ms from initial clearFailsafe at t=1000)
    timingMock.setMicros(1000 + 100001);
    TEST_ASSERT_TRUE(cdcParser.isFailsafe());
    
    // Advance past next RC period
    timingMock.advanceMicros(CRSF_DEFAULT_PERIOD_US);
    crsfTask->run();
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1, crsfTask->getRCFramesSent(),
        "RC frames should stop when failsafe activates");
}

// ============================================================================
// Test Runner
// ============================================================================

int main(int argc, char **argv) {
    UNITY_BEGIN();

    RUN_TEST(test_rc_frames_sent_at_default_frequency);
    RUN_TEST(test_rc_frequency_updated_by_sync_frame);
    RUN_TEST(test_uart_switches_from_tx_to_rx_after_complete);
    RUN_TEST(test_frames_from_module_are_parsed);
    RUN_TEST(test_queued_frames_sent_instead_of_rc);
    RUN_TEST(test_no_frame_sent_while_transmitting);
    RUN_TEST(test_multiple_sync_frames_update_frequency);
    RUN_TEST(test_no_rc_frames_when_failsafe_active);
    RUN_TEST(test_rc_frames_start_after_cdc_frame);
    RUN_TEST(test_rc_transmission_stops_on_failsafe);

    return UNITY_END();
}
