/**
 * @file crsf_protocol.cpp
 * @brief CRSF protocol implementation
 */

#include "crsf_protocol.h"

// CRC8 Lookup Table (Polynomial 0xD5)
static const uint8_t crc8_lut[256] = {
    0x00, 0xD5, 0x7F, 0xAA, 0xFE, 0x2B, 0x81, 0x54,
    0x29, 0xFC, 0x56, 0x83, 0xD7, 0x02, 0xA8, 0x7D,
    0x52, 0x87, 0x2D, 0xF8, 0xAC, 0x79, 0xD3, 0x06,
    0x7B, 0xAE, 0x04, 0xD1, 0x85, 0x50, 0xFA, 0x2F,
    0xA4, 0x71, 0xDB, 0x0E, 0x5A, 0x8F, 0x25, 0xF0,
    0x8D, 0x58, 0xF2, 0x27, 0x73, 0xA6, 0x0C, 0xD9,
    0xF6, 0x23, 0x89, 0x5C, 0x08, 0xDD, 0x77, 0xA2,
    0xDF, 0x0A, 0xA0, 0x75, 0x21, 0xF4, 0x5E, 0x8B,
    0x9D, 0x48, 0xE2, 0x37, 0x63, 0xB6, 0x1C, 0xC9,
    0xB4, 0x61, 0xCB, 0x1E, 0x4A, 0x9F, 0x35, 0xE0,
    0xCF, 0x1A, 0xB0, 0x65, 0x31, 0xE4, 0x4E, 0x9B,
    0xE6, 0x33, 0x99, 0x4C, 0x18, 0xCD, 0x67, 0xB2,
    0x39, 0xEC, 0x46, 0x93, 0xC7, 0x12, 0xB8, 0x6D,
    0x10, 0xC5, 0x6F, 0xBA, 0xEE, 0x3B, 0x91, 0x44,
    0x6B, 0xBE, 0x14, 0xC1, 0x95, 0x40, 0xEA, 0x3F,
    0x42, 0x97, 0x3D, 0xE8, 0xBC, 0x69, 0xC3, 0x16,
    0xEF, 0x3A, 0x90, 0x45, 0x11, 0xC4, 0x6E, 0xBB,
    0xC6, 0x13, 0xB9, 0x6C, 0x38, 0xED, 0x47, 0x92,
    0xBD, 0x68, 0xC2, 0x17, 0x43, 0x96, 0x3C, 0xE9,
    0x94, 0x41, 0xEB, 0x3E, 0x6A, 0xBF, 0x15, 0xC0,
    0x4B, 0x9E, 0x34, 0xE1, 0xB5, 0x60, 0xCA, 0x1F,
    0x62, 0xB7, 0x1D, 0xC8, 0x9C, 0x49, 0xE3, 0x36,
    0x19, 0xCC, 0x66, 0xB3, 0xE7, 0x32, 0x98, 0x4D,
    0x30, 0xE5, 0x4F, 0x9A, 0xCE, 0x1B, 0xB1, 0x64,
    0x72, 0xA7, 0x0D, 0xD8, 0x8C, 0x59, 0xF3, 0x26,
    0x5B, 0x8E, 0x24, 0xF1, 0xA5, 0x70, 0xDA, 0x0F,
    0x20, 0xF5, 0x5F, 0x8A, 0xDE, 0x0B, 0xA1, 0x74,
    0x09, 0xDC, 0x76, 0xA3, 0xF7, 0x22, 0x88, 0x5D,
    0xD6, 0x03, 0xA9, 0x7C, 0x28, 0xFD, 0x57, 0x82,
    0xFF, 0x2A, 0x80, 0x55, 0x01, 0xD4, 0x7E, 0xAB,
    0x84, 0x51, 0xFB, 0x2E, 0x7A, 0xAF, 0x05, 0xD0,
    0xAD, 0x78, 0xD2, 0x07, 0x53, 0x86, 0x2C, 0xF9
};

uint8_t crsf_crc8(const uint8_t* data, uint8_t len)
{
    uint8_t crc = 0;
    while (len--) {
        crc = crc8_lut[crc ^ *data++];
    }
    return crc;
}

void crsf_pack_channels(const uint16_t* channels, uint8_t* packed)
{
    packed[0]  = (uint8_t)(channels[0] & 0xFF);
    packed[1]  = (uint8_t)((channels[0] >> 8) | (channels[1] << 3));
    packed[2]  = (uint8_t)((channels[1] >> 5) | (channels[2] << 6));
    packed[3]  = (uint8_t)((channels[2] >> 2) & 0xFF);
    packed[4]  = (uint8_t)((channels[2] >> 10) | (channels[3] << 1));
    packed[5]  = (uint8_t)((channels[3] >> 7) | (channels[4] << 4));
    packed[6]  = (uint8_t)((channels[4] >> 4) | (channels[5] << 7));
    packed[7]  = (uint8_t)((channels[5] >> 1) & 0xFF);
    packed[8]  = (uint8_t)((channels[5] >> 9) | (channels[6] << 2));
    packed[9]  = (uint8_t)((channels[6] >> 6) | (channels[7] << 5));
    packed[10] = (uint8_t)((channels[7] >> 3) & 0xFF);
    packed[11] = (uint8_t)(channels[8] & 0xFF);
    packed[12] = (uint8_t)((channels[8] >> 8) | (channels[9] << 3));
    packed[13] = (uint8_t)((channels[9] >> 5) | (channels[10] << 6));
    packed[14] = (uint8_t)((channels[10] >> 2) & 0xFF);
    packed[15] = (uint8_t)((channels[10] >> 10) | (channels[11] << 1));
    packed[16] = (uint8_t)((channels[11] >> 7) | (channels[12] << 4));
    packed[17] = (uint8_t)((channels[12] >> 4) | (channels[13] << 7));
    packed[18] = (uint8_t)((channels[13] >> 1) & 0xFF);
    packed[19] = (uint8_t)((channels[13] >> 9) | (channels[14] << 2));
    packed[20] = (uint8_t)((channels[14] >> 6) | (channels[15] << 5));
    packed[21] = (uint8_t)((channels[15] >> 3) & 0xFF);
}

void crsf_unpack_channels(const uint8_t* packed, uint16_t* channels)
{
    channels[0]  = ((uint16_t)packed[0]  | (uint16_t)packed[1]  << 8)                      & 0x07FF;
    channels[1]  = ((uint16_t)packed[1]  >> 3 | (uint16_t)packed[2]  << 5)                 & 0x07FF;
    channels[2]  = ((uint16_t)packed[2]  >> 6 | (uint16_t)packed[3]  << 2 | (uint16_t)packed[4] << 10) & 0x07FF;
    channels[3]  = ((uint16_t)packed[4]  >> 1 | (uint16_t)packed[5]  << 7)                 & 0x07FF;
    channels[4]  = ((uint16_t)packed[5]  >> 4 | (uint16_t)packed[6]  << 4)                 & 0x07FF;
    channels[5]  = ((uint16_t)packed[6]  >> 7 | (uint16_t)packed[7]  << 1 | (uint16_t)packed[8] << 9)  & 0x07FF;
    channels[6]  = ((uint16_t)packed[8]  >> 2 | (uint16_t)packed[9]  << 6)                 & 0x07FF;
    channels[7]  = ((uint16_t)packed[9]  >> 5 | (uint16_t)packed[10] << 3)                 & 0x07FF;
    channels[8]  = ((uint16_t)packed[11] | (uint16_t)packed[12] << 8)                      & 0x07FF;
    channels[9]  = ((uint16_t)packed[12] >> 3 | (uint16_t)packed[13] << 5)                 & 0x07FF;
    channels[10] = ((uint16_t)packed[13] >> 6 | (uint16_t)packed[14] << 2 | (uint16_t)packed[15] << 10) & 0x07FF;
    channels[11] = ((uint16_t)packed[15] >> 1 | (uint16_t)packed[16] << 7)                 & 0x07FF;
    channels[12] = ((uint16_t)packed[16] >> 4 | (uint16_t)packed[17] << 4)                 & 0x07FF;
    channels[13] = ((uint16_t)packed[17] >> 7 | (uint16_t)packed[18] << 1 | (uint16_t)packed[19] << 9)  & 0x07FF;
    channels[14] = ((uint16_t)packed[19] >> 2 | (uint16_t)packed[20] << 6)                 & 0x07FF;
    channels[15] = ((uint16_t)packed[20] >> 5 | (uint16_t)packed[21] << 3)                 & 0x07FF;
}

uint8_t crsf_build_rc_frame(const uint16_t* channels, uint8_t* frame)
{
    frame[0] = CRSF_ADDRESS_MODULE;                 // Destination: Module (0xEE)
    frame[1] = CRSF_RC_CHANNELS_PACKED_LEN + 2;     // Length: Type + Payload + CRC
    frame[2] = CRSF_FRAMETYPE_RC_CHANNELS;          // Type: RC Channels (0x16)
    
    crsf_pack_channels(channels, &frame[3]);
    
    frame[25] = crsf_crc8(&frame[2], CRSF_RC_CHANNELS_PACKED_LEN + 1);
    
    return CRSF_RC_FRAME_LEN;
}

uint8_t crsf_build_ping_frame(uint8_t* frame)
{
    // Device ping payload: [destination, origin]
    frame[0] = CRSF_SYNC_BYTE;           // 0xC8: Sync byte for radio->module commands
    frame[1] = 4;                         // Length: Type + Payload (2 bytes) + CRC
    frame[2] = CRSF_FRAMETYPE_PING_DEVICES;
    frame[3] = 0x00;                      // Destination: Broadcast
    frame[4] = CRSF_ADDRESS_RADIO;        // Origin: Handset (0xEA)
    frame[5] = crsf_crc8(&frame[2], 3);   // CRC over Type + 2-byte Payload
    
    return 6;
}

uint8_t crsf_build_param_request(uint8_t deviceAddr, uint8_t paramIndex, uint8_t* frame)
{
    // Extended addressing: [sync][len][type][dest][origin][payload...][crc]
    // Payload: [paramIndex, chunkNumber]
    frame[0] = CRSF_SYNC_BYTE;          // 0xC8: Sync byte
    frame[1] = 6;                        // Length: Type + Dest + Origin + Payload (2) + CRC
    frame[2] = CRSF_FRAMETYPE_PARAMETER_READ;  // 0x2C: Parameter read
    frame[3] = deviceAddr;               // Destination: Device address
    frame[4] = CRSF_ADDRESS_RADIO;       // Origin: Radio/handset
    frame[5] = paramIndex;               // Param number
    frame[6] = 0;                        // Chunk number (0 = first chunk)
    frame[7] = crsf_crc8(&frame[2], 5);  // CRC over Type + Dest + Origin + Payload
    
    return 8;
}
