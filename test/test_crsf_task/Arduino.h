/**
 * @file Arduino.h
 * @brief Arduino.h wrapper for crsfTask testing
 *
 * Includes timing mocks and Serial mocks for integration testing
 */

#pragma once

#include <stdint.h>
#include <stdarg.h>
#include <stdio.h>
#include <algorithm>
#include <string.h>

// Include timing mocks first
#include "timing_mock.h"

// Mock Serial class for testing
class MockSerial {
public:
    MockSerial() : begun(false), writeCallCount(0) {}

    void begin(uint32_t baud) {
        begun = true;
        (void)baud;
    }

    int printf(const char* format, ...) {
        va_list args;
        va_start(args, format);
        int result = vprintf(format, args);
        va_end(args);
        return result;
    }

    void println(const char* str = "") {
        printf("%s\n", str);
    }

    size_t write(const uint8_t* data, size_t length) {
        writeCallCount++;
        // In tests, we could capture this data if needed
        (void)data;
        return length;
    }

    operator bool() const { return begun; }

    uint32_t getWriteCallCount() const { return writeCallCount; }
    void resetStats() { writeCallCount = 0; }

private:
    bool begun;
    uint32_t writeCallCount;
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

// Define min/max for compatibility
template<typename T>
inline T min(T a, T b) {
    return std::min(a, b);
}

template<typename T>
inline T max(T a, T b) {
    return std::max(a, b);
}

// Mock HardwareSerial (not used in tests, but defined to avoid compile errors)
class HardwareSerial {};

#define ARDUINO_H
