/**
 * @file module_sync.cpp
 * @brief Module timing synchronization implementation
 */

#include "module_sync.h"
#include "crsf_protocol.h"
#include <Arduino.h>

// Timing limits
constexpr uint32_t CRSF_MIN_PERIOD_US = 1000;
constexpr uint32_t CRSF_MAX_PERIOD_US = 50000;

void ModuleSync::updateTiming(int32_t refreshRateUs, int32_t inputLagUs)
{
    refreshRate = refreshRateUs;
    inputLag = inputLagUs;
    lastUpdate = millis();
    valid = true;
    
    // Notify callback if set
    if (callback) {
        callback(refreshRate, inputLag);
    }
}

uint32_t ModuleSync::getAdjustedPeriod()
{
    if (!valid) {
        return CRSF_DEFAULT_PERIOD_US;
    }
    
    // Apply the lag correction (EdgeTX algorithm)
    int32_t adjustedPeriod = refreshRate + inputLag;
    
    // Clamp to valid range
    if (adjustedPeriod < (int32_t)CRSF_MIN_PERIOD_US) {
        adjustedPeriod = CRSF_MIN_PERIOD_US;
    } else if (adjustedPeriod > (int32_t)CRSF_MAX_PERIOD_US) {
        adjustedPeriod = CRSF_MAX_PERIOD_US;
    }
    
    // Consume the lag
    inputLag -= (adjustedPeriod - refreshRate);
    
    return (uint32_t)adjustedPeriod;
}
