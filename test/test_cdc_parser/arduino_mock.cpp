/**
 * @file arduino_mock.cpp
 * @brief Mock Arduino functions implementation
 */

#include "arduino_mock.h"

// Define global Serial mock
MockSerial Serial;

// Mock time tracking
static uint32_t mock_time_us = 0;

uint32_t micros() {
    return mock_time_us;
}

uint32_t millis() {
    return mock_time_us / 1000;
}

void arduino_mock_set_time_us(uint32_t time_us) {
    mock_time_us = time_us;
}

void arduino_mock_advance_time_us(uint32_t delta_us) {
    mock_time_us += delta_us;
}

void arduino_mock_reset_time() {
    mock_time_us = 0;
}
