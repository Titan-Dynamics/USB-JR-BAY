#include <Arduino.h>
#include "crsf_handler.h"
#include "usb_host_parser.h"
#include "debug.h"

// ============================================================================
// Global Objects
// ============================================================================

// CRSF protocol handler
CrsfPacketHandler crsf;

// USB host protocol parser (USB CDC serial from PC)
UsbHostParser usbParser;

// ============================================================================
// Setup
// ============================================================================

void setup()
{
    // Initialize USB CDC for debugging and RC data input
    Serial.begin(115200);

    // Initialize CRSF handler
    crsf.begin();

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
    // Process incoming USB host protocol data
    // This parses RC channel frames from the USB CDC port,
    // and then updates the CRSF handler with the new values
    usbParser.update(crsf);

    // Update CRSF handler (non-blocking)
    // This handles:
    // - Reading incoming UART data from ELRS module
    // - Processing received packets (link stats, mixer sync)
    // - Sending RC packets at precise intervals
    crsf.update();
}
