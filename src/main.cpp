/**
 * @file main.cpp
 * @brief ESP32-S3 CRSF Protocol Handler - Main Entry Point
 *
 * Implements EdgeTX-like communication with an ELRS TX module over
 * a half-duplex one-wire UART. This is the Arduino setup/loop glue code.
 *
 * Hardware: ESP32-S3 (Seeed XIAO) + ELRS TX module on GPIO5
 */

#include <Arduino.h>
#include "crsf_uart.h"
#include "crsf_parser.h"
#include "crsf_protocol.h"
#include "module_sync.h"
#include "rc_channels.h"
#include "cdc_parser.h"
#include "crsf_task.h"

// =============================================================================
// Configuration
// =============================================================================

constexpr uint8_t CRSF_PIN = 5;                 // GPIO5 for half-duplex CRSF
constexpr uint32_t CRSF_BAUDRATE = 1870000;     // CRSF baudrate
constexpr uint32_t STATS_INTERVAL_MS = 5000;    // Print stats every 5s

// =============================================================================
// Global Objects
// =============================================================================

static CRSFUart crsfUart;
static ModuleSync moduleSync;
static RCChannels rcChannels;
static CRSFParser parser;
static CDCParser cdcParser(rcChannels);
static CRSFTask crsfTask(crsfUart, parser, cdcParser, moduleSync, rcChannels);

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Forward frame from CDC to TX module (callback for CDCParser)
 */
bool forwardCDCToModule(const uint8_t* frame, uint8_t length)
{
    return crsfTask.queueOutputFrame(frame, length);
}

/**
 * Forward frame from module to CDC (callback for CRSFParser)
 */
void forwardModuleToCDC(const uint8_t* frame, uint8_t length)
{
    if (Serial) {
        Serial.write(frame, length);
    }
}

/**
 * Handle serial data from PC (commands and CRSF frames)
 */
void handleSerialCommands()
{
    while (Serial.available()) {
        uint8_t byte = Serial.read();

        // Try to process as CRSF frame
        cdcParser.processByte(byte);
    }
}

// =============================================================================
// Arduino Setup and Loop
// =============================================================================

void setup()
{
    // Initialize USB Serial for PC connection
    Serial.begin(CRSF_BAUDRATE);
    delay(1000);

    // Initialize CRSF UART
    crsfUart.begin(CRSF_PIN, CRSF_BAUDRATE);

    if (!crsfUart.isInitialized()) {
        Serial.println("ERROR: Failed to initialize CRSF UART!");
        while (1) { delay(1000); }
    }

    // Set up bidirectional frame forwarding
    cdcParser.setForwardCallback(forwardCDCToModule);
    parser.setToCDCCallback(forwardModuleToCDC);
    
    // Set up timing sync callback
    parser.setTimingSyncCallback([](int32_t refreshRateUs, int32_t inputLagUs) {
        moduleSync.updateTiming(refreshRateUs, inputLagUs);
    });
}

void loop()
{
    // Run main CRSF task
    crsfTask.run();
    
    // Handle serial commands from PC
    handleSerialCommands();
}
