#include "crsf_handler.h"
#include "debug.h"

// ============================================================================
// CrsfSerial Implementation
// ============================================================================

CrsfSerial::CrsfSerial() : uart(CRSF_UART_NUM), inTxMode(false)
{
}

void CrsfSerial::begin()
{
    // Initialize UART
    uart.begin(CRSF_BAUD, SERIAL_8N1, CRSF_UART_PIN, CRSF_UART_PIN);
    uart.setTimeout(0); // Non-blocking

    // Start in RX mode
    setRxMode();

    DBGPRINTLN("[CRSF] UART initialized");
}

void CrsfSerial::end()
{
    uart.end();
}

void CrsfSerial::setupGpioRx()
{
    // Set pin as input
    gpio_set_direction((gpio_num_t)CRSF_UART_PIN, GPIO_MODE_INPUT);

    // Configure for inverted signal (idle low)
    gpio_matrix_in((gpio_num_t)CRSF_UART_PIN, U0RXD_IN_IDX, true); // true = inverted
    gpio_pulldown_en((gpio_num_t)CRSF_UART_PIN);
    gpio_pullup_dis((gpio_num_t)CRSF_UART_PIN);
}

void CrsfSerial::setupGpioTx()
{
    // Set pin low initially (for inverted idle state)
    gpio_set_level((gpio_num_t)CRSF_UART_PIN, 0);
    gpio_set_pull_mode((gpio_num_t)CRSF_UART_PIN, GPIO_FLOATING);
    gpio_set_direction((gpio_num_t)CRSF_UART_PIN, GPIO_MODE_OUTPUT);

    // Disconnect RX and connect TX
    constexpr uint8_t MATRIX_DETACH_IN_LOW = 0x30;
    gpio_matrix_in(MATRIX_DETACH_IN_LOW, U0RXD_IN_IDX, false);
    gpio_matrix_out((gpio_num_t)CRSF_UART_PIN, U0TXD_OUT_IDX, true, false); // true = inverted
}

void CrsfSerial::setRxMode()
{
    if (!inTxMode)
        return;

    setupGpioRx();
    inTxMode = false;
}

void CrsfSerial::setTxMode()
{
    if (inTxMode)
        return;

    setupGpioTx();
    inTxMode = true;
}

bool CrsfSerial::isTxIdle()
{
    return uart_ll_is_tx_idle(UART_LL_GET_HW(CRSF_UART_NUM));
}

void CrsfSerial::write(const uint8_t *data, size_t len)
{
    uart.write(data, len);
}

void CrsfSerial::flush()
{
    uart.flush();

    // Wait for TX to actually complete
    while (!isTxIdle())
    {
        delayMicroseconds(1);
    }
}

int CrsfSerial::available()
{
    return uart.available();
}

uint8_t CrsfSerial::read()
{
    return uart.read();
}

void CrsfSerial::flushRx()
{
    while (uart.available())
    {
        uart.read();
    }
}

// ============================================================================
// CrsfPacketHandler Implementation
// ============================================================================

CrsfPacketHandler::CrsfPacketHandler()
    : lastRcPacketSent(0), rcPacketInterval(4000) // Default 250 Hz
      ,
      lastMixerSyncReceived(0), connected(false), rxIndex(0), packetReady(false), rxPacketCount(0), rxBadCrcCount(0), txPacketCount(0)
{
    // Initialize all channels to center position
    for (int i = 0; i < 16; i++)
    {
        channels[i] = CRSF_CHANNEL_VALUE_MID;
    }
}

void CrsfPacketHandler::begin()
{
    serial.begin();
    lastRcPacketSent = micros();

    DBGPRINTLN("[CRSF] Packet handler initialized");
    DBGPRINTF("[CRSF] Default RC rate: 250 Hz (%d µs interval)\n", rcPacketInterval);
}

void CrsfPacketHandler::update()
{
    // Process incoming data
    while (serial.available())
    {
        uint8_t byte = serial.read();
        processIncomingByte(byte);
    }

    // Process complete packets
    if (packetReady)
    {
        validateAndProcessPacket();
        packetReady = false;
    }

    // Send RC packet at regular intervals
    if (isTimeToSendRc())
    {
        sendRcChannels();
    }
}

void CrsfPacketHandler::processIncomingByte(uint8_t byte)
{
    // Look for sync byte (device address)
    if (rxIndex == 0)
    {
        // Valid addresses to start a packet
        if (byte == CRSF_ADDRESS_RADIO_TRANSMITTER ||
            byte == CRSF_ADDRESS_CRSF_TRANSMITTER ||
            byte == CRSF_ADDRESS_FLIGHT_CONTROLLER ||
            byte == CRSF_ADDRESS_BROADCAST)
        {
            rxBuffer[rxIndex++] = byte;
        }
        return;
    }

    // Add byte to buffer
    if (rxIndex < CRSF_MAX_PACKET_LEN)
    {
        rxBuffer[rxIndex++] = byte;
    }
    else
    {
        // Buffer overflow, reset
        rxIndex = 0;
        return;
    }

    // Check if we have enough for length field
    if (rxIndex >= 2)
    {
        uint8_t frameSize = rxBuffer[1];
        uint8_t totalSize = frameSize + 2; // +2 for addr and length fields

        // Sanity check
        if (totalSize > CRSF_MAX_PACKET_LEN || totalSize < 4)
        {
            // Invalid size, reset
            rxIndex = 0;
            return;
        }

        // Check if we have the complete packet
        if (rxIndex >= totalSize)
        {
            packetReady = true;
        }
    }
}

void CrsfPacketHandler::validateAndProcessPacket()
{
    if (rxIndex < 4)
    {
        rxIndex = 0;
        return;
    }

    uint8_t frameSize = rxBuffer[1];
    uint8_t totalSize = frameSize + 2;

    // Verify we have complete packet
    if (rxIndex < totalSize)
    {
        rxIndex = 0;
        return;
    }

    // Calculate and verify CRC
    uint8_t calculatedCrc = CrsfCrc::calc(&rxBuffer[2], frameSize - 1);
    uint8_t receivedCrc = rxBuffer[totalSize - 1];

    if (calculatedCrc != receivedCrc)
    {
        DBGPRINTF("[CRSF] CRC mismatch: calc=0x%02X recv=0x%02X type=0x%02X\n",
                  calculatedCrc, receivedCrc, rxBuffer[2]);
        rxBadCrcCount++;
        rxIndex = 0;
        return;
    }

    // Valid packet - process it
    rxPacketCount++;
    handlePacket(rxBuffer, totalSize);

    // Reset buffer for next packet
    rxIndex = 0;
}

void CrsfPacketHandler::handlePacket(const uint8_t *packet, uint8_t len)
{
    uint8_t type = packet[2];
    const uint8_t *payload = &packet[3];
    uint8_t payloadLen = packet[1] - 2; // frame_size - type - crc

    switch (type)
    {
    case CRSF_FRAMETYPE_LINK_STATISTICS:
        handleLinkStatistics(payload, payloadLen);
        break;

    case CRSF_FRAMETYPE_HANDSET:
        // Extended frame - check subtype
        if (payloadLen >= 3 && payload[2] == CRSF_HANDSET_SUBCMD_TIMING)
        {
            handleMixerSync(&payload[2], payloadLen - 2);
        }
        break;

    case CRSF_FRAMETYPE_DEVICE_PING:
        DBGPRINTLN("[CRSF] Received device ping");
        break;

    case CRSF_FRAMETYPE_DEVICE_INFO:
        DBGPRINTLN("[CRSF] Received device info");
        break;

    default:
        DBGPRINTF("[CRSF] Received packet type 0x%02X (len=%d)\n", type, len);
        break;
    }
}

void CrsfPacketHandler::handleLinkStatistics(const uint8_t *payload, uint8_t len)
{
    if (len < CRSF_LINK_STATISTICS_PAYLOAD_SIZE)
    {
        return;
    }

    const crsf_link_statistics_t *stats = (const crsf_link_statistics_t *)payload;

    // ELRS sends RSSI values as NEGATIVE signed int8_t in two's complement
    // e.g., byte 0xB1 = -79 as int8_t, which represents -79 dBm (already negative!)
    // Just cast to int8_t to get the correct signed value
    int8_t rssi_uplink_1 = (int8_t)stats->uplink_RSSI_1;
    int8_t rssi_uplink_2 = (int8_t)stats->uplink_RSSI_2;
    int8_t rssi_downlink_1 = (int8_t)stats->downlink_RSSI_1;

    // Print debug text link statistics if enabled
    DBGPRINTF("[Link] 1RSS=%d 2RSS=%d LQ=%d RSNR=%d RFMD=%d TPWR=%d TRSS=%d TLQ=%d TSNR=%d FLAGS=%d\n",
              rssi_uplink_1,
              rssi_uplink_2,
              stats->uplink_Link_quality,
              (int8_t)stats->uplink_SNR,
              stats->rf_Mode,
              stats->uplink_TX_Power,
              rssi_downlink_1,
              stats->downlink_Link_quality,
              (int8_t)stats->downlink_SNR,
              stats->active_antenna);

    // Send binary telemetry via USB protocol
    sendUsbTelemetry(
        rssi_uplink_1,                // 1RSS
        rssi_uplink_2,                // 2RSS
        stats->uplink_Link_quality,   // LQ
        (int8_t)stats->uplink_SNR,    // RSNR
        stats->rf_Mode,               // RFMD
        stats->uplink_TX_Power,       // TPWR
        rssi_downlink_1,              // TRSS
        stats->downlink_Link_quality, // TLQ
        (int8_t)stats->downlink_SNR,  // TSNR
        stats->active_antenna         // FLAGS
    );

    // Consider connected if we receive link stats
    if (!connected)
    {
        connected = true;
        DBGPRINTLN("[CRSF] Connected to ELRS module!");
    }
}

void CrsfPacketHandler::handleMixerSync(const uint8_t *payload, uint8_t len)
{
    if (len < sizeof(crsf_mixer_sync_t))
    {
        return;
    }

    const crsf_mixer_sync_t *sync = (const crsf_mixer_sync_t *)payload;

    // Extract big-endian values
    uint32_t rate_be = __builtin_bswap32(sync->rate);
    uint32_t offset_be = __builtin_bswap32(sync->offset);

    // Convert from 0.1µs to µs
    uint32_t newInterval = rate_be / 10;
    int32_t offset_us = (int32_t)offset_be / 10;

    // Sanity check
    if (newInterval < 500 || newInterval > 50000)
    {
        DBGPRINTF("[Sync] Invalid interval: %d µs\n", newInterval);
        return;
    }

    // Update RC packet interval
    if (newInterval != rcPacketInterval)
    {
        rcPacketInterval = newInterval;
        uint32_t frequency = 1000000 / rcPacketInterval;
        DBGPRINTF("[Sync] New RC rate: %d Hz (%d µs interval, offset=%d µs)\n",
                  frequency, rcPacketInterval, offset_us);
    }

    lastMixerSyncReceived = millis();
}

void CrsfPacketHandler::packChannels(uint8_t *dest, const uint16_t *channels)
{
    // Pack 16 channels (11 bits each) into 22 bytes
    crsf_channels_t *packed = (crsf_channels_t *)dest;

    packed->ch0 = channels[0];
    packed->ch1 = channels[1];
    packed->ch2 = channels[2];
    packed->ch3 = channels[3];
    packed->ch4 = channels[4];
    packed->ch5 = channels[5];
    packed->ch6 = channels[6];
    packed->ch7 = channels[7];
    packed->ch8 = channels[8];
    packed->ch9 = channels[9];
    packed->ch10 = channels[10];
    packed->ch11 = channels[11];
    packed->ch12 = channels[12];
    packed->ch13 = channels[13];
    packed->ch14 = channels[14];
    packed->ch15 = channels[15];
}

void CrsfPacketHandler::sendRcChannels()
{
    uint8_t packet[26];

    // Build packet header
    packet[0] = CRSF_ADDRESS_FLIGHT_CONTROLLER; // To FC/RX
    packet[1] = 24;                             // Frame size (type + 22 bytes payload + crc)
    packet[2] = CRSF_FRAMETYPE_RC_CHANNELS_PACKED;

    // Pack channels
    packChannels(&packet[3], channels);

    // Calculate CRC (over type + payload)
    packet[25] = CrsfCrc::calc(&packet[2], 23);

    // Switch to TX mode
    serial.setTxMode();

    // Send packet
    serial.write(packet, 26);
    serial.flush();

    // Switch back to RX mode
    serial.setRxMode();
    serial.flushRx(); // Discard any loopback

    // Update timing
    lastRcPacketSent = micros();
    txPacketCount++;

    // Periodic status
    static uint32_t lastStatus = 0;
    if (millis() - lastStatus >= 5000)
    {
        DBGPRINTF("[CRSF] Status: TX=%d, RX=%d, CRC_Err=%d, Rate=%dHz\n",
                  txPacketCount, rxPacketCount, rxBadCrcCount,
                  1000000 / rcPacketInterval);
        lastStatus = millis();
    }
}

void CrsfPacketHandler::setChannelValue(uint8_t channel, uint16_t value)
{
    if (channel < 16)
    {
        channels[channel] = value;
    }
}

bool CrsfPacketHandler::isTimeToSendRc()
{
    uint32_t now = micros();
    uint32_t elapsed = now - lastRcPacketSent;
    return elapsed >= rcPacketInterval;
}

void CrsfPacketHandler::updateRcTiming()
{
    // Check if we haven't received mixer sync in a while
    if (connected && lastMixerSyncReceived > 0)
    {
        if (millis() - lastMixerSyncReceived > 2000)
        {
            // Lost sync, revert to default
            DBGPRINTLN("[CRSF] Lost mixer sync, reverting to default rate");
            rcPacketInterval = 4000; // 250 Hz
            lastMixerSyncReceived = 0;
        }
    }
}

void CrsfPacketHandler::sendUsbTelemetry(int8_t rss1, int8_t rss2, uint8_t lq, int8_t rsnr,
                                         uint8_t rfmd, uint8_t tpwr, int8_t trss, uint8_t tlq,
                                         int8_t tsnr, uint8_t flags)
{
    // USB Protocol Frame Format (LINK_STATS.md):
    // [HEADER0][HEADER1][LENGTH_L][LENGTH_H][TYPE][PAYLOAD][CRC]
    // TYPE_TEL = 0x02, Payload = 10 bytes

    uint8_t frame[16];
    uint8_t idx = 0;

    // Header
    frame[idx++] = 0x55; // HEADER0
    frame[idx++] = 0xAA; // HEADER1

    // Length (TYPE + PAYLOAD = 1 + 10 = 11 bytes)
    uint16_t length = 11;
    frame[idx++] = length & 0xFF;        // LENGTH_L (low byte)
    frame[idx++] = (length >> 8) & 0xFF; // LENGTH_H (high byte)

    // Type
    frame[idx++] = 0x02; // TYPE_TEL

    // Payload (10 bytes) - TYPE_TEL format from LINK_STATS.md
    frame[idx++] = (uint8_t)rss1; // 1RSS (signed as uint8_t)
    frame[idx++] = (uint8_t)rss2; // 2RSS (signed as uint8_t)
    frame[idx++] = lq;            // LQ
    frame[idx++] = (uint8_t)rsnr; // RSNR (signed as uint8_t)
    frame[idx++] = rfmd;          // RFMD
    frame[idx++] = tpwr;          // TPWR
    frame[idx++] = (uint8_t)trss; // TRSS (signed as uint8_t)
    frame[idx++] = tlq;           // TLQ
    frame[idx++] = (uint8_t)tsnr; // TSNR (signed as uint8_t)
    frame[idx++] = flags;         // FLAGS

    // CRC (over TYPE + PAYLOAD)
    // Use CRSF CRC which matches the 0xD5 polynomial from LINK_STATS.md
    frame[idx++] = CrsfCrc::calc(&frame[4], length);

    // Send frame (idx should be 15)
    Serial.write(frame, idx);
}
