/**
 * @file module_sync.h
 * @brief Module timing synchronization
 * 
 * Handles timing correction frames from the ELRS module and calculates
 * adjusted RC frame periods to maintain synchronization.
 */

#pragma once

#include <stdint.h>

// Default RC frame period when no sync available (4ms = 250Hz)
constexpr uint32_t CRSF_DEFAULT_PERIOD_US = 4000;

/**
 * Callback type for timing frame updates
 * @param refreshRate Refresh rate from module in microseconds
 * @param inputLag Input lag/offset in microseconds
 */
typedef void (*ModuleSyncCallback)(int32_t refreshRate, int32_t inputLag);

class ModuleSync {
public:
    ModuleSync() : refreshRate(0), inputLag(0), lastUpdate(0), valid(false), callback(nullptr) {}
    
    /**
     * Set callback for timing updates
     * @param cb Callback function to call when timing frame is processed
     */
    void setTimingCallback(ModuleSyncCallback cb) { callback = cb; }
    
    /**
     * Update timing from a RADIO_ID frame
     * @param refreshRateUs Refresh rate in microseconds
     * @param inputLagUs Input lag/offset in microseconds
     */
    void updateTiming(int32_t refreshRateUs, int32_t inputLagUs);
    
    /**
     * Get the adjusted period for the next RC frame
     * Implements EdgeTX's adaptive synchronization algorithm
     * @return Period in microseconds
     */
    uint32_t getAdjustedPeriod();
    
    /**
     * Check if sync data is valid
     */
    bool isValid() const { return valid; }
    
    /**
     * Get base refresh rate from module
     */
    int32_t getRefreshRate() const { return refreshRate; }
    
    /**
     * Get current input lag
     */
    int32_t getInputLag() const { return inputLag; }
    
    /**
     * Get age of last sync update in milliseconds
     */
    uint32_t getAge(uint32_t currentMillis) const {
        return valid ? (currentMillis - lastUpdate) : 0;
    }

private:
    int32_t refreshRate;        // Module refresh rate (us)
    int32_t inputLag;           // Timing offset (us)
    uint32_t lastUpdate;        // Last update time (ms)
    bool valid;                 // Whether sync data is valid
    ModuleSyncCallback callback; // Optional callback for timing updates
};
