/**
 * @file crsf_uart.cpp
 * @brief Half-duplex UART implementation for ESP32
 */

#include "crsf_uart.h"
#include <hal/uart_ll.h>
#include <driver/gpio.h>

void CRSFUart::begin(uint8_t pin, uint32_t baudrate)
{
    gpio = pin;
    baud = baudrate;
    uartNum = 1;  // Use UART1 (UART0 is typically USB CDC)
    initialized = false;
    transmitting = false;
    
    // Create HardwareSerial instance
    serial = new HardwareSerial(uartNum);
    
    // Initialize UART with same pin for TX and RX (half-duplex)
    serial->begin(baudrate, SERIAL_8N1, pin, pin, false, 0);
    serial->setTimeout(0);
    
    // Start in receive mode
    setRXMode();
    
    initialized = true;
}

void CRSFUart::setRXMode()
{
    // Set GPIO direction to input
    gpio_set_direction((gpio_num_t)gpio, GPIO_MODE_INPUT);
    
    // Connect GPIO to UART RX input (not inverted)
    gpio_matrix_in((gpio_num_t)gpio, U1RXD_IN_IDX, false);
    
    // Enable pull-up for idle-high (normal UART)
    gpio_pullup_en((gpio_num_t)gpio);
    gpio_pulldown_dis((gpio_num_t)gpio);
}

void CRSFUart::setTXMode()
{
    // Disable pull resistors
    gpio_set_pull_mode((gpio_num_t)gpio, GPIO_FLOATING);
    
    // Set idle state HIGH (normal UART)
    gpio_set_level((gpio_num_t)gpio, 1);
    gpio_set_direction((gpio_num_t)gpio, GPIO_MODE_OUTPUT);
    
    // Disconnect UART RX by routing constant HIGH to it
    // This prevents receiving our own transmitted bytes
    constexpr uint8_t MATRIX_DETACH_IN_HIGH = 0x38;
    gpio_matrix_in(MATRIX_DETACH_IN_HIGH, U1RXD_IN_IDX, false);
    
    // Connect UART TX to GPIO (not inverted)
    gpio_matrix_out((gpio_num_t)gpio, U1TXD_OUT_IDX, false, false);
}

void CRSFUart::transmit(const uint8_t* data, uint8_t length)
{
    if (!initialized) return;
    
    setTXMode();
    transmitting = true;
    serial->write(data, length);
}

bool CRSFUart::isTXComplete()
{
    if (!initialized || !transmitting) return true;
    return uart_ll_is_tx_idle(UART_LL_GET_HW(uartNum));
}

void CRSFUart::switchToRX()
{
    if (!transmitting) return;
    
    transmitting = false;
    setRXMode();
    flush();  // Discard any echo bytes
}

int CRSFUart::available()
{
    if (!initialized) return 0;
    return serial->available();
}

int CRSFUart::read()
{
    if (!initialized) return -1;
    return serial->read();
}

void CRSFUart::flush()
{
    if (!initialized) return;
    while (serial->available()) {
        serial->read();
    }
}
