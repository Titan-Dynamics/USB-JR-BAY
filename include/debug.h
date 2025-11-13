#pragma once

#include <Arduino.h>

// ============================================================================
// Debug Output Control
// ============================================================================

/**
 * Debug Output Library
 *
 * Provides macros for conditional debug output that can be enabled/disabled
 * at compile time or runtime.
 *
 * Usage:
 *   DBGPRINT("Hello");           // Like Serial.print()
 *   DBGPRINTLN("World");         // Like Serial.println()
 *   DBGPRINTF("Value: %d\n", x); // Like Serial.printf()
 *
 * Control:
 *   - Compile-time: Define DEBUG_ENABLED=0 to disable all debug output
 *   - Runtime: Call Debug::setEnabled(false) to disable dynamically
 */

// ============================================================================
// Debug Control Class
// ============================================================================

class Debug
{
public:
    /**
     * Enable or disable debug output at runtime
     * @param enabled true to enable debug output, false to disable
     */
    static void setEnabled(bool enabled)
    {
        debugEnabled = enabled;
    }

    /**
     * Check if debug output is enabled
     * @return true if debug output is enabled
     */
    static bool isEnabled()
    {
        return debugEnabled;
    }

private:
    static bool debugEnabled;
};

// ============================================================================
// Debug Macros
// ============================================================================

// Compile-time enable/disable (default: enabled)
#ifndef DEBUG_ENABLED
#define DEBUG_ENABLED 1
#endif

#if DEBUG_ENABLED

// Debug print (no newline)
#define DBGPRINT(...)                  \
    do                                 \
    {                                  \
        if (Debug::isEnabled())        \
        {                              \
            Serial.print(__VA_ARGS__); \
        }                              \
    } while (0)

// Debug print with newline
#define DBGPRINTLN(...)                  \
    do                                   \
    {                                    \
        if (Debug::isEnabled())          \
        {                                \
            Serial.println(__VA_ARGS__); \
        }                                \
    } while (0)

// Debug printf (formatted output)
#define DBGPRINTF(...)                  \
    do                                  \
    {                                   \
        if (Debug::isEnabled())         \
        {                               \
            Serial.printf(__VA_ARGS__); \
        }                               \
    } while (0)

// Debug write (raw bytes)
#define DBGWRITE(data, len)          \
    do                               \
    {                                \
        if (Debug::isEnabled())      \
        {                            \
            Serial.write(data, len); \
        }                            \
    } while (0)

#else

// Compile-time disabled - no-ops
#define DBGPRINT(...) \
    do                \
    {                 \
    } while (0)
#define DBGPRINTLN(...) \
    do                  \
    {                   \
    } while (0)
#define DBGPRINTF(...) \
    do                 \
    {                  \
    } while (0)
#define DBGWRITE(data, len) \
    do                      \
    {                       \
    } while (0)

#endif // DEBUG_ENABLED
