/**
 * @file crsf_uart.h
 * @brief Half-duplex UART management for ESP32
 * 
 * Handles ESP32 GPIO matrix configuration for single-wire half-duplex UART.
 * Controls TX/RX switching and completion detection.
 */

#pragma once

#include <Arduino.h>

class CRSFUart {
public:
    /**
     * Initialize the UART in half-duplex mode
     * @param pin GPIO pin number for half-duplex communication
     * @param baudrate UART baudrate
     */
    void begin(uint8_t pin, uint32_t baudrate);
    
    /**
     * Check if initialization was successful
     */
    bool isInitialized() const { return initialized; }
    
    /**
     * Switch to transmit mode and send data
     * @param data Buffer to transmit
     * @param length Number of bytes to send
     */
    void transmit(const uint8_t* data, uint8_t length);
    
    /**
     * Check if transmission is complete (all bytes shifted out)
     * @return true if TX is idle
     */
    bool isTXComplete();
    
    /**
     * Switch back to receive mode after transmission
     * Automatically flushes any echo bytes
     */
    void switchToRX();
    
    /**
     * Check if currently transmitting
     */
    bool isTransmitting() const { return transmitting; }
    
    /**
     * Get number of available received bytes
     */
    int available();
    
    /**
     * Read one byte from RX buffer
     */
    int read();
    
    /**
     * Flush RX buffer
     */
    void flush();

private:
    HardwareSerial* serial;
    uint8_t gpio;
    uint8_t uartNum;
    uint32_t baud;
    bool initialized;
    bool transmitting;
    
    void setRXMode();
    void setTXMode();
};
