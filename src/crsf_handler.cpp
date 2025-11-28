#include "crsf_handler.h"
#include "debug.h"

// Minimal CrsfSerial implementation — used for half-duplex bridging.

CrsfSerial::CrsfSerial() : uart(CRSF_UART_NUM), inTxMode(false)
{
}

void CrsfSerial::begin()
{
    // Initialize UART at the CRSF baud rate.
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
    // Set pin as input (inverted)
    gpio_set_direction((gpio_num_t)CRSF_UART_PIN, GPIO_MODE_INPUT);
    gpio_matrix_in((gpio_num_t)CRSF_UART_PIN, U0RXD_IN_IDX, true); // true = inverted
    gpio_pulldown_en((gpio_num_t)CRSF_UART_PIN);
    gpio_pullup_dis((gpio_num_t)CRSF_UART_PIN);
}

void CrsfSerial::setupGpioTx()
{
    // Set pin low initially (for inverted idle state) and output
    gpio_set_level((gpio_num_t)CRSF_UART_PIN, 0);
    gpio_set_pull_mode((gpio_num_t)CRSF_UART_PIN, GPIO_FLOATING);
    gpio_set_direction((gpio_num_t)CRSF_UART_PIN, GPIO_MODE_OUTPUT);

    // Disconnect RX and connect TX (inverted)
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

