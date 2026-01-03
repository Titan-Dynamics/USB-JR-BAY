/**
 * @file uart_mock.h
 * @brief Mock CRSFUart for testing crsfTask behavior
 *
 * Simulates UART hardware behavior including:
 * - TX/RX mode switching
 * - Transmission completion
 * - Received data buffering
 */

#pragma once

#include <stdint.h>
#include <string.h>
#include "timing_mock.h"

constexpr uint16_t MOCK_UART_RX_BUFFER_SIZE = 256;
constexpr uint8_t MOCK_UART_TX_BUFFER_SIZE = 64;

/**
 * Mock UART for testing
 * Tracks state transitions and simulates TX/RX behavior
 */
class CRSFUart {
public:
    CRSFUart()
        : initialized(false)
        , transmitting(false)
        , gpio(0)
        , baud(0)
        , txStartTime(0)
        , txLength(0)
        , rxHead(0)
        , rxTail(0)
        , txCallCount(0)
        , rxCallCount(0)
        , switchToRXCallCount(0)
    {
        memset(txBuffer, 0, sizeof(txBuffer));
        memset(rxBuffer, 0, sizeof(rxBuffer));
    }

    void begin(uint8_t pin, uint32_t baudrate) {
        gpio = pin;
        baud = baudrate;
        initialized = true;
        transmitting = false;
    }

    bool isInitialized() const { return initialized; }

    /**
     * Simulate transmit - copies data and marks as transmitting
     */
    void transmit(const uint8_t* data, uint8_t length) {
        if (!initialized || transmitting) return;

        memcpy(txBuffer, data, length);
        txLength = length;
        transmitting = true;
        txStartTime = timingMock.getMicros();
        txCallCount++;
    }

    /**
     * Check if transmission is complete
     * Simulates transmission time based on baudrate
     */
    bool isTXComplete() {
        if (!transmitting) return false;

        // Calculate transmission time: (bits per byte * bytes * 1000000) / baudrate
        // Assuming 10 bits per byte (1 start + 8 data + 1 stop)
        uint32_t txTimeUs = (txLength * 10 * 1000000UL) / baud;
        uint32_t elapsed = timingMock.getMicros() - txStartTime;

        return elapsed >= txTimeUs;
    }

    /**
     * Switch back to RX mode after transmission
     */
    void switchToRX() {
        transmitting = false;
        switchToRXCallCount++;
    }

    bool isTransmitting() const { return transmitting; }

    /**
     * Get number of bytes available in RX buffer
     */
    int available() {
        if (rxHead >= rxTail) {
            return rxHead - rxTail;
        } else {
            return MOCK_UART_RX_BUFFER_SIZE - rxTail + rxHead;
        }
    }

    /**
     * Read one byte from RX buffer
     */
    int read() {
        if (available() == 0) return -1;

        uint8_t byte = rxBuffer[rxTail];
        rxTail = (rxTail + 1) % MOCK_UART_RX_BUFFER_SIZE;
        rxCallCount++;
        return byte;
    }

    void flush() {
        rxHead = 0;
        rxTail = 0;
    }

    // ==== Test Helper Methods ====

    /**
     * Inject bytes into RX buffer (simulate receiving data from module)
     */
    void injectRxData(const uint8_t* data, uint8_t length) {
        for (uint8_t i = 0; i < length; i++) {
            rxBuffer[rxHead] = data[i];
            rxHead = (rxHead + 1) % MOCK_UART_RX_BUFFER_SIZE;
        }
    }

    /**
     * Get last transmitted frame
     */
    const uint8_t* getLastTxFrame() const { return txBuffer; }
    uint8_t getLastTxLength() const { return txLength; }

    /**
     * Get call counts for verification
     */
    uint32_t getTxCallCount() const { return txCallCount; }
    uint32_t getRxCallCount() const { return rxCallCount; }
    uint32_t getSwitchToRXCallCount() const { return switchToRXCallCount; }

    /**
     * Reset statistics
     */
    void resetStats() {
        txCallCount = 0;
        rxCallCount = 0;
        switchToRXCallCount = 0;
    }

private:
    bool initialized;
    bool transmitting;
    uint8_t gpio;
    uint32_t baud;

    // TX tracking
    uint32_t txStartTime;
    uint8_t txBuffer[MOCK_UART_TX_BUFFER_SIZE];
    uint8_t txLength;

    // RX simulation
    uint8_t rxBuffer[MOCK_UART_RX_BUFFER_SIZE];
    uint16_t rxHead;
    uint16_t rxTail;

    // Statistics
    uint32_t txCallCount;
    uint32_t rxCallCount;
    uint32_t switchToRXCallCount;
};
