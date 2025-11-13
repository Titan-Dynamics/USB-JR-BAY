#pragma once

#include <Arduino.h>

// ============================================================================
// CRSF Protocol Constants
// ============================================================================

// Frame types
#define CRSF_FRAMETYPE_GPS 0x02
#define CRSF_FRAMETYPE_VARIO 0x07
#define CRSF_FRAMETYPE_BATTERY_SENSOR 0x08
#define CRSF_FRAMETYPE_HEARTBEAT 0x0B
#define CRSF_FRAMETYPE_LINK_STATISTICS 0x14
#define CRSF_FRAMETYPE_RC_CHANNELS_PACKED 0x16
#define CRSF_FRAMETYPE_ATTITUDE 0x1E
#define CRSF_FRAMETYPE_FLIGHT_MODE 0x21
#define CRSF_FRAMETYPE_DEVICE_PING 0x28
#define CRSF_FRAMETYPE_DEVICE_INFO 0x29
#define CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY 0x2B
#define CRSF_FRAMETYPE_PARAMETER_READ 0x2C
#define CRSF_FRAMETYPE_PARAMETER_WRITE 0x2D
#define CRSF_FRAMETYPE_COMMAND 0x32
#define CRSF_FRAMETYPE_HANDSET 0x3A
#define CRSF_FRAMETYPE_MSP_REQ 0x7A
#define CRSF_FRAMETYPE_MSP_RESP 0x7B
#define CRSF_FRAMETYPE_MSP_WRITE 0x7C

// Addresses
#define CRSF_ADDRESS_BROADCAST 0x00
#define CRSF_ADDRESS_USB 0x10
#define CRSF_ADDRESS_BLUETOOTH 0x12
#define CRSF_ADDRESS_TBS_CORE_PNP_PRO 0x80
#define CRSF_ADDRESS_RESERVED1 0x8A
#define CRSF_ADDRESS_CURRENT_SENSOR 0xC0
#define CRSF_ADDRESS_GPS 0xC2
#define CRSF_ADDRESS_TBS_BLACKBOX 0xC4
#define CRSF_ADDRESS_FLIGHT_CONTROLLER 0xC8
#define CRSF_ADDRESS_RESERVED2 0xCA
#define CRSF_ADDRESS_RACE_TAG 0xCC
#define CRSF_ADDRESS_RADIO_TRANSMITTER 0xEA
#define CRSF_ADDRESS_CRSF_RECEIVER 0xEC
#define CRSF_ADDRESS_CRSF_TRANSMITTER 0xEE
#define CRSF_ADDRESS_ELRS_LUA 0xEF

// Subcommands
#define CRSF_COMMAND_SUBCMD_RX 0x10
#define CRSF_COMMAND_SUBCMD_RX_BIND 0x01
#define CRSF_COMMAND_MODEL_SELECT_ID 0x05
#define CRSF_HANDSET_SUBCMD_TIMING 0x10

// Channel values
#define CRSF_CHANNEL_VALUE_MIN 172 // 987us
#define CRSF_CHANNEL_VALUE_1000 191
#define CRSF_CHANNEL_VALUE_MID 992 // 1500us
#define CRSF_CHANNEL_VALUE_2000 1792
#define CRSF_CHANNEL_VALUE_MAX 1811 // 2012us

// Packet sizes
#define CRSF_MAX_PACKET_LEN 64
#define CRSF_PAYLOAD_SIZE_MAX 60
#define CRSF_FRAME_SIZE_MAX 64
#define CRSF_RC_CHANNELS_PAYLOAD_SIZE 22
#define CRSF_LINK_STATISTICS_PAYLOAD_SIZE 10

// CRC
#define CRSF_CRC_POLY 0xD5

// ============================================================================
// CRSF Data Structures
// ============================================================================

#pragma pack(push, 1)

// Standard header (non-extended frames)
typedef struct
{
    uint8_t device_addr;
    uint8_t frame_size; // Size after this byte (type + payload + crc)
    uint8_t type;
    uint8_t payload[0];
} crsf_header_t;

// Extended header (for device communication)
typedef struct
{
    uint8_t device_addr;
    uint8_t frame_size;
    uint8_t type;
    uint8_t dest_addr;
    uint8_t orig_addr;
    uint8_t payload[0];
} crsf_ext_header_t;

// RC Channels (16 channels, 11 bits each)
typedef struct
{
    unsigned ch0 : 11;
    unsigned ch1 : 11;
    unsigned ch2 : 11;
    unsigned ch3 : 11;
    unsigned ch4 : 11;
    unsigned ch5 : 11;
    unsigned ch6 : 11;
    unsigned ch7 : 11;
    unsigned ch8 : 11;
    unsigned ch9 : 11;
    unsigned ch10 : 11;
    unsigned ch11 : 11;
    unsigned ch12 : 11;
    unsigned ch13 : 11;
    unsigned ch14 : 11;
    unsigned ch15 : 11;
} crsf_channels_t;

// Link Statistics
typedef struct
{
    uint8_t uplink_RSSI_1;
    uint8_t uplink_RSSI_2;
    uint8_t uplink_Link_quality;
    int8_t uplink_SNR;
    uint8_t active_antenna;
    uint8_t rf_Mode;
    uint8_t uplink_TX_Power;
    uint8_t downlink_RSSI_1;
    uint8_t downlink_Link_quality;
    int8_t downlink_SNR;
} crsf_link_statistics_t;

// Extended Link Statistics (ELRS)
typedef struct
{
    uint8_t uplink_RSSI_1;
    uint8_t uplink_RSSI_2;
    uint8_t uplink_Link_quality;
    int8_t uplink_SNR;
    uint8_t active_antenna;
    uint8_t rf_Mode;
    uint8_t uplink_TX_Power;
    uint8_t downlink_RSSI_1;
    uint8_t downlink_Link_quality;
    int8_t downlink_SNR;
    uint8_t downlink_RSSI_2;
} elrs_link_statistics_t;

// Mixer Sync (OpenTX Sync)
typedef struct
{
    uint8_t subType; // 0x10
    uint32_t rate;   // Big-endian, interval in 0.1µs units
    uint32_t offset; // Big-endian, offset in 0.1µs units
} crsf_mixer_sync_t;

#pragma pack(pop)

// ============================================================================
// CRC Calculation
// ============================================================================

class CrsfCrc
{
public:
    static uint8_t calc(const uint8_t *data, uint8_t len, uint8_t crc = 0)
    {
        for (uint8_t i = 0; i < len; i++)
        {
            crc ^= data[i];
            for (uint8_t j = 0; j < 8; j++)
            {
                if (crc & 0x80)
                {
                    crc = (crc << 1) ^ CRSF_CRC_POLY;
                }
                else
                {
                    crc = crc << 1;
                }
            }
        }
        return crc;
    }
};
