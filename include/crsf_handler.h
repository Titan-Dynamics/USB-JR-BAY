#pragma once

#include <Arduino.h>
#include <HardwareSerial.h>
#include <driver/gpio.h>
#include <hal/uart_ll.h>
#include <soc/uart_reg.h>
#include "crsf_protocol.h"

// ============================================================================
// Hardware Configuration
// ============================================================================

#define CRSF_UART_PIN   5   // GPIO5 for 1-wire UART
#define CRSF_BAUD       1870000 // 1.87 MBaud
#define CRSF_UART_NUM   0   // UART0

// ============================================================================
// CRSF Half-Duplex UART Handler
// ============================================================================

class CrsfSerial
{
public:
    CrsfSerial();

    void begin();
    void end();

    // Half-duplex control
    void setRxMode();
    void setTxMode();
    bool isTxIdle();

    // Data transmission
    void write(const uint8_t *data, size_t len);
    void flush();

    // Data reception
    int available();
    uint8_t read();
    void flushRx();

private:
    HardwareSerial uart;
    bool inTxMode;

    void setupGpioRx();
    void setupGpioTx();
};

// ============================================================================
// CRSF Packet Handler
// ============================================================================

class CrsfPacketHandler
{
public:
    CrsfPacketHandler();

    void begin();
    void update();

    // RC Packet transmission
    void sendRcChannels();
    void setChannelValue(uint8_t channel, uint16_t value);

    // Packet reception handlers
    void handleLinkStatistics(const uint8_t *payload, uint8_t len);
    void handleMixerSync(const uint8_t *payload, uint8_t len);
    void handlePacket(const uint8_t *packet, uint8_t len);

    // Status
    bool isConnected() const { return connected; }
    uint32_t getRcPacketInterval() const { return rcPacketInterval; }

private:
    CrsfSerial serial;

    // Timing
    uint32_t lastRcPacketSent;
    uint32_t rcPacketInterval; // In microseconds
    uint32_t lastMixerSyncReceived;
    bool connected;

    // RC Channels
    uint16_t channels[16];

    // RX Buffer
    uint8_t rxBuffer[CRSF_MAX_PACKET_LEN];
    uint8_t rxIndex;
    bool packetReady;

    // Statistics
    uint32_t rxPacketCount;
    uint32_t rxBadCrcCount;
    uint32_t txPacketCount;

    // Internal methods
    void processIncomingByte(uint8_t byte);
    void validateAndProcessPacket();
    void packChannels(uint8_t *dest, const uint16_t *channels);
    void sendUsbTelemetry(int8_t rss1, int8_t rss2, uint8_t lq, int8_t rsnr,
                          uint8_t rfmd, uint8_t tpwr, int8_t trss, uint8_t tlq,
                          int8_t tsnr, uint8_t flags);

    // Timing helpers
    void updateRcTiming();
    bool isTimeToSendRc();
};
