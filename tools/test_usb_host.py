#!/usr/bin/env python3
"""
USB Host Protocol Test Script

This script sends RC channel data to the ESP32S3 CRSF TX Adapter
using the custom USB host protocol documented in USB_HOST_PROTOCOL.md.
It continuously loops through a sequence of tests until you stop it with Ctrl+C.

Usage:
    python test_usb_host.py [COM_PORT]
    # Loops forever; press Ctrl+C to stop

Example:
    python test_usb_host.py COM3
    python test_usb_host.py /dev/ttyUSB0
"""

import serial
import struct
import time
import sys

# Protocol constants
USB_SYNC0 = 0x55
USB_SYNC1 = 0xAA
USB_HTYPE_CHANNELS = 0x01

# CRSF raw range mapping to microseconds (approximate, linear)
CRSF_MIN_RAW = 172      # ~1000 µs
CRSF_MAX_RAW = 1811     # ~2000 µs
CRSF_CENTER_RAW = 992   # ~1500 µs
MICROS_MIN = 1000
MICROS_MAX = 2000

def us_to_raw(us):
    """Map microseconds [1000..2000] to CRSF raw [172..1811]."""
    us = max(MICROS_MIN, min(MICROS_MAX, int(us)))
    return int(round(
        CRSF_MIN_RAW + (us - MICROS_MIN) * (CRSF_MAX_RAW - CRSF_MIN_RAW) / (MICROS_MAX - MICROS_MIN)
    ))

def crc8_d5(data):
    """Calculate CRC8 with polynomial 0xD5"""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0xD5) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc

def send_channels(ser, channels):
    """
    Send 16 RC channels (0-2047) to ESP32
    
    Args:
        ser: Serial port object
        channels: List of 16 channel values (0-2047)
    """
    if len(channels) != 16:
        raise ValueError("Must provide exactly 16 channel values")
    
    # Pack 16 channels as little-endian 16-bit values
    payload = b''
    for ch in channels:
        payload += struct.pack('<H', min(int(ch), 2047))
    
    # Build frame
    frame_type = USB_HTYPE_CHANNELS
    length = 1 + len(payload)  # type + payload
    
    # Calculate CRC over type + payload
    crc_data = bytes([frame_type]) + payload
    crc = crc8_d5(crc_data)
    
    # Assemble complete frame
    frame = bytes([USB_SYNC0, USB_SYNC1]) + struct.pack('<H', length) + crc_data + bytes([crc])
    
    # Send frame
    ser.write(frame)
    return len(frame)

def run_test_cycle(ser):
        """
        Run a full test cycle:
            1) Centered sticks
            2) Min values
            3) Max values
            4) Throttle pump from min to max in 1µs steps over 3 seconds
            5) Return to center
        """
        # Test 1: Send centered sticks
        print("[Test 1] Sending centered sticks (992 = 1500µs)")
        channels = [992] * 16
        bytes_sent = send_channels(ser, channels)
        print(f"  Sent {bytes_sent} bytes")
        time.sleep(1)

        # Test 2: Send min values
        print("\n[Test 2] Sending min values (172 = 1000µs)")
        channels = [172] * 16
        send_channels(ser, channels)
        time.sleep(1)

        # Test 3: Send max values
        print("\n[Test 3] Sending max values (1811 = 2000µs)")
        channels = [1811] * 16
        send_channels(ser, channels)
        time.sleep(1)

        # Test 4: Smooth sweep in 1µs increments over 3 seconds
        print("\n[Test 4] Smooth throttle sweep 1000→2000µs in 1µs steps (3s)")
        duration_s = 3.0
        steps = MICROS_MAX - MICROS_MIN + 1  # inclusive range
        interval = duration_s / steps        # target interval per step
        start_time = time.perf_counter()
        next_time = start_time
        packet_count = 0

        for us in range(MICROS_MIN, MICROS_MAX + 1):
            ch0 = CRSF_CENTER_RAW
            ch1 = CRSF_CENTER_RAW
            ch2 = us_to_raw(us)
            ch3 = CRSF_CENTER_RAW
            channels = [ch0, ch1, ch2, ch3] + [CRSF_CENTER_RAW] * 12

            send_channels(ser, channels)
            packet_count += 1

            next_time += interval
            now = time.perf_counter()
            sleep_dt = next_time - now
            if sleep_dt > 0:
                time.sleep(sleep_dt)

        actual_duration = time.perf_counter() - start_time
        print(f"  Sent {packet_count} packets in {actual_duration:.3f}s (~{packet_count/max(actual_duration,1e-6):.1f} Hz)")

        # Test 5: Return to center
        print("\n[Test 5] Returning to center")
        channels = [992] * 16
        send_channels(ser, channels)

def main():
    # Get COM port from command line or use default
    port = sys.argv[1] if len(sys.argv) > 1 else 'COM3'

    print("=" * 60)
    print("USB Host Protocol Test - RC Channel Sender")
    print("=" * 60)
    print(f"Port: {port}")
    print(f"Baud: 115200")
    print(f"Protocol: Custom binary (55 AA ...)")
    print("=" * 60)
    print()

    ser = None
    try:
        # Open serial port
        ser = serial.Serial(port, 115200, timeout=0.1)
        print(f"[OK] Connected to {port}")
        print()

        # Wait for ESP32 to initialize
        time.sleep(2)
        print("Looping through tests. Press Ctrl+C to stop.\n")

        cycle = 1
        while True:
            print("-" * 60)
            print(f"Cycle {cycle}")
            print("-" * 60)
            run_test_cycle(ser)
            print("\n[Cycle complete]\n")
            cycle += 1
            time.sleep(1)

    except serial.SerialException as e:
        print(f"[ERROR] Serial port error: {e}")
        print(f"Make sure {port} is the correct port and not in use.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user")
        # fall through to finally to close serial
    finally:
        if 'ser' in locals() and ser is not None:
            try:
                if ser.is_open:
                    ser.close()
            except Exception:
                pass
        print("[INFO] Serial port closed. Goodbye.")
        # Exit 0 on Ctrl+C, 1 already handled above for SerialException
        # If not interrupted or error, this means normal termination

if __name__ == "__main__":
    main()
