/**
 * @file crsf_protocol.h
 * @brief CRSF protocol constants, frame types, and CRC calculation
 * 
 * This file defines all CRSF protocol constants as specified in the 
 * EdgeTX/ELRS implementation.
 */

#pragma once

#include <stdint.h>

// =============================================================================
// Protocol Constants
// =============================================================================

// CRSF Addresses
constexpr uint8_t CRSF_ADDRESS_BROADCAST = 0x00;
constexpr uint8_t CRSF_ADDRESS_RADIO = 0xEA;
constexpr uint8_t CRSF_ADDRESS_MODULE = 0xEE;
constexpr uint8_t CRSF_SYNC_BYTE = 0xC8;
constexpr uint8_t CRSF_ADDRESS_ELRS_LUA = 0xEF;
constexpr uint8_t CRSF_ADDRESS_RECEIVER = 0xEC;

// CRSF Frame Types
constexpr uint8_t CRSF_FRAMETYPE_GPS = 0x02;
constexpr uint8_t CRSF_FRAMETYPE_BATTERY = 0x08;
constexpr uint8_t CRSF_FRAMETYPE_LINK_STATISTICS = 0x14;
constexpr uint8_t CRSF_FRAMETYPE_RC_CHANNELS = 0x16;
constexpr uint8_t CRSF_FRAMETYPE_ATTITUDE = 0x1E;
constexpr uint8_t CRSF_FRAMETYPE_FLIGHT_MODE = 0x21;
constexpr uint8_t CRSF_FRAMETYPE_PING_DEVICES = 0x28;
constexpr uint8_t CRSF_FRAMETYPE_DEVICE_INFO = 0x29;
constexpr uint8_t CRSF_FRAMETYPE_REQUEST_SETTINGS = 0x2A;
constexpr uint8_t CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY = 0x2B;
constexpr uint8_t CRSF_FRAMETYPE_PARAMETER_READ = 0x2C;
constexpr uint8_t CRSF_FRAMETYPE_PARAMETER_WRITE = 0x2D;
constexpr uint8_t CRSF_FRAMETYPE_COMMAND = 0x32;
constexpr uint8_t CRSF_FRAMETYPE_RADIO_ID = 0x3A;

// CRSF Subcommands
constexpr uint8_t CRSF_SUBCMD_TIMING = 0x10;
constexpr uint8_t CRSF_SUBCMD_MODEL_SELECT = 0x05;

// Frame Size Limits
constexpr uint8_t CRSF_MAX_FRAME_SIZE = 64;
constexpr uint8_t CRSF_RC_CHANNELS_PACKED_LEN = 22;
constexpr uint8_t CRSF_RC_FRAME_LEN = 26;

// Channel Constants
constexpr uint16_t CRSF_CHANNEL_CENTER = 992;
constexpr uint16_t CRSF_CHANNEL_MIN = 0;
constexpr uint16_t CRSF_CHANNEL_MAX = 1984;

// =============================================================================
// CRC Functions
// =============================================================================

/**
 * Calculate CRC8 with polynomial 0xD5
 * @param data Pointer to data buffer
 * @param len Length of data in bytes
 * @return CRC8 value
 */
uint8_t crsf_crc8(const uint8_t* data, uint8_t len);

// =============================================================================
// Frame Building Functions
// =============================================================================

/**
 * Pack 16 RC channel values (11-bit each) into 22 bytes
 * @param channels Array of 16 channel values (0-1984)
 * @param packed Output buffer (must be 22 bytes)
 */
void crsf_pack_channels(const uint16_t* channels, uint8_t* packed);

/**
 * Unpack 22 bytes into 16 RC channel values (11-bit each)
 * @param packed Input buffer (must be 22 bytes)
 * @param channels Output array of 16 channel values (0-1984)
 */
void crsf_unpack_channels(const uint8_t* packed, uint16_t* channels);

/**
 * Build an RC channels frame
 * @param channels Array of 16 channel values
 * @param frame Output buffer (must be CRSF_RC_FRAME_LEN bytes)
 * @return Frame length
 */
uint8_t crsf_build_rc_frame(const uint16_t* channels, uint8_t* frame);

/**
 * Build a ping devices frame for discovery
 * @param frame Output buffer (must be at least 4 bytes)
 * @return Frame length
 */
uint8_t crsf_build_ping_frame(uint8_t* frame);

/**
 * Build a parameter request frame
 * @param deviceAddr Target device address
 * @param paramIndex Parameter index to read
 * @param frame Output buffer (must be at least 8 bytes)
 * @return Frame length
 */
uint8_t crsf_build_param_request(uint8_t deviceAddr, uint8_t paramIndex, uint8_t* frame);
