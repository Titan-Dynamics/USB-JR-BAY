/**
 * @file crsf_parser.cpp
 * @brief CRSF frame reception and parsing
 */

#include "crsf_parser.h"
#include <Arduino.h>

CRSFParser::CRSFParser()
    : state(WAIT_SYNC)
    , rxBufferIndex(0)
    , rxFrameLength(0)
    , toCDCCallback(nullptr)
    , timingSyncCallback(nullptr)
    , framesReceived(0)
    , crcErrors(0)
{
}

void CRSFParser::resetStats() {
    framesReceived = 0;
    crcErrors = 0;
}

bool CRSFParser::isValidSyncByte(uint8_t byte) const
{
    return (byte == CRSF_SYNC_BYTE || 
            byte == CRSF_ADDRESS_RADIO || 
            byte == CRSF_ADDRESS_RECEIVER || 
            byte == CRSF_ADDRESS_MODULE);
}

void CRSFParser::processByte(uint8_t byte)
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

void CRSFParser::parseRadioIdFrame(const uint8_t* payload, uint8_t len)
{
    if (len < 11) return;
    
    // Check subcommand (payload[2])
    if (payload[2] != CRSF_SUBCMD_TIMING) return;
    
    // Extract interval and offset (big-endian, 100ns units)
    // RATE is at payload[3-6], OFFSET is at payload[7-10]
    int32_t interval = ((int32_t)payload[3] << 24) | ((int32_t)payload[4] << 16) | 
                       ((int32_t)payload[5] << 8) | payload[6];
    int32_t offset = ((int32_t)payload[7] << 24) | ((int32_t)payload[8] << 16) | 
                     ((int32_t)payload[9] << 8) | payload[10];
    
    // Convert from 100ns units to microseconds (divide by 10)
    int32_t refreshRateUs = interval / 10;
    int32_t inputLagUs = offset / 10;
    
    // Notify callback if set
    if (timingSyncCallback) {
        timingSyncCallback(refreshRateUs, inputLagUs);
    }
}

void CRSFParser::processFrame(const uint8_t* frame, uint8_t length)
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
    
    // Payload starts at byte 3, length is frameLen - 2 (exclude type and CRC)
    const uint8_t* payload = &frame[3];
    uint8_t payloadLen = frameLen - 2;
    
    // Handle RADIO_ID (timing sync) frames locally
    if (type == CRSF_FRAMETYPE_RADIO_ID) {
        parseRadioIdFrame(payload, payloadLen);
        return;  // Don't forward timing frames to CDC
    }
    
    // Forward all other frames to CDC for processing on PC
    if (toCDCCallback) {
        toCDCCallback(frame, length);
    }
}
