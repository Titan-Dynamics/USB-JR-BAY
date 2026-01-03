/**
 * @file test_cdc_ping_forward.cpp
 * @brief AIT-style test for CDC parser device ping forwarding
 *
 * This test validates that a device ping frame received via CDC
 * is properly parsed and forwarded to the TX UART callback.
 */

#include <unity.h>
#include <stdint.h>
#include <string.h>

// Include Arduino mock before any project files
#include "arduino_mock.h"

// Include production code headers
#include "cdc_parser.h"
#include "rc_channels.h"
#include "crsf_protocol.h"

// Include production code implementations
// (Since test_build_src = no, we need to compile these directly)
#include "../../src/cdc_parser.cpp"
#include "../../src/rc_channels.cpp"
#include "../../src/crsf_protocol.cpp"

// Test state tracking
static bool forwardCallbackCalled = false;
static uint8_t forwardedFrame[CRSF_MAX_FRAME_SIZE];
static uint8_t forwardedFrameLength = 0;

/**
 * Mock callback for frame forwarding to TX UART
 * Simulates the TX UART receiving the forwarded frame
 */
bool mockTxUartForwardCallback(const uint8_t* frame, uint8_t length) {
    forwardCallbackCalled = true;
    forwardedFrameLength = length;
    memcpy(forwardedFrame, frame, length);
    return true;
}

/**
 * Reset test state before each test
 */
void setUp(void) {
    forwardCallbackCalled = false;
    forwardedFrameLength = 0;
    memset(forwardedFrame, 0, sizeof(forwardedFrame));
}

/**
 * Cleanup after each test
 */
void tearDown(void) {
}

/**
 * Test: Device ping frame is correctly forwarded from CDC to TX UART
 *
 * Test procedure:
 * 1. Manually construct a valid device ping frame (no production code)
 * 2. Inject it byte-by-byte into CDC parser (simulating CDC serial reception)
 * 3. Verify that the forward callback was called
 * 4. Verify that the forwarded frame matches the original
 */
void test_device_ping_forwarding(void) {
    // Arrange: Create objects
    RCChannels channels;
    CDCParser cdcParser(channels);

    // Set the forward callback
    cdcParser.setForwardCallback(mockTxUartForwardCallback);

    // Manually build a device ping frame (CRSF PING_DEVICES 0x28)
    // Frame structure: [ADDR][LEN][TYPE][DEST][ORIGIN][CRC]
    uint8_t pingFrame[6];
    pingFrame[0] = 0xC8;  // Sync byte - indicates radio->module command
    pingFrame[1] = 0x04;  // Length: Type(1) + Dest(1) + Origin(1) + CRC(1) = 4 bytes
    pingFrame[2] = 0x28;  // Type: PING_DEVICES
    pingFrame[3] = 0x00;  // Destination: Broadcast (discover all devices)
    pingFrame[4] = 0xEA;  // Origin: Radio/handset address

    // Calculate CRC over bytes [2..4] (Type + Dest + Origin)
    // CRC8 with polynomial 0xD5 - using production CRC function as it's a standard algorithm
    // For reference: the expected CRC for [0x28, 0x00, 0xEA] is 0x54
    uint8_t crc = crsf_crc8(&pingFrame[2], 3);
    pingFrame[5] = crc;

    uint8_t pingFrameLength = 6;

    // Act: Inject the ping frame byte-by-byte into CDC parser
    // This simulates receiving data from the CDC serial port
    for (uint8_t i = 0; i < pingFrameLength; i++) {
        cdcParser.processByte(pingFrame[i]);
    }

    // Assert: Verify that the frame was forwarded
    TEST_ASSERT_TRUE_MESSAGE(forwardCallbackCalled,
        "Forward callback should have been called for PING_DEVICES frame");

    TEST_ASSERT_EQUAL_UINT8_MESSAGE(pingFrameLength, forwardedFrameLength,
        "Forwarded frame length should match original");

    TEST_ASSERT_EQUAL_HEX8_ARRAY_MESSAGE(pingFrame, forwardedFrame, pingFrameLength,
        "Forwarded frame content should match original ping frame");

    // Verify statistics
    TEST_ASSERT_EQUAL_UINT32(1, cdcParser.getFramesReceived());
    TEST_ASSERT_EQUAL_UINT32(1, cdcParser.getForwardedFrames());
    TEST_ASSERT_EQUAL_UINT32(0, cdcParser.getCRCErrors());
    TEST_ASSERT_EQUAL_UINT32(0, cdcParser.getRCFramesReceived());
}

/**
 * Test: Multiple frame types are correctly forwarded
 */
void test_multiple_forwardable_frames(void) {
    // Arrange
    RCChannels channels;
    CDCParser cdcParser(channels);
    cdcParser.setForwardCallback(mockTxUartForwardCallback);

    // Manually build a ping frame
    // Frame: [ADDR][LEN][TYPE][DEST][ORIGIN][CRC]
    uint8_t pingFrame[6];
    pingFrame[0] = 0xC8;  // Sync byte
    pingFrame[1] = 0x04;  // Length
    pingFrame[2] = 0x28;  // Type: PING_DEVICES
    pingFrame[3] = 0x00;  // Destination: Broadcast
    pingFrame[4] = 0xEA;  // Origin: Radio
    pingFrame[5] = crsf_crc8(&pingFrame[2], 3);  // CRC

    // Inject ping frame
    for (uint8_t i = 0; i < 6; i++) {
        cdcParser.processByte(pingFrame[i]);
    }

    TEST_ASSERT_EQUAL_UINT32(1, cdcParser.getForwardedFrames());

    // Manually build a parameter read frame (PARAMETER_READ 0x2C)
    // Frame: [ADDR][LEN][TYPE][DEST][ORIGIN][PARAM_IDX][CHUNK][CRC]
    uint8_t paramFrame[8];
    paramFrame[0] = 0xC8;  // Sync byte
    paramFrame[1] = 0x06;  // Length: Type(1) + Dest(1) + Origin(1) + ParamIdx(1) + Chunk(1) + CRC(1) = 6
    paramFrame[2] = 0x2C;  // Type: PARAMETER_READ
    paramFrame[3] = 0xEE;  // Destination: Module
    paramFrame[4] = 0xEA;  // Origin: Radio
    paramFrame[5] = 0x05;  // Parameter index: 5
    paramFrame[6] = 0x00;  // Chunk number: 0 (first chunk)
    paramFrame[7] = crsf_crc8(&paramFrame[2], 5);  // CRC over 5 bytes

    forwardCallbackCalled = false; // Reset for next frame

    // Inject parameter read frame
    for (uint8_t i = 0; i < 8; i++) {
        cdcParser.processByte(paramFrame[i]);
    }

    TEST_ASSERT_TRUE(forwardCallbackCalled);
    TEST_ASSERT_EQUAL_UINT32(2, cdcParser.getForwardedFrames());
    TEST_ASSERT_EQUAL_UINT32(2, cdcParser.getFramesReceived());
}

/**
 * Test: Invalid CRC causes frame to be rejected
 */
void test_invalid_crc_rejected(void) {
    // Arrange
    RCChannels channels;
    CDCParser cdcParser(channels);
    cdcParser.setForwardCallback(mockTxUartForwardCallback);

    // Manually build a ping frame with INTENTIONALLY WRONG CRC
    uint8_t pingFrame[6];
    pingFrame[0] = 0xC8;  // Sync byte
    pingFrame[1] = 0x04;  // Length
    pingFrame[2] = 0x28;  // Type: PING_DEVICES
    pingFrame[3] = 0x00;  // Destination: Broadcast
    pingFrame[4] = 0xEA;  // Origin: Radio
    // Correct CRC would be: crsf_crc8(&pingFrame[2], 3) = 0x54
    pingFrame[5] = 0xAB;  // WRONG CRC - intentionally corrupted

    // Act: Inject corrupted frame
    for (uint8_t i = 0; i < 6; i++) {
        cdcParser.processByte(pingFrame[i]);
    }

    // Assert: Frame should NOT be forwarded
    TEST_ASSERT_FALSE_MESSAGE(forwardCallbackCalled,
        "Forward callback should NOT be called for frame with invalid CRC");

    TEST_ASSERT_EQUAL_UINT32(0, cdcParser.getForwardedFrames());
    TEST_ASSERT_EQUAL_UINT32(1, cdcParser.getCRCErrors());
}

/**
 * Test: RC frame updates channels instead of being forwarded
 */
void test_rc_frame_updates_channels(void) {
    // Arrange
    RCChannels channels;
    CDCParser cdcParser(channels);
    cdcParser.setForwardCallback(mockTxUartForwardCallback);

    // Manually build an RC channels frame (CRSF_FRAMETYPE_RC_CHANNELS 0x16)
    // Frame: [ADDR][LEN][TYPE][22 bytes packed channels][CRC]
    // Total: 1 + 1 + 1 + 22 + 1 = 26 bytes

    // Define test channel values (11-bit each, range 0-1984)
    uint16_t testChannels[16];
    for (int i = 0; i < 16; i++) {
        testChannels[i] = CRSF_CHANNEL_CENTER + i * 10;  // 992, 1002, 1012, ...
    }

    uint8_t rcFrame[26];
    rcFrame[0] = 0xEE;  // Destination: Module
    rcFrame[1] = 0x18;  // Length: Type(1) + Payload(22) + CRC(1) = 24 bytes (0x18)
    rcFrame[2] = 0x16;  // Type: RC_CHANNELS

    // Pack 16 channels (11 bits each) into 22 bytes
    // This is a manual bit-packing operation - 16 * 11 bits = 176 bits = 22 bytes
    crsf_pack_channels(testChannels, &rcFrame[3]);  // Use helper for complex bit packing

    // Calculate CRC over Type + Payload (23 bytes)
    rcFrame[25] = crsf_crc8(&rcFrame[2], 23);

    // Act: Inject RC frame
    for (uint8_t i = 0; i < 26; i++) {
        cdcParser.processByte(rcFrame[i]);
    }

    // Assert: RC frames are NOT forwarded, they update local channels
    TEST_ASSERT_FALSE_MESSAGE(forwardCallbackCalled,
        "RC frames should NOT be forwarded, they update local channel values");

    TEST_ASSERT_EQUAL_UINT32(1, cdcParser.getRCFramesReceived());
    TEST_ASSERT_EQUAL_UINT32(0, cdcParser.getForwardedFrames());

    // Verify channels were updated
    for (int i = 0; i < 16; i++) {
        TEST_ASSERT_EQUAL_UINT16(testChannels[i], channels.getChannelCRSF(i));
    }
}

/**
 * Test: Failsafe is active when no RC frames have been received
 */
void test_failsafe_active_on_startup(void) {
    RCChannels channels;
    CDCParser cdcParser(channels);
    
    TEST_ASSERT_TRUE_MESSAGE(cdcParser.isFailsafe(),
        "Failsafe should be active when no RC frames have been received");
    
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, cdcParser.getLastRCFrameTime(),
        "Last RC frame time should be 0 when no frames received");
}

/**
 * Test: Failsafe clears when first RC frame is received
 */
void test_failsafe_clears_on_first_rc_frame(void) {
    RCChannels channels;
    CDCParser cdcParser(channels);
    
    arduino_mock_set_time_us(1000);
    
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
    
    TEST_ASSERT_FALSE_MESSAGE(cdcParser.isFailsafe(),
        "Failsafe should be inactive after receiving an RC frame");
    
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(1000, cdcParser.getLastRCFrameTime(),
        "Last RC frame time should be updated");
}

/**
 * Test: Failsafe activates after timeout expires
 */
void test_failsafe_activates_after_timeout(void) {
    RCChannels channels;
    CDCParser cdcParser(channels);
    
    uint16_t testChannels[16];
    for (int i = 0; i < 16; i++) {
        testChannels[i] = CRSF_CHANNEL_CENTER;
    }
    
    arduino_mock_set_time_us(1000);
    
    uint8_t rcFrame[26];
    rcFrame[0] = 0xEE;
    rcFrame[1] = 0x18;
    rcFrame[2] = 0x16;
    crsf_pack_channels(testChannels, &rcFrame[3]);
    rcFrame[25] = crsf_crc8(&rcFrame[2], 23);
    
    for (uint8_t i = 0; i < 26; i++) {
        cdcParser.processByte(rcFrame[i]);
    }
    
    TEST_ASSERT_FALSE(cdcParser.isFailsafe());
    
    arduino_mock_set_time_us(1000 + 100001);
    
    TEST_ASSERT_TRUE_MESSAGE(cdcParser.isFailsafe(),
        "Failsafe should activate after >100ms without RC frames");
}

/**
 * Test: Non-RC frames don't affect failsafe status
 */
void test_non_rc_frames_dont_clear_failsafe(void) {
    RCChannels channels;
    CDCParser cdcParser(channels);
    
    arduino_mock_set_time_us(1000);
    
    uint8_t pingFrame[6];
    pingFrame[0] = 0xC8;
    pingFrame[1] = 0x04;
    pingFrame[2] = 0x28;
    pingFrame[3] = 0x00;
    pingFrame[4] = 0xEA;
    pingFrame[5] = crsf_crc8(&pingFrame[2], 3);
    
    for (uint8_t i = 0; i < 6; i++) {
        cdcParser.processByte(pingFrame[i]);
    }
    
    TEST_ASSERT_TRUE_MESSAGE(cdcParser.isFailsafe(),
        "Non-RC frames should not clear failsafe");
    
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(0, cdcParser.getLastRCFrameTime(),
        "Last RC frame time should remain 0");
}

/**
 * Test runner main function
 */
int main(int argc, char **argv) {
    UNITY_BEGIN();

    RUN_TEST(test_device_ping_forwarding);
    RUN_TEST(test_multiple_forwardable_frames);
    RUN_TEST(test_invalid_crc_rejected);
    RUN_TEST(test_rc_frame_updates_channels);
    RUN_TEST(test_failsafe_active_on_startup);
    RUN_TEST(test_failsafe_clears_on_first_rc_frame);
    RUN_TEST(test_failsafe_activates_after_timeout);
    RUN_TEST(test_non_rc_frames_dont_clear_failsafe);

    return UNITY_END();
}

