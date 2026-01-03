/**
 * @file cdc_parser.h
 * @brief CDC CRSF frame parser for PC-to-ESP32 communication
 *
 * Parses CRSF frames received from the PC via USB CDC serial port.
 * - RC frames (0x16) update channel values
 * - Ping frames (0x28) and other commands are forwarded to TX module
 */

#pragma once

#include <stdint.h>
#include "crsf_protocol.h"
#include "rc_channels.h"

/**
 * Callback type for frames that need to be forwarded to the TX module
 * @param frame Complete CRSF frame including address, length, type, payload, CRC
 * @param length Total frame length
 * @return true if frame was queued successfully
 */
typedef bool (*CDCForwardCallback)(const uint8_t* frame, uint8_t length);

class CDCParser {
public:
    static constexpr uint32_t FAILSAFE_TIMEOUT_US = 100000; // 100ms

    CDCParser(RCChannels& channels);

    /**
     * Process one byte from the CDC serial port
     * Call repeatedly as bytes arrive from Serial
     * @param byte Received byte
     */
    void processByte(uint8_t byte);

    /**
     * Set callback for frames that should be forwarded to TX module
     * @param callback Function to call when a frame needs forwarding
     */
    void setForwardCallback(CDCForwardCallback callback) { forwardCallback = callback; }

    /**
     * Check if failsafe is active (no RC frame received recently)
     * @return true if in failsafe mode (no RC transmission should occur)
     */
    bool isFailsafe() const;

    /**
     * Get the timestamp of the last received RC frame
     * @return Microsecond timestamp of last RC frame (0 if none received)
     */
    uint32_t getLastRCFrameTime() const { return lastRCFrameTime; }

    /**
     * Get statistics
     */
    uint32_t getFramesReceived() const { return framesReceived; }
    uint32_t getCRCErrors() const { return crcErrors; }
    uint32_t getRCFramesReceived() const { return rcFramesReceived; }
    uint32_t getForwardedFrames() const { return forwardedFrames; }

    /**
     * Reset statistics
     */
    void resetStats();

private:
    enum State {
        WAIT_SYNC,
        WAIT_LENGTH,
        RECEIVE_DATA
    };

    State state;
    uint8_t rxBuffer[CRSF_MAX_FRAME_SIZE];
    uint8_t rxBufferIndex;
    uint8_t rxFrameLength;

    RCChannels& rcChannels;
    CDCForwardCallback forwardCallback;

    uint32_t framesReceived;
    uint32_t crcErrors;
    uint32_t rcFramesReceived;
    uint32_t forwardedFrames;
    uint32_t lastRCFrameTime;  // Timestamp of last RC frame received (0 = none)

    void processFrame(const uint8_t* frame, uint8_t length);
    bool isValidSyncByte(uint8_t byte) const;
};
