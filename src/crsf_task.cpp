/**
 * @file crsf_task.cpp
 * @brief Main CRSF task implementation
 */

#ifndef UNIT_TEST
#include "crsf_task.h"
#endif

#include "crsf_protocol.h"
#include <Arduino.h>
#include <string.h>

CRSFTask::CRSFTask(CRSFUart& uart, CRSFParser& parser, CDCParser& cdcParser,
                   ModuleSync& moduleSync, RCChannels& channels)
    : uart(uart)
    , parser(parser)
    , cdcParser(cdcParser)
    , moduleSync(moduleSync)
    , channels(channels)
    , lastRCFrameTime(0)
    , rcFramesSent(0)
    , outputFrameLength(0)
    , outputFramePending(false)
{
}

bool CRSFTask::isTimeToSendRC() const
{
    uint32_t now = micros();
    uint32_t elapsed = now - lastRCFrameTime;
    uint32_t period = moduleSync.getAdjustedPeriod();
    return elapsed >= period;
}

void CRSFTask::sendRCFrame()
{
    uint8_t frame[CRSF_RC_FRAME_LEN];
    crsf_build_rc_frame(channels.getChannels(), frame);
    uart.transmit(frame, CRSF_RC_FRAME_LEN);
    rcFramesSent++;
}

bool CRSFTask::queueOutputFrame(const uint8_t* data, uint8_t length)
{
    if (outputFramePending || length > CRSF_MAX_FRAME_SIZE) {
        return false;
    }
    memcpy(outputFrameBuffer, data, length);
    outputFrameLength = length;
    outputFramePending = true;
    return true;
}

void CRSFTask::run()
{
    // Handle TX completion and switch to RX
    if (uart.isTransmitting() && uart.isTXComplete()) {
        uart.switchToRX();
    }

    // Process incoming data when not transmitting
    if (!uart.isTransmitting()) {
        while (uart.available()) {
            parser.processByte(uart.read());
        }
    }

    // Send RC frame or queued output frame (only if not in failsafe)
    if (!uart.isTransmitting() && isTimeToSendRC()) {
        // Check failsafe status - don't send RC frames if no recent CDC frames
        if (cdcParser.isFailsafe()) {
            // In failsafe mode - don't send RC frames
            // Update timestamp so we keep checking at the right interval
            lastRCFrameTime = micros();
            return;
        }

        if (outputFramePending) {
            uart.transmit(outputFrameBuffer, outputFrameLength);
            outputFramePending = false;
        } else {
            sendRCFrame();
        }
        lastRCFrameTime = micros();
    }
}
