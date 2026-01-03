/**
 * @file crsf_parser.h
 * @brief CRSF frame reception and parsing state machine
 *
 * Implements a state machine to assemble complete CRSF frames from
 * a byte stream and dispatch them to appropriate handlers.
 */

#pragma once

#include <stdint.h>
#include "crsf_protocol.h"

/**
 * Callback type for frames that should be forwarded to CDC
 * @param frame Complete CRSF frame including address, length, type, payload, CRC
 * @param length Total frame length
 */
typedef void (*CRSFToCDCCallback)(const uint8_t* frame, uint8_t length);

/**
 * Callback type for timing sync updates
 * @param refreshRateUs Refresh rate in microseconds
 * @param inputLagUs Input lag/offset in microseconds
 */
typedef void (*CRSFTimingSyncCallback)(int32_t refreshRateUs, int32_t inputLagUs);

class CRSFParser {
public:
    CRSFParser();

    /**
     * Process one byte from the UART
     * Call repeatedly as bytes arrive
     * @param byte Received byte
     */
    void processByte(uint8_t byte);

    /**
     * Set callback for frames that should be forwarded to CDC
     * @param callback Function to call when a frame needs forwarding to PC
     */
    void setToCDCCallback(CRSFToCDCCallback callback) { toCDCCallback = callback; }

    /**
     * Set callback for timing sync updates
     * @param callback Function to call when timing frame is parsed
     */
    void setTimingSyncCallback(CRSFTimingSyncCallback callback) { timingSyncCallback = callback; }

    /**
     * Get statistics
     */
    uint32_t getFramesReceived() const { return framesReceived; }
    uint32_t getCRCErrors() const { return crcErrors; }

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

    CRSFToCDCCallback toCDCCallback;
    CRSFTimingSyncCallback timingSyncCallback;

    uint32_t framesReceived;
    uint32_t crcErrors;

    void processFrame(const uint8_t* frame, uint8_t length);
    bool isValidSyncByte(uint8_t byte) const;
    void parseRadioIdFrame(const uint8_t* payload, uint8_t len);
};
