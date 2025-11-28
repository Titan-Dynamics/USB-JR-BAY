#pragma once

#include <Arduino.h>
#include <HardwareSerial.h>
#include <driver/gpio.h>
#include <hal/uart_ll.h>
#include <soc/uart_reg.h>

// ============================================================================
// Hardware Configuration
// ============================================================================

#define CRSF_UART_PIN   5       // GPIO5 for 1-wire UART
#define CRSF_BAUD       5250000 // 5.25 MBaud
#define CRSF_UART_NUM   0       // UART0

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
