#pragma once

#include <Arduino.h>
#include "crsf_handler.h"

// ============================================================================
// USB Host Protocol Definitions
// ============================================================================

// Frame sync bytes
#define USB_SYNC0 0x55
#define USB_SYNC1 0xAA

// Frame types
#define USB_HTYPE_CHANNELS 0x01
#define USB_HTYPE_TEL_STATS 0x02
#define USB_HTYPE_DEBUG 0x03
#define USB_HTYPE_TEL_RAW 0x04

// Parser state machine
enum UsbHostState
{
    USB_S0,      // Waiting for SYNC0
    USB_S1,      // Waiting for SYNC1
    USB_S2_LEN0, // Reading length low byte
    USB_S3_LEN1, // Reading length high byte
    USB_S4_BODY, // Reading TYPE + PAYLOAD
    USB_S5_CRC   // Reading and validating CRC
};

// ============================================================================
// USB Host Protocol Parser
// ============================================================================

/**
 * UsbHostParser
 *
 * Parses binary protocol frames from USB CDC serial and extracts RC channel data.
 * Implements the protocol specification from USB_HOST_PROTOCOL.md.
 *
 * Protocol Frame Format:
 *   [SYNC0][SYNC1][LEN_LOW][LEN_HIGH][TYPE][PAYLOAD...][CRC8]
 *
 * RC Channel Frame (Type 0x01):
 *   - 32-byte payload (16 channels × 2 bytes little-endian)
 *   - Channel range: 0-2047 (992 = center/1500µs)
 *   - CRC8 polynomial: 0xD5
 *
 * Features:
 *   - Non-blocking byte-by-byte processing
 *   - 6-state state machine for robust parsing
 *   - CRC8 validation
 *   - Automatic channel clamping (0-2047)
 *   - Direct integration with CrsfPacketHandler
 *
 * Usage:
 *   UsbHostParser usbParser;
 *   void loop() {
 *       usbParser.update(crsf);
 *   }
 */
class UsbHostParser
{
public:
    UsbHostParser();

    /**
     * Process incoming USB serial data
     * Call this every loop iteration
     * Non-blocking - processes all available bytes
     */
    void update(CrsfPacketHandler &crsf);

private:
    UsbHostState state;
    uint16_t hlen;
    uint8_t htype;
    uint8_t hbuf[128];
    uint16_t hpos;

    /**
     * Process a single byte through the state machine
     */
    void processByte(uint8_t b, CrsfPacketHandler &crsf);

    /**
     * Handle a complete, validated frame
     */
    void handleFrame(uint8_t type, const uint8_t *payload, uint16_t plen, CrsfPacketHandler &crsf);

    /**
     * Calculate CRC8 with polynomial 0xD5
     */
    uint8_t crc8_d5(const uint8_t *data, uint16_t len);
};
