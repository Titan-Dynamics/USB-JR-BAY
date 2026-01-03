/**
 * @file cdc_parser.cpp
 * @brief CDC CRSF frame parser implementation
 */

#include "cdc_parser.h"
#include <Arduino.h>

CDCParser::CDCParser(RCChannels& channels)
    : state(WAIT_SYNC)
    , rxBufferIndex(0)
    , rxFrameLength(0)
    , rcChannels(channels)
    , forwardCallback(nullptr)
    , framesReceived(0)
    , crcErrors(0)
    , rcFramesReceived(0)
    , forwardedFrames(0)
    , lastRCFrameTime(0)  // Start in failsafe (no RC frames received yet)
{
}

void CDCParser::resetStats() {
    framesReceived = 0;
    crcErrors = 0;
    rcFramesReceived = 0;
    forwardedFrames = 0;
}

bool CDCParser::isFailsafe() const
{
    // If no RC frame has ever been received, failsafe is active
    if (lastRCFrameTime == 0) {
        return true;
    }

    // Check if timeout has expired since last RC frame
    uint32_t now = micros();
    uint32_t elapsed = now - lastRCFrameTime;
    return elapsed > FAILSAFE_TIMEOUT_US;
}

bool CDCParser::isValidSyncByte(uint8_t byte) const
{
    return (byte == CRSF_SYNC_BYTE ||
            byte == CRSF_ADDRESS_RADIO ||
            byte == CRSF_ADDRESS_RECEIVER ||
            byte == CRSF_ADDRESS_MODULE);
}

void CDCParser::processByte(uint8_t byte)
{
    switch (state) {
        case WAIT_SYNC:
            if (isValidSyncByte(byte)) {
                rxBuffer[0] = byte;
                rxBufferIndex = 1;
                state = WAIT_LENGTH;
            }
            break;

        case WAIT_LENGTH:
            if (byte >= 2 && byte <= CRSF_MAX_FRAME_SIZE - 2) {
                rxBuffer[1] = byte;
                rxBufferIndex = 2;
                rxFrameLength = byte + 2;  // Total frame length
                state = RECEIVE_DATA;
            } else {
                // Invalid length, reset
                state = WAIT_SYNC;
            }
            break;

        case RECEIVE_DATA:
            rxBuffer[rxBufferIndex++] = byte;

            if (rxBufferIndex >= rxFrameLength) {
                // Complete frame received
                processFrame(rxBuffer, rxFrameLength);
                state = WAIT_SYNC;
            }
            break;
    }
}

void CDCParser::processFrame(const uint8_t* frame, uint8_t length)
{
    if (length < 4) return;

    uint8_t addr = frame[0];
    uint8_t frameLen = frame[1];
    uint8_t type = frame[2];

    // Verify frame length
    if (frameLen + 2 != length) {
        return;
    }

    // Verify CRC (over Type + Payload)
    uint8_t expectedCRC = crsf_crc8(&frame[2], frameLen - 1);
    uint8_t receivedCRC = frame[length - 1];
    if (expectedCRC != receivedCRC) {
        crcErrors++;
        return;
    }

    framesReceived++;

    // Handle frame based on type
    switch (type) {
        case CRSF_FRAMETYPE_RC_CHANNELS: {
            // RC frames update local channel values
            const uint8_t* payload = &frame[3];
            uint8_t payloadLen = frameLen - 2;

            if (payloadLen == CRSF_RC_CHANNELS_PACKED_LEN) {
                uint16_t newChannels[RCChannels::NUM_CHANNELS];
                crsf_unpack_channels(payload, newChannels);
                rcChannels.setChannelsCRSF(newChannels);
                rcFramesReceived++;
                lastRCFrameTime = micros();  // Update timestamp to prevent failsafe
            }
            break;
        }

        case CRSF_FRAMETYPE_PING_DEVICES:
        case CRSF_FRAMETYPE_PARAMETER_READ:
        case CRSF_FRAMETYPE_PARAMETER_WRITE:
        case CRSF_FRAMETYPE_COMMAND:
            // Forward these frames to the TX module
            if (forwardCallback) {
                if (forwardCallback(frame, length)) {
                    forwardedFrames++;
                }
            }
            break;

        default:
            Serial.printf("[CDC] Unhandled frame type: 0x%02X\n", type);
            break;
    }
}
