/**
 * @file rc_channels.cpp
 * @brief RC channel management implementation
 */

#include "rc_channels.h"
#include "crsf_protocol.h"
#include <Arduino.h>

RCChannels::RCChannels()
{
    centerAll();
}

void RCChannels::centerAll()
{
    for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
        channels[i] = CRSF_CHANNEL_CENTER;
    }
}

void RCChannels::setChannel(uint8_t channel, uint16_t microseconds)
{
    if (channel < 1 || channel > NUM_CHANNELS) return;

    channels[channel - 1] = microsecondsToCRSF(microseconds);
}

void RCChannels::setChannelsCRSF(const uint16_t* crsfChannels)
{
    for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
        channels[i] = crsfChannels[i];
    }
}

uint16_t RCChannels::getChannelCRSF(uint8_t channel) const
{
    if (channel >= NUM_CHANNELS) return CRSF_CHANNEL_CENTER;
    return channels[channel];
}

uint16_t RCChannels::microsecondsToCRSF(uint16_t us) const
{
    // Convert microseconds (1000-2000) to CRSF value (191-1792)
    // Using ELRS standard range mapping
    int32_t crsf = map(us, 1000, 2000, 191, 1792);
    crsf = constrain(crsf, CRSF_CHANNEL_MIN, CRSF_CHANNEL_MAX);
    return (uint16_t)crsf;
}
