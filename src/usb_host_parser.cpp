/*
 * USB Host Protocol Parser Implementation
 *
 * Implements the binary protocol parser for receiving RC channel data from a PC
 * via USB CDC serial.
 *
 * Protocol: Custom binary framing with CRC8 validation
 * Baud: 115200
 * Interface: USB CDC Serial (Serial object)
 */

#include <Arduino.h>
#include "usb_host_parser.h"
#include "debug.h"

// Timestamp (ms) of the last received USB serial byte
// Used by CRSF handler to inhibit TX after inactivity
volatile uint32_t usb_host_last_activity_ms = 0;

// ============================================================================
// Constructor
// ============================================================================

UsbHostParser::UsbHostParser()
    : state(USB_S0), hlen(0), htype(0), hpos(0)
{
    memset(hbuf, 0, sizeof(hbuf));
}

// ============================================================================
// Public Methods
// ============================================================================

void UsbHostParser::update(CrsfPacketHandler &crsf)
{
    // Process all available bytes from USB serial
    // Non-blocking - only processes what's in the buffer
    while (Serial.available())
    {
        uint8_t b = Serial.read();
        // Mark USB activity for inactivity timeout logic
        usb_host_last_activity_ms = millis();
        processByte(b, crsf);
    }
}

// ============================================================================
// Private Methods
// ============================================================================

void UsbHostParser::processByte(uint8_t b, CrsfPacketHandler &crsf)
{
    switch (state)
    {
    case USB_S0:
        // Waiting for first sync byte (0x55)
        state = (b == USB_SYNC0) ? USB_S1 : USB_S0;
        break;

    case USB_S1:
        // Waiting for second sync byte (0xAA)
        state = (b == USB_SYNC1) ? USB_S2_LEN0 : USB_S0;
        break;

    case USB_S2_LEN0:
        // Read length low byte
        hlen = b;
        state = USB_S3_LEN1;
        break;

    case USB_S3_LEN1:
        // Read length high byte (little-endian)
        hlen |= ((uint16_t)b << 8);
        hpos = 0;
        state = USB_S4_BODY;
        break;

    case USB_S4_BODY:
        // Read TYPE byte first, then PAYLOAD bytes
        if (hpos == 0)
        {
            htype = b;
        }
        else
        {
            // Store payload bytes in buffer (with bounds checking)
            if (hpos - 1 < sizeof(hbuf))
            {
                hbuf[hpos - 1] = b;
            }
        }
        hpos++;

        // Check if we've read all bytes (type + payload)
        if (hpos >= hlen)
        {
            state = USB_S5_CRC;
        }
        break;

    case USB_S5_CRC:
    {
        // Validate CRC and process frame if valid
        // CRC is calculated over TYPE + PAYLOAD bytes
        uint8_t tmp[1 + sizeof(hbuf)];
        tmp[0] = htype;
        if (hlen > 1)
        {
            uint16_t copyLen = (hlen - 1 < sizeof(hbuf)) ? (hlen - 1) : sizeof(hbuf);
            memcpy(tmp + 1, hbuf, copyLen);
        }
        uint8_t calcCrc = crc8_d5(tmp, hlen);

        if (calcCrc == b)
        {
            // CRC valid - process frame
            handleFrame(htype, hbuf, (uint16_t)(hlen - 1), crsf);
        }
        // Invalid CRC: silently discard frame

        // Reset state machine for next frame
        state = USB_S0;
        break;
    }
    }
}

void UsbHostParser::handleFrame(uint8_t type, const uint8_t *payload, uint16_t plen, CrsfPacketHandler &crsf)
{
    if (type == USB_HTYPE_CHANNELS && plen == 32)
    {
        // RC Channels frame: 16 channels × 2 bytes (little-endian 16-bit)

        // Extract and set each channel value
        for (int i = 0; i < 16; i++)
        {
            // Little-endian 16-bit value
            uint16_t value = (uint16_t)payload[2 * i] | ((uint16_t)payload[2 * i + 1] << 8);

            // Clamp to valid range (0-2047)
            if (value > 2047)
            {
                value = 2047;
            }

            // Update CRSF handler with new channel value
            crsf.setChannelValue(i, value);
        }

        // Debug output for first packet only (to avoid spam)
        static bool firstPacket = true;
        if (firstPacket)
        {
            firstPacket = false;
            DBGPRINTF("[USB] Received RC channels: CH0=%d, CH1=%d, CH2=%d, CH3=%d\n",
                      (uint16_t)payload[0] | ((uint16_t)payload[1] << 8),
                      (uint16_t)payload[2] | ((uint16_t)payload[3] << 8),
                      (uint16_t)payload[4] | ((uint16_t)payload[5] << 8),
                      (uint16_t)payload[6] | ((uint16_t)payload[7] << 8));
        }
    }
    // Future: Handle other frame types (0x02 telemetry, 0x03 debug, etc.)
}

uint8_t UsbHostParser::crc8_d5(const uint8_t *data, uint16_t len)
{
    // CRC8 with polynomial 0xD5
    // Matches specification in USB_HOST_PROTOCOL.md
    uint8_t crc = 0;
    for (uint16_t i = 0; i < len; i++)
    {
        crc ^= data[i];
        for (uint8_t j = 0; j < 8; j++)
        {
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0xD5) : (uint8_t)(crc << 1);
        }
    }
    return crc;
}
