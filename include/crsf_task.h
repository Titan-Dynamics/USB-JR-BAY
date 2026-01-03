/**
 * @file crsf_task.h
 * @brief Main CRSF task class
 *
 * Encapsulates the main task logic for sending RC frames,
 * processing incoming data, and managing UART mode switching.
 */

#pragma once

#include <stdint.h>
#include "crsf_uart.h"
#include "crsf_parser.h"
#include "cdc_parser.h"
#include "module_sync.h"
#include "rc_channels.h"
#include "crsf_protocol.h"

/**
 * @class CRSFTask
 * @brief Main CRSF protocol handler task
 *
 * Manages the core timing and communication logic for the CRSF protocol:
 * - Sends RC frames at synchronized intervals (only when not in failsafe)
 * - Processes incoming data from TX module
 * - Handles UART TX/RX mode switching
 * - Manages queued output frames
 */
class CRSFTask {
public:
    /**
     * @brief Construct a new CRSF Task
     * 
     * @param uart UART interface for communication with TX module
     * @param parser Parser for incoming frames from module
     * @param cdcParser Parser for CDC frames from PC (for failsafe check)
     * @param moduleSync Timing synchronization handler
     * @param channels RC channel data
     */
    CRSFTask(CRSFUart& uart, CRSFParser& parser, CDCParser& cdcParser,
             ModuleSync& moduleSync, RCChannels& channels);

    /**
     * @brief Execute one iteration of the CRSF task
     * 
     * Should be called repeatedly from the main loop().
     * Handles all timing-critical operations including:
     * - UART mode switching after TX complete
     * - Processing incoming bytes when not transmitting
     * - Sending RC frames at appropriate intervals
     */
    void run();

    /**
     * @brief Queue a frame to be sent in the next RC slot
     * 
     * Used for sending device queries, parameter requests, etc.
     * The queued frame will be sent instead of the next RC frame.
     * 
     * @param data Frame data to send
     * @param length Length of frame data
     * @return true if frame was queued successfully
     * @return false if queue is full or length is invalid
     */
    bool queueOutputFrame(const uint8_t* data, uint8_t length);

    /**
     * @brief Get the number of RC frames sent
     * @return uint32_t Total RC frames transmitted
     */
    uint32_t getRCFramesSent() const { return rcFramesSent; }

    /**
     * @brief Get the timestamp of the last RC frame
     * @return uint32_t Microsecond timestamp of last RC frame
     */
    uint32_t getLastRCFrameTime() const { return lastRCFrameTime; }

private:
    /**
     * @brief Check if it's time to send the next RC frame
     * @return true if enough time has elapsed since last frame
     */
    bool isTimeToSendRC() const;

    /**
     * @brief Send an RC channels frame to the module
     */
    void sendRCFrame();

    // Dependencies
    CRSFUart& uart;
    CRSFParser& parser;
    CDCParser& cdcParser;
    ModuleSync& moduleSync;
    RCChannels& channels;

    // State tracking
    uint32_t lastRCFrameTime;
    uint32_t rcFramesSent;

    // Output frame queue (single slot)
    uint8_t outputFrameBuffer[CRSF_MAX_FRAME_SIZE];
    uint8_t outputFrameLength;
    bool outputFramePending;
};
