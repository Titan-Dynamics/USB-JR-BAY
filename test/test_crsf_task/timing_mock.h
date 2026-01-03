/**
 * @file timing_mock.h
 * @brief Mock timing functions for crsfTask testing
 *
 * Provides controllable time simulation for testing timing-dependent behavior
 */

#pragma once

#include <stdint.h>

/**
 * Mock timing controller
 * Allows tests to control time advancement
 */
class TimingMock {
public:
    TimingMock() : currentMicros(0), currentMillis(0) {}

    /**
     * Get current microseconds
     */
    uint32_t getMicros() const { return currentMicros; }

    /**
     * Get current milliseconds
     */
    uint32_t getMillis() const { return currentMillis; }

    /**
     * Set absolute time in microseconds
     */
    void setMicros(uint32_t us) {
        currentMicros = us;
        currentMillis = us / 1000;
    }

    /**
     * Advance time by microseconds
     */
    void advanceMicros(uint32_t us) {
        currentMicros += us;
        currentMillis = currentMicros / 1000;
    }

    /**
     * Advance time by milliseconds
     */
    void advanceMillis(uint32_t ms) {
        advanceMicros(ms * 1000);
    }

    /**
     * Reset time to zero
     */
    void reset() {
        currentMicros = 0;
        currentMillis = 0;
    }

private:
    uint32_t currentMicros;
    uint32_t currentMillis;
};

// Global timing mock instance
extern TimingMock timingMock;

// Mock Arduino timing functions
inline uint32_t micros() {
    return timingMock.getMicros();
}

inline uint32_t millis() {
    return timingMock.getMillis();
}

inline void delay(uint32_t ms) {
    // No-op in tests
    (void)ms;
}
