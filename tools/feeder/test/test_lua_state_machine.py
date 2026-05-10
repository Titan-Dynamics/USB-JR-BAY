"""
Unit tests for LuaStateMachine.

Drives the state machine with synthetic CRSF frames. A StubSend collects
outbound bytes. Simulated time values are passed directly to tick() and
_request_parameter() — no patching of time.monotonic() needed.
"""

import struct
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from crsf_protocol import (
    CRSF_SYNC_BYTE,
    CRSF_ADDRESS_BROADCAST,
    CRSF_ADDRESS_RADIO,
    CRSF_ADDRESS_MODULE,
    CRSF_ADDRESS_ELRS_LUA,
    CRSF_FRAMETYPE_PING_DEVICES,
    CRSF_FRAMETYPE_DEVICE_INFO,
    CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY,
    CRSF_FRAMETYPE_PARAMETER_READ,
    CRSF_FRAMETYPE_PARAMETER_WRITE,
    crsf_crc8,
)
from device_parameters import (
    PARAM_TYPE_UINT8, PARAM_TYPE_TEXT_SELECTION, ELRS_SERIAL_NUMBER,
)
from lua_state_machine import LuaStateMachine


class StubSend:
    """Captures outbound frames and always returns True."""
    def __init__(self):
        self.queued = []

    def __call__(self, frame: bytes) -> bool:
        self.queued.append(bytes(frame))
        return True

    def pop_all(self):
        frames, self.queued = list(self.queued), []
        return frames


def _build_device_info_payload(src, name, serial_number, n_params=10):
    """Build a DEVICE_INFO extended frame payload (dest+src prefix included)."""
    body = (
        name.encode() + b'\x00'
        + struct.pack('>I', serial_number)
        + struct.pack('>I', 0x00000001)  # hardwareId
        + struct.pack('>I', 0x00000002)  # firmwareId
        + bytes([n_params, 0])
    )
    return bytes([CRSF_ADDRESS_RADIO, src]) + body


def _build_device_info_frame(src, name, serial_number, n_params=10):
    """Build a complete DEVICE_INFO frame dict (as CRSFFrameDecoder would produce)."""
    payload = _build_device_info_payload(src, name, serial_number, n_params)
    return {
        'valid':   True,
        'type':    CRSF_FRAMETYPE_DEVICE_INFO,
        'payload': payload,
    }


def _build_param_entry_frame(dst, src, field_id, chunks_remaining, chunk_data):
    """Build a complete PARAMETER_SETTINGS_ENTRY frame dict."""
    payload = bytes([dst, src, field_id, chunks_remaining]) + bytes(chunk_data)
    return {
        'valid':   True,
        'type':    CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY,
        'payload': payload,
    }


def _uint8_blob(parent, name, value, min_=0, max_=255, default=0):
    return (
        bytes([parent, PARAM_TYPE_UINT8])
        + name.encode() + b'\x00'
        + bytes([value, min_, max_, default])
        + b'\x00'
    )


def _elrs_device(n_params=5):
    return {
        'address':         CRSF_ADDRESS_MODULE,
        'isElrs':          True,
        'parametersTotal': n_params,
        'name':            'TitanLRS TX',
    }


def _non_elrs_device():
    return {
        'address':         0xEC,
        'isElrs':          False,
        'parametersTotal': 3,
        'name':            'SomeRX',
    }


class TestScanDevices(unittest.TestCase):
    def setUp(self):
        self.send = StubSend()
        self.lua  = LuaStateMachine(send=self.send)

    def test_scan_emits_broadcast_ping_with_origin_0xEA(self):
        self.lua.scan_devices()
        frames = self.send.queued
        self.assertEqual(len(frames), 1)
        f = frames[0]
        # Frame layout: [SYNC, len, type, dest, origin, CRC]
        self.assertEqual(f[2], CRSF_FRAMETYPE_PING_DEVICES)
        self.assertEqual(f[3], CRSF_ADDRESS_BROADCAST)
        self.assertEqual(f[4], CRSF_ADDRESS_RADIO)
        # Verify CRC
        self.assertEqual(f[5], crsf_crc8(f[2:5]))

    def test_scan_clears_devices_and_fires_callback(self):
        self.lua.devices = [{'address': 0xEE}]
        changed = []
        self.lua.on_devices_changed = lambda d: changed.append(list(d))
        self.lua.scan_devices()
        self.assertEqual(changed[-1], [])


class TestDeviceInfoHandling(unittest.TestCase):
    def setUp(self):
        self.send = StubSend()
        self.lua  = LuaStateMachine(send=self.send)

    def test_device_info_during_scan_window_appends(self):
        changed = []
        self.lua.on_devices_changed = lambda d: changed.append(list(d))
        self.lua.scan_devices()

        frame = _build_device_info_frame(
            CRSF_ADDRESS_MODULE, 'TitanLRS TX', ELRS_SERIAL_NUMBER, 47
        )
        self.lua.handle_frame(frame)

        self.assertTrue(len(changed) >= 1)
        devices = changed[-1]
        self.assertEqual(len(devices), 1)
        self.assertTrue(devices[0]['isElrs'])
        self.assertEqual(devices[0]['address'], CRSF_ADDRESS_MODULE)
        self.assertEqual(devices[0]['parametersTotal'], 47)

    def test_scan_window_expires_after_timeout(self):
        t0 = 100.0
        self.lua._scan_deadline = t0 + 2.0

        # Before expiry: device added
        frame = _build_device_info_frame(0xEE, 'TX', ELRS_SERIAL_NUMBER)
        self.lua.handle_frame(frame)
        self.assertEqual(len(self.lua.devices), 1)

        # Expire the window
        self.lua.tick(t0 + 2.001)
        self.assertIsNone(self.lua._scan_deadline)

        # After expiry + no selected device: new address NOT added
        frame2 = _build_device_info_frame(0xEC, 'RX', 0xDEADBEEF)
        self.lua.handle_frame(frame2)
        self.assertEqual(len(self.lua.devices), 1)  # still just one


class TestSelectDevice(unittest.TestCase):
    def setUp(self):
        self.send = StubSend()
        self.lua  = LuaStateMachine(send=self.send)

    def test_select_elrs_tx_switches_origin_to_0xEF(self):
        self.lua.select_device(_elrs_device(n_params=5))
        self.assertEqual(self.lua.origin_address, CRSF_ADDRESS_ELRS_LUA)
        # The first PARAM_READ frame must have origin=0xEF
        frames = [f for f in self.send.queued
                  if f[2] == CRSF_FRAMETYPE_PARAMETER_READ]
        self.assertTrue(len(frames) >= 1)
        self.assertEqual(frames[0][4], CRSF_ADDRESS_ELRS_LUA)

    def test_select_non_elrs_keeps_origin_0xEA(self):
        self.lua.select_device(_non_elrs_device())
        self.assertEqual(self.lua.origin_address, CRSF_ADDRESS_RADIO)
        frames = [f for f in self.send.queued
                  if f[2] == CRSF_FRAMETYPE_PARAMETER_READ]
        self.assertTrue(len(frames) >= 1)
        self.assertEqual(frames[0][4], CRSF_ADDRESS_RADIO)


class TestLoadParameters(unittest.TestCase):
    def setUp(self):
        self.send = StubSend()
        self.lua  = LuaStateMachine(send=self.send)
        self.lua.select_device(_elrs_device(n_params=3))
        self.send.queued = []  # clear setup frames

    def test_load_first_param_request(self):
        self.lua.load_parameters()
        frames = [f for f in self.send.queued if f[2] == CRSF_FRAMETYPE_PARAMETER_READ]
        self.assertTrue(len(frames) >= 1)
        f = frames[0]
        self.assertEqual(f[5], 1)  # param_index = 1
        self.assertEqual(f[6], 0)  # chunk_number = 0

    def test_single_chunk_param_completes(self):
        updated = []
        self.lua.on_field_updated = lambda p: updated.append(p)
        self.lua.load_parameters()
        self.send.queued = []

        blob = _uint8_blob(0, 'TX Power', 10, 0, 100, 10, )
        frame = _build_param_entry_frame(
            CRSF_ADDRESS_ELRS_LUA, CRSF_ADDRESS_MODULE, 1, 0, blob
        )
        self.lua.handle_frame(frame)

        self.assertEqual(self.lua.loaded_count, 1)
        self.assertIn(1, self.lua.parameters)
        self.assertEqual(self.lua.parameters[1]['name'], 'TX Power')
        self.assertTrue(len(updated) >= 1)
        # Next PARAM_READ should be for param 2
        next_reads = [f for f in self.send.queued if f[2] == CRSF_FRAMETYPE_PARAMETER_READ]
        self.assertTrue(len(next_reads) >= 1)
        self.assertEqual(next_reads[0][5], 2)

    def test_multi_chunk_param_assembles(self):
        """Three chunks with chunks_remaining 2, 1, 0 should produce one param."""
        updated = []
        self.lua.on_field_updated = lambda p: updated.append(p)
        self.lua.load_parameters()
        self.send.queued = []

        blob = _uint8_blob(0, 'Telem Ratio', 3, 0, 7, 0)
        third   = len(blob) // 3
        chunk0  = blob[:third]
        chunk1  = blob[third:2 * third]
        chunk2  = blob[2 * third:]

        self.lua.handle_frame(
            _build_param_entry_frame(CRSF_ADDRESS_ELRS_LUA, CRSF_ADDRESS_MODULE, 1, 2, chunk0)
        )
        self.assertNotIn(1, self.lua.parameters)

        self.lua.handle_frame(
            _build_param_entry_frame(CRSF_ADDRESS_ELRS_LUA, CRSF_ADDRESS_MODULE, 1, 1, chunk1)
        )
        self.assertNotIn(1, self.lua.parameters)

        self.lua.handle_frame(
            _build_param_entry_frame(CRSF_ADDRESS_ELRS_LUA, CRSF_ADDRESS_MODULE, 1, 0, chunk2)
        )
        self.assertIn(1, self.lua.parameters)
        self.assertEqual(len(updated), 1)

    def test_duplicate_final_chunk_is_silently_ignored(self):
        """Firmware always sends the final chunk twice.

        The duplicate arrives after pending_param_number has advanced, so the
        dedup set is already cleared.  It must be recognised as a late duplicate
        (not re-parsed and not logged as 'dropped').
        """
        updated = []
        self.lua.on_field_updated = lambda p: updated.append(p)
        dropped_msgs = []
        self.lua.on_debug = lambda msg: dropped_msgs.append(msg) if 'dropped' in msg else None
        self.lua.load_parameters()
        self.send.queued = []

        blob = _uint8_blob(0, 'TX Power', 10)
        frame = _build_param_entry_frame(
            CRSF_ADDRESS_ELRS_LUA, CRSF_ADDRESS_MODULE, 1, 0, blob
        )

        # First copy: param 1 is parsed, pending advances to 2.
        self.lua.handle_frame(frame)
        self.assertEqual(self.lua.loaded_count, 1)
        self.assertIn(1, self.lua.parameters)

        # Second copy (late duplicate): must not re-parse and must not 'drop'.
        self.lua.handle_frame(frame)
        self.assertEqual(self.lua.loaded_count, 1)
        self.assertEqual(len(updated), 1)
        self.assertEqual(dropped_msgs, [])

    def test_duplicate_chunk_frames_are_deduplicated(self):
        """Firmware sends each non-final chunk twice; only one copy must be used."""
        updated = []
        self.lua.on_field_updated = lambda p: updated.append(p)
        self.lua.load_parameters()
        self.send.queued = []

        blob = _uint8_blob(0, 'Packet Rate', 2, 0, 5, 0)
        half = len(blob) // 2
        chunk0 = blob[:half]
        chunk1 = blob[half:]

        # chunk 0 arrives twice (firmware redundancy)
        self.lua.handle_frame(
            _build_param_entry_frame(CRSF_ADDRESS_ELRS_LUA, CRSF_ADDRESS_MODULE, 1, 1, chunk0)
        )
        self.lua.handle_frame(
            _build_param_entry_frame(CRSF_ADDRESS_ELRS_LUA, CRSF_ADDRESS_MODULE, 1, 1, chunk0)
        )
        self.assertNotIn(1, self.lua.parameters)
        # Only one chunk should have been stored
        self.assertEqual(len(self.lua.pending_chunks), 1)

        # final chunk arrives (once)
        self.lua.handle_frame(
            _build_param_entry_frame(CRSF_ADDRESS_ELRS_LUA, CRSF_ADDRESS_MODULE, 1, 0, chunk1)
        )
        self.assertIn(1, self.lua.parameters)
        self.assertEqual(self.lua.parameters[1]['name'], 'Packet Rate')
        self.assertEqual(len(updated), 1)


class TestTimeoutAndRetry(unittest.TestCase):
    def setUp(self):
        self.send = StubSend()
        self.lua  = LuaStateMachine(send=self.send)
        self.lua.select_device(_elrs_device(n_params=3))
        self.send.queued = []

    def test_timeout_retries_up_to_three_times(self):
        t0 = 0.0
        self.lua.load_parameters()
        self.send.queued = []

        # First timeout → retry 1
        self.lua._param_deadline = t0 + LuaStateMachine.PARAM_TIMEOUT_S
        self.lua.tick(t0 + LuaStateMachine.PARAM_TIMEOUT_S + 0.001)
        self.assertEqual(self.lua.retry_count, 1)
        self.assertNotIn(1, self.lua.missing_params)
        self.send.queued = []

        # Second timeout → retry 2
        self.lua._param_deadline = t0 + LuaStateMachine.PARAM_TIMEOUT_S
        self.lua.tick(t0 + LuaStateMachine.PARAM_TIMEOUT_S + 0.001)
        self.assertEqual(self.lua.retry_count, 2)
        self.send.queued = []

        # Third timeout → retry 3
        self.lua._param_deadline = t0 + LuaStateMachine.PARAM_TIMEOUT_S
        self.lua.tick(t0 + LuaStateMachine.PARAM_TIMEOUT_S + 0.001)
        self.assertEqual(self.lua.retry_count, 3)
        self.send.queued = []

        # Fourth timeout → give up, move to param 2
        self.lua._param_deadline = t0 + LuaStateMachine.PARAM_TIMEOUT_S
        self.lua.tick(t0 + LuaStateMachine.PARAM_TIMEOUT_S + 0.001)
        self.assertIn(1, self.lua.missing_params)
        self.assertEqual(self.lua.retry_count, 0)
        # Next request should be for param 2
        next_reads = [f for f in self.send.queued if f[2] == CRSF_FRAMETYPE_PARAMETER_READ]
        self.assertTrue(len(next_reads) >= 1)
        self.assertEqual(next_reads[0][5], 2)

    def _exhaust_param(self, param_num, t=0.0):
        """Simulate MAX_RETRIES+1 timeouts for a param (causes it to go to missing_params)."""
        self.lua._param_deadline = t + LuaStateMachine.PARAM_TIMEOUT_S
        self.lua.pending_param_number = param_num
        for _ in range(LuaStateMachine.MAX_RETRIES + 1):
            self.lua._param_deadline = t + LuaStateMachine.PARAM_TIMEOUT_S
            self.lua.tick(t + LuaStateMachine.PARAM_TIMEOUT_S + 0.001)
            self.send.queued = []

    def test_three_consecutive_failures_aborts(self):
        aborted = []
        self.lua.on_loading_aborted = lambda msg: aborted.append(msg)
        self.lua.load_parameters()
        self.send.queued = []

        # Fail params 1, 2, 3 consecutively
        for pnum in range(1, 4):
            self.lua.pending_param_number = pnum
            for _ in range(LuaStateMachine.MAX_RETRIES + 1):
                self.lua._param_deadline = 1.0
                self.lua.tick(1.1)
                self.send.queued = []
            if aborted:
                break

        self.assertTrue(len(aborted) >= 1)
        self.assertFalse(self.lua.is_loading)


class TestMissingParamRetry(unittest.TestCase):
    def setUp(self):
        self.send = StubSend()
        self.lua  = LuaStateMachine(send=self.send)

    def _deliver_param(self, param_num, parent=0):
        blob = _uint8_blob(parent, f'P{param_num}', param_num, 0, 255, 0)
        self.lua.handle_frame(
            _build_param_entry_frame(
                CRSF_ADDRESS_ELRS_LUA, CRSF_ADDRESS_MODULE, param_num, 0, blob
            )
        )

    def test_missing_param_retry_pass(self):
        """Load 2 params; fail #2 during sequential load; succeed on retry.

        With n_params=2: after param 2 fails, next sequential number (3) exceeds
        parameter_count (2), so _retry_missing_parameters fires immediately,
        avoiding the "past-end timeout" complication of n_params=3.
        """
        completed = []
        self.lua.on_loading_complete = lambda p: completed.append(dict(p))
        self.lua.select_device({
            'address':         CRSF_ADDRESS_MODULE,
            'isElrs':          True,
            'parametersTotal': 2,
            'name':            'Test TX',
        })
        self.send.queued = []

        # Deliver param 1
        self._deliver_param(1)
        self.send.queued = []

        # Param 2 fails (MAX_RETRIES + 1 ticks) — because next_num=3 > 2,
        # the timeout handler calls _retry_missing_parameters immediately.
        for _ in range(LuaStateMachine.MAX_RETRIES + 1):
            self.lua._param_deadline = 1.0
            self.lua.tick(1.1)
            self.send.queued = []

        # retry_missing should now be in progress, pending param 2
        self.assertTrue(self.lua._retry_missing_in_progress)
        self.assertEqual(self.lua.pending_param_number, 2)

        # Deliver param 2 on the retry
        self._deliver_param(2)

        self.assertIn(2, self.lua.parameters)
        self.assertFalse(self.lua.is_loading)
        self.assertTrue(len(completed) >= 1)
        self.assertIn(1, completed[-1])
        self.assertIn(2, completed[-1])


class TestUpdateParameter(unittest.TestCase):
    def setUp(self):
        self.send = StubSend()
        self.lua  = LuaStateMachine(send=self.send)
        device = _elrs_device(n_params=2)
        self.lua.select_device(device)
        self.lua.parameters[5] = {
            'number': 5, 'parentFolder': 0, 'type': PARAM_TYPE_TEXT_SELECTION,
            'hidden': False, 'name': 'Packet Rate',
            'value': 0, 'options': ['50Hz', '100Hz', '250Hz'],
            'min': 0, 'max': 2, 'defaultValue': 0, 'unit': '',
            'status': None, 'timeout': None,
        }
        self.send.queued = []

    def test_update_parameter_writes_correct_frame(self):
        result = self.lua.update_parameter(5, 2)
        self.assertTrue(result)
        write_frames = [f for f in self.send.queued
                        if f[2] == CRSF_FRAMETYPE_PARAMETER_WRITE]
        self.assertEqual(len(write_frames), 1)
        f = write_frames[0]
        self.assertEqual(f[3], CRSF_ADDRESS_MODULE)   # dest = TX
        self.assertEqual(f[4], CRSF_ADDRESS_ELRS_LUA) # origin = 0xEF (ELRS TX)
        self.assertEqual(f[5], 5)                     # param_index
        self.assertEqual(f[6], 2)                     # value
        # Verify CRC
        self.assertEqual(f[7], crsf_crc8(f[2:7]))

    def test_update_parameter_triggers_reload_chain(self):
        completed = []
        self.lua.on_loading_complete = lambda p: completed.append(dict(p))
        self.lua.is_loading = False  # loading already done

        self.lua.update_parameter(5, 2)
        self.send.queued = []

        # Advance tick past the 200ms reload delay
        now = self.lua._reload_after_write_at + 0.001
        self.lua.tick(now)
        self.assertTrue(self.lua._reload_in_progress)

        # Deliver the PARAM_READ responses (param 5 is last in reload queue)
        blob = _uint8_blob(0, 'Packet Rate', 2)
        self.lua.handle_frame(
            _build_param_entry_frame(CRSF_ADDRESS_ELRS_LUA, CRSF_ADDRESS_MODULE, 5, 0, blob)
        )

        self.assertTrue(len(completed) >= 1)
        self.assertFalse(self.lua._reload_in_progress)


class TestFolderNavigation(unittest.TestCase):
    def setUp(self):
        self.send = StubSend()
        self.lua  = LuaStateMachine(send=self.send)

    def test_navigate_to_folder_and_back(self):
        self.lua.navigate_to_folder(7, 'Wifi')
        self.assertEqual(self.lua.current_folder, 7)
        self.assertEqual(len(self.lua.folder_stack), 1)
        self.assertEqual(self.lua.folder_stack[0], {'id': 7, 'name': 'Wifi'})

        self.lua.navigate_back()
        self.assertEqual(self.lua.current_folder, 0)
        self.assertEqual(len(self.lua.folder_stack), 0)

    def test_navigate_back_at_root_is_noop(self):
        self.lua.navigate_back()
        self.assertEqual(self.lua.current_folder, 0)

    def test_nested_folder_navigation(self):
        self.lua.navigate_to_folder(7, 'Wifi')
        self.lua.navigate_to_folder(12, 'Advanced')
        self.assertEqual(self.lua.current_folder, 12)
        self.assertEqual(len(self.lua.folder_stack), 2)

        self.lua.navigate_back()
        self.assertEqual(self.lua.current_folder, 7)
        self.lua.navigate_back()
        self.assertEqual(self.lua.current_folder, 0)


class TestReset(unittest.TestCase):
    def setUp(self):
        self.send = StubSend()
        self.lua  = LuaStateMachine(send=self.send)

    def test_reset_clears_all(self):
        self.lua.select_device(_elrs_device())
        self.lua.parameters[1] = {'number': 1}
        self.lua.navigate_to_folder(5, 'Wifi')
        self.lua.missing_params.add(3)

        self.lua.reset()

        self.assertEqual(self.lua.devices,           [])
        self.assertIsNone(self.lua.selected_device)
        self.assertEqual(self.lua.parameters,        {})
        self.assertEqual(self.lua.loaded_count,      0)
        self.assertEqual(self.lua.current_folder,    0)
        self.assertEqual(self.lua.folder_stack,      [])
        self.assertEqual(self.lua.missing_params,    set())
        self.assertFalse(self.lua.is_loading)
        self.assertFalse(self.lua._reload_in_progress)
        self.assertIsNone(self.lua._param_deadline)
        self.assertIsNone(self.lua._scan_deadline)
        self.assertEqual(self.lua.origin_address, CRSF_ADDRESS_RADIO)


if __name__ == '__main__':
    unittest.main()
