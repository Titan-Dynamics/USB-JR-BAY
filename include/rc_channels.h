/**
 * @file rc_channels.h
 * @brief RC channel value management and conversion
 * 
 * Manages 16 RC channels with conversion between microseconds (1000-2000)
 * and CRSF values (0-1984, center 992).
 */

#pragma once

#include <stdint.h>

class RCChannels {
public:
    static constexpr uint8_t NUM_CHANNELS = 16;
    
    RCChannels();
    
    /**
     * Set channel value in microseconds (1000-2000)
     * @param channel Channel number (1-16)
     * @param microseconds PWM value in microseconds
     */
    void setChannel(uint8_t channel, uint16_t microseconds);

    /**
     * Set all channels from CRSF values (0-1984)
     * @param crsfChannels Array of 16 CRSF channel values
     */
    void setChannelsCRSF(const uint16_t* crsfChannels);

    /**
     * Get channel value in CRSF format (0-1984)
     * @param channel Channel number (0-15)
     */
    uint16_t getChannelCRSF(uint8_t channel) const;
    
    /**
     * Get raw channel array (CRSF values)
     */
    const uint16_t* getChannels() const { return channels; }
    
    /**
     * Set all channels to center (1500us)
     */
    void centerAll();

private:
    uint16_t channels[NUM_CHANNELS];  // CRSF values (0-1984)
    
    uint16_t microsecondsToCRSF(uint16_t us) const;
};
