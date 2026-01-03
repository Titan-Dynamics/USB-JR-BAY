/**
 * @file arduino_mock.h
 * @brief Mock Arduino functions for native testing
 */

#pragma once

#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <algorithm>

// Mock Serial class for testing
class MockSerial {
public:
    MockSerial() {}

    int printf(const char* format, ...) {
        va_list args;
        va_start(args, format);
        int result = vprintf(format, args);
        va_end(args);
        return result;
    }
};

// Global mock serial instance
extern MockSerial Serial;

// Arduino utility functions
inline long map(long x, long in_min, long in_max, long out_min, long out_max) {
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
}

inline long constrain(long x, long a, long b) {
    return std::min(std::max(x, a), b);
}

// Time functions for testing
uint32_t micros();
uint32_t millis();

// Test control functions
void arduino_mock_set_time_us(uint32_t time_us);
void arduino_mock_advance_time_us(uint32_t delta_us);
void arduino_mock_reset_time();

// Define Arduino.h compatibility macro
#ifndef ARDUINO_H
#define ARDUINO_H
#endif
