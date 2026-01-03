/**
 * @file Arduino.h
 * @brief Arduino.h wrapper for native testing
 *
 * This file acts as a drop-in replacement for Arduino.h when compiling
 * for the native test environment. It includes our mock implementations.
 */

#pragma once

#include "arduino_mock.h"
