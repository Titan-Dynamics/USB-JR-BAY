#include <Arduino.h>
#include "crsf_handler.h"
#include "debug.h"

// ============================================================================
// Global Objects
// ============================================================================

// CRSF half-duplex serial driver
CrsfSerial crsfSerial;

// ============================================================================
// Setup
// ============================================================================

void setup()
{
    // Initialize USB CDC for debugging and RC data input
    Serial.begin(5250000);

    // Initialize CRSF half-duplex UART
    crsfSerial.begin();

    // Debug output control:
    // This affects all DBGPRINT/DBGPRINTLN/DBGPRINTF output throughout the code
    // Debug::setEnabled(true);
    Debug::setEnabled(false);
}

// ============================================================================
// Main Loop
// ============================================================================

void loop()
{
    // Transparent forwarder:
    // Read bytes from USB CDC and send to CRSF UART (half-duplex)
    static uint8_t buf[256];
    uint16_t len = 0;

    while (Serial.available() && len < sizeof(buf))
    {
        buf[len++] = Serial.read();
    }

    if (len > 0)
    {
        // Switch to TX, send burst, then return to RX
        crsfSerial.setTxMode();
        crsfSerial.write(buf, len);
        crsfSerial.flush();
        crsfSerial.setRxMode();

        // Discard only the loopback echo (same number of bytes we just sent)
        // Don't flush the entire RX buffer, as fast responses from the TX
        // may already be arriving (e.g., DEVICE_INFO replies to DEVICE_PING)
        uint16_t bytesToDiscard = len;
        while (bytesToDiscard > 0 && crsfSerial.available())
        {
            crsfSerial.read();
            bytesToDiscard--;
        }
    }

    // Forward any bytes arriving from the ELRS UART back to USB CDC
    while (crsfSerial.available())
    {
        Serial.write(crsfSerial.read());
    }
}
