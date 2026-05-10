"""
LUA Parameter State Machine.

Single-threaded port of CrsfParams (scan.js ~lines 991-2048).

Discovers ELRS/CRSF devices, loads their parameters, and handles writes
with post-write reload of related fields. All I/O is driven via tick()
from the GUI loop — no internal threads.

Key corrections over the old Python code (documented in USB_FEEDER_LUA_PLAN.md §0):
  - TX is always at 0xEE; 0xEA is *our* origin during scan.
  - ELRS detection is by serialNumber == 0x454C5253, not by address.
  - Origin switches to 0xEF (ADDR_ELRS_LUA) for all PARAM_READ/WRITE to ELRS TX.
  - Type mask is 0x3F, not 0x7F.
"""

import time
from typing import Callable, Dict, List, Optional, Set

from crsf_protocol import (
    CRSF_ADDRESS_BROADCAST,
    CRSF_ADDRESS_RADIO,
    CRSF_ADDRESS_MODULE,
    CRSF_ADDRESS_ELRS_LUA,
    CRSF_FRAMETYPE_DEVICE_INFO,
    CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY,
    CRSF_FRAMETYPE_ELRS_STATUS,
    build_ping_frame,
    build_param_read,
    build_param_write,
    parse_param_entry_header,
)
from device_parameters import (
    parse_device_info,
    parse_parameter,
    PARAM_TYPE_UINT8,
    PARAM_TYPE_INT8,
    PARAM_TYPE_TEXT_SELECTION,
    PARAM_TYPE_STRING,
    PARAM_TYPE_FOLDER,
    PARAM_TYPE_COMMAND,
)


class LuaStateMachine:
    """
    Discovers ELRS devices and loads/writes their parameters.

    Single-threaded. Inbound frames arrive via handle_frame(); outbound
    frames are emitted via the send hook supplied at construction
    (typically CRSFStateMachine.queue_frame). tick(now) advances scan
    timeout, request timeout, and reload-after-write timer.

    Direct port of CrsfParams in scan.js (lines ~991-2048).

    Callbacks (called inline; safe to touch widgets):
        on_devices_changed(devices: list[dict])
        on_loading_progress(loaded: int, total: int)
        on_loading_complete(parameters: dict[int, dict])
        on_loading_aborted(message: str)
        on_field_updated(param: dict)
        on_debug(msg: str)
    """

    SCAN_WINDOW_S        = 2.0  # scan.js: 2000 ms collection window
    PARAM_TIMEOUT_S      = 3.0  # scan.js: 3000 ms per-request timeout
    MAX_RETRIES          = 3    # scan.js: this.maxRetries
    MAX_CONSECUTIVE_FAIL = 3    # scan.js: consecutiveParamFailures threshold
    LINKSTAT_POLL_S      = 1.0  # scan.js: startLinkstatPolling every 1000 ms

    def __init__(self, send: Callable[[bytes], bool]):
        """
        Args:
            send: Callable that accepts a bytes frame and returns True on success.
                  Typically CRSFStateMachine.queue_frame.
        """
        self._send = send

        # Callbacks — default to no-ops so the state machine is usable without a UI.
        self.on_devices_changed:  Callable[[list], None]        = lambda _: None
        self.on_loading_progress: Callable[[int, int], None]    = lambda _l, _t: None
        self.on_loading_complete: Callable[[dict], None]        = lambda _: None
        self.on_loading_aborted:  Callable[[str], None]         = lambda _: None
        self.on_field_updated:    Callable[[dict], None]        = lambda _: None
        self.on_debug:            Callable[[str], None]         = lambda _: None
        self.on_elrs_error:       Callable[[str], None]         = lambda _: None
        self.on_elrs_confirm:     Callable[[str, int], None]    = lambda _m, _n: None
        self.on_elrs_status:      Callable[[int, int, int], None] = lambda _b, _g, _f: None

        self._init_state()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_devices(self) -> None:
        """Send a broadcast ping and open a 2-second collection window.

        Mirrors scanDevices (scan.js:1368-1397).
        """
        self.devices = []
        self.on_devices_changed([])
        frame = build_ping_frame(
            target_addr=CRSF_ADDRESS_BROADCAST,
            origin_addr=CRSF_ADDRESS_RADIO,
        )
        self._send(frame)
        self._scan_deadline = time.monotonic() + self.SCAN_WINDOW_S

    def select_device(self, device: dict) -> None:
        """Select a device from the scan list and begin loading its parameters.

        Mirrors selectDevice (scan.js:1399-1430). Sets origin_address to
        0xEF for ELRS TX modules, 0xEA for everything else (§0.1 / §0.2).
        """
        self.selected_device = device
        self.current_folder  = 0
        self.folder_stack    = []

        is_elrs_tx = (device.get('isElrs', False)
                      and device.get('address') == CRSF_ADDRESS_MODULE)
        self.origin_address = (CRSF_ADDRESS_ELRS_LUA if is_elrs_tx
                               else CRSF_ADDRESS_RADIO)

        self.load_parameters()

    def load_parameters(self) -> None:
        """(Re-)load all parameters from the selected device from scratch.

        Mirrors loadParameters (scan.js:1432-1470).
        """
        if not self.selected_device:
            return
        self._param_deadline = None
        self._linkstat_poll_at = None  # stop polling while loading
        self._cancel_pending()

        self.parameters              = {}
        self.parameter_count         = self.selected_device.get('parametersTotal', 0)
        self.loaded_count            = 0
        self.pending_param_number    = 0
        self.pending_chunk_number    = 0
        self.pending_chunks          = []
        self.is_loading              = True
        self.retry_count             = 0
        self.missing_params          = set()
        self.consecutive_param_failures = 0

        self.on_loading_progress(0, self.parameter_count)

        now = time.monotonic()
        self._request_parameter(1, 0, now)

    def update_parameter(self, param_num: int, value: int) -> bool:
        """Write a parameter value and schedule a reload of related fields.

        Mirrors updateParameter (scan.js:1681-1715).
        Returns False if the device is not selected or the param is not writable.
        """
        if not self.selected_device:
            return False
        param = self.parameters.get(param_num)
        if param is None:
            return False

        t = param['type']
        if t in (PARAM_TYPE_UINT8, PARAM_TYPE_TEXT_SELECTION,
                 PARAM_TYPE_INT8, PARAM_TYPE_COMMAND):
            write_value = value & 0xFF
        else:
            return False  # STRING/FOLDER/INFO/UINT16/INT16/FLOAT not writable

        frame = build_param_write(
            device_addr=self.selected_device['address'],
            origin_addr=self.origin_address,
            param_index=param_num,
            value=write_value,
        )
        if not self._send(frame):
            return False

        if t == PARAM_TYPE_COMMAND:
            # Track this command so spontaneous status-update PARAM_ENTRYs
            # (e.g. status=3 confirmation request) can be matched and handled.
            # Do NOT overwrite param['value'] — it holds the button label string.
            self._pending_command_num = param_num
        else:
            # Optimistic local update (scan.js:1708) for snappy UI.
            param['value'] = value
            # Reload related fields after a short delay (scan.js:1712-1714).
            self._reload_after_write_at    = time.monotonic() + 0.2
            self._reload_after_write_param = param
        return True

    def navigate_to_folder(self, folder_id: int, name: str) -> None:
        """Navigate into a folder. Pure state — no I/O.

        Mirrors navigateToFolder (scan.js:1646-1664).
        """
        self.folder_stack.append({'id': folder_id, 'name': name})
        self.current_folder = folder_id

    def navigate_back(self) -> None:
        """Navigate back to the parent folder. Pure state — no I/O.

        Mirrors navigateBack (scan.js:1665-1678).
        """
        if not self.folder_stack:
            return
        self.folder_stack.pop()
        self.current_folder = self.folder_stack[-1]['id'] if self.folder_stack else 0

    def handle_frame(self, frame: dict) -> None:
        """Dispatch a decoded CRSF frame to the appropriate handler.

        Mirrors handleMessage (scan.js:1060-1077).
        """
        if not frame.get('valid'):
            return
        t = frame.get('type')
        if t == CRSF_FRAMETYPE_DEVICE_INFO:
            self._on_device_info(frame.get('payload', b''))
        elif t == CRSF_FRAMETYPE_PARAMETER_SETTINGS_ENTRY:
            self._on_param_entry(frame.get('payload', b''))
        elif t == CRSF_FRAMETYPE_ELRS_STATUS:
            self._on_elrs_status(frame.get('payload', b''))

    def tick(self, now: float) -> None:
        """Advance all tick-driven timers. Call once per GUI tick.

        Replaces setTimeout / setInterval from the web version.
        """
        # Scan window expiry — just clear the deadline.
        if self._scan_deadline is not None and now >= self._scan_deadline:
            self._scan_deadline = None

        # Reload-after-write delay.
        if self._reload_after_write_at is not None and now >= self._reload_after_write_at:
            param = self._reload_after_write_param
            self._reload_after_write_at    = None
            self._reload_after_write_param = None
            if param is not None and self.selected_device is not None:
                self._reload_related_fields(param, now)

        # Per-request timeout (loading, retry-missing, or reload chain).
        if self._param_deadline is not None and now >= self._param_deadline:
            self._param_deadline = None
            self._on_param_timeout(self.pending_param_number,
                                   self.pending_chunk_number, now)

        # ELRS_STATUS polling — send PARAM_WRITE [0, 0] every second so the
        # firmware replies with status flags + error message.
        # Mirrors startLinkstatPolling / pollLinkstat (scan.js:1899-1943).
        if (self._linkstat_poll_at is not None
                and now >= self._linkstat_poll_at
                and self.selected_device is not None
                and not self.is_loading):
            self._linkstat_poll_at = now + self.LINKSTAT_POLL_S
            frame = build_param_write(
                device_addr=self.selected_device['address'],
                origin_addr=self.origin_address,
                param_index=0,
                value=0,
            )
            self._send(frame)

    def reset(self) -> None:
        """Clear all state. Call when the serial connection drops."""
        self._init_state()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _init_state(self) -> None:
        self.devices:               List[dict]      = []
        self.selected_device:       Optional[dict]  = None
        self.origin_address:        int             = CRSF_ADDRESS_RADIO

        self.parameters:                Dict[int, dict] = {}
        self.parameter_count:           int             = 0
        self.loaded_count:              int             = 0
        self.pending_param_number:      int             = 0
        self.pending_chunk_number:      int             = 0
        self.pending_chunks:            List[bytes]     = []
        self._seen_chunks_remaining:    Set[int]        = set()
        self._last_completed_param_num: int             = 0
        self.is_loading:                bool            = False
        self.retry_count:               int             = 0
        self.missing_params:            Set[int]        = set()
        self.consecutive_param_failures: int            = 0

        self.current_folder:    int         = 0
        self.folder_stack:      List[dict]  = []

        self._reload_queue:             List[int]       = []
        self._reload_index:             int             = 0
        self._reload_in_progress:       bool            = False
        self._retry_missing_in_progress: bool           = False
        self._retry_missing_list:       List[int]       = []
        self._retry_missing_index:      int             = 0

        self._scan_deadline:            Optional[float] = None
        self._param_deadline:           Optional[float] = None
        self._reload_after_write_at:    Optional[float] = None
        self._reload_after_write_param: Optional[dict]  = None
        self._linkstat_poll_at:         Optional[float] = None
        self._pending_command_num:      Optional[int]   = None

        self.elrs_flags:      int = 0
        self.elrs_flags_info: str = ''

    def _cancel_pending(self) -> None:
        """Clear all in-progress reload/retry-missing state."""
        self._reload_in_progress        = False
        self._reload_queue              = []
        self._reload_index              = 0
        self._retry_missing_in_progress = False
        self._retry_missing_list        = []
        self._retry_missing_index       = 0
        self._reload_after_write_at     = None
        self._reload_after_write_param  = None

    def _request_parameter(self, param_num: int, chunk_num: int, now: float) -> None:
        """Send a PARAM_READ request and arm the timeout.

        Mirrors requestParameter (scan.js:1473-1490).
        """
        if not self.selected_device:
            return
        if not self.is_loading and not self._reload_in_progress:
            return

        self.pending_param_number = param_num
        self.pending_chunk_number = chunk_num
        if chunk_num == 0:
            self._seen_chunks_remaining = set()

        frame = build_param_read(
            device_addr=self.selected_device['address'],
            origin_addr=self.origin_address,
            param_index=param_num,
            chunk_number=chunk_num,
        )
        self._send(frame)
        self._param_deadline = now + self.PARAM_TIMEOUT_S

    def _on_param_timeout(self, param_num: int, chunk_num: int, now: float) -> None:
        """Handle a per-request timeout.

        Mirrors handleParameterTimeout (scan.js:1492-1540).
        """
        if not self.is_loading and not self._reload_in_progress:
            return

        # Reload-chain case: no retry, just advance the chain.
        if self._reload_in_progress:
            self.pending_chunks = []
            self._continue_reload_chain(now)
            return

        # Normal loading / retry-missing: attempt up to MAX_RETRIES retries.
        if self.retry_count < self.MAX_RETRIES:
            self.retry_count += 1
            self._request_parameter(param_num, chunk_num, now)
            return

        # Give up on this param.
        self.on_debug(f"giving up on param {param_num} after {self.MAX_RETRIES} retries")
        self.missing_params.add(param_num)
        self.retry_count    = 0
        self.pending_chunks = []
        self.consecutive_param_failures += 1

        if self.consecutive_param_failures >= self.MAX_CONSECUTIVE_FAIL:
            self._abort_loading("connection lost — click Reload to retry")
            return

        if self._retry_missing_in_progress:
            self._continue_retry_missing_chain(now)
        else:
            next_num = param_num + 1
            if next_num <= self.parameter_count:
                self._request_parameter(next_num, 0, now)
            else:
                self._retry_missing_parameters(now)

    def _on_device_info(self, payload: bytes) -> None:
        """Handle a DEVICE_INFO frame.

        Mirrors handleDeviceInfo (scan.js:1156-1201).
        payload[0]=dst, payload[1]=src (extended frame).
        """
        if len(payload) < 2:
            return
        src = payload[1]

        # After the scan window, only accept info from the selected device.
        if self._scan_deadline is None and (
            self.selected_device is None
            or self.selected_device.get('address') != src
        ):
            return

        info = parse_device_info(bytes(payload[2:]))
        if info is None:
            return
        info['address'] = src

        for i, d in enumerate(self.devices):
            if d.get('address') == src:
                self.devices[i] = info
                break
        else:
            self.devices.append(info)

        self.on_devices_changed(list(self.devices))

    def _on_param_entry(self, payload: bytes) -> None:
        """Handle a PARAMETER_SETTINGS_ENTRY frame.

        Mirrors handleParamEntry (scan.js:1203-1285).
        """
        hdr = parse_param_entry_header(payload)
        if hdr is None:
            self.on_debug(f"[LUA] PARAM_ENTRY: parse_param_entry_header returned None for payload len={len(payload)}")
            return
        self.on_debug(
            f"[LUA] PARAM_ENTRY raw: field_id={hdr['field_id']} chunks_remaining={hdr['chunks_remaining']} "
            f"chunk_size={len(hdr['chunk_data'])} src={hdr['src']:#04x} "
            f"pending={self.pending_param_number} "
            f"first4={hdr['chunk_data'][:4].hex() if hdr['chunk_data'] else '(empty)'}"
        )
        if not self.selected_device or hdr['src'] != self.selected_device.get('address'):
            self.on_debug(f"[LUA] PARAM_ENTRY dropped: src mismatch or no selected device")
            return

        param_number    = hdr['field_id']
        chunks_remaining = hdr['chunks_remaining']
        chunk_data      = hdr['chunk_data']

        if param_number != self.pending_param_number:
            # Check for a spontaneous command status update (e.g. status=3 confirm).
            # Mirrors scan.js isCommandPoll check in handleParamEntry.
            if (self._pending_command_num is not None
                    and param_number == self._pending_command_num
                    and chunks_remaining == 0):
                self._handle_command_status(param_number, chunk_data)
            elif (param_number == self._last_completed_param_num
                    and chunks_remaining == 0):
                # The protocol sends every final chunk twice. The duplicate arrives
                # after we've already advanced pending_param_number, so the mismatch
                # check fires before the dedup set can catch it. Silently ignore.
                self.on_debug(
                    f"[LUA] PARAM_ENTRY dedup: late duplicate final chunk for param {param_number}, ignoring"
                )
            else:
                # Unexpected param — clear any partial collection (mirrors LUA fieldData=nil reset).
                self.pending_chunks = []
                self._seen_chunks_remaining = set()
                self.on_debug(f"[LUA] PARAM_ENTRY dropped: got param {param_number} but pending is {self.pending_param_number}")
            return

        # Firmware sends each chunk response twice for reliability; deduplicate
        # by tracking which chunks_remaining values have already been processed.
        if chunks_remaining in self._seen_chunks_remaining:
            self.on_debug(f"[LUA] PARAM_ENTRY dedup: param {param_number} chunks_remaining={chunks_remaining} already seen, skipping")
            return
        self._seen_chunks_remaining.add(chunks_remaining)

        self._param_deadline = None
        self.consecutive_param_failures = 0
        self.pending_chunks.append(chunk_data)

        if chunks_remaining == 0:
            # Chunks are delta slices (scan.js spec). Duplicate frames are
            # already filtered above, so pending_chunks contains exactly one
            # copy of each chunk in order.
            full = b''.join(self.pending_chunks)
            self.pending_chunks = []
            param = parse_parameter(param_number, full)
            self.on_debug(
                f"[LUA] PARAM_ENTRY parse result: param_number={param_number} "
                f"full_len={len(full)} parse_result={'None' if param is None else repr({'type': param['type'], 'name': param['name'], 'value': param['value']})}"
            )
            if param is not None:
                self.parameters[param_number] = param
                if param['type'] == PARAM_TYPE_TEXT_SELECTION:
                    self.on_debug(
                        f"[LUA] TEXT_SEL param {param_number} {param['name']!r}  "
                        f"value={param['value']}  options={param['options']!r}"
                    )
                if self.is_loading:
                    self.loaded_count += 1
                    self.on_loading_progress(self.loaded_count, self.parameter_count)
                self.on_field_updated(param)

            # Record the completed param so the pending-number mismatch check
            # can recognise the duplicate final chunk that always follows.
            self._last_completed_param_num = param_number

            now = time.monotonic()
            if self._reload_in_progress:
                self._continue_reload_chain(now)
            elif self._retry_missing_in_progress:
                self._continue_retry_missing_chain(now)
            elif self.is_loading:
                if self.loaded_count < self.parameter_count:
                    self._request_next_parameter(now)
                else:
                    self._retry_missing_parameters(now)
        else:
            # More chunks coming — request the next chunk.
            next_chunk = len(self.pending_chunks)
            self.pending_chunk_number = next_chunk
            self._request_parameter(param_number, next_chunk, time.monotonic())

    def _request_next_parameter(self, now: float) -> None:
        """Request the next sequential parameter.

        Mirrors requestNextParameter (scan.js:1542-1557).
        """
        self.retry_count = 0
        self.consecutive_param_failures = 0
        self._request_parameter(self.pending_param_number + 1, 0, now)

    def _retry_missing_parameters(self, now: float) -> None:
        """Attempt to load any parameters that timed out during sequential loading.

        Mirrors retryMissingParameters (scan.js:1559-1595).
        """
        if not self.missing_params:
            self._finish_loading()
            return
        self._retry_missing_in_progress = True
        self._retry_missing_list        = sorted(self.missing_params)
        self.missing_params             = set()
        self._retry_missing_index       = 0
        self._continue_retry_missing_chain(now)

    def _continue_retry_missing_chain(self, now: float) -> None:
        """Advance to the next param in the retry-missing list."""
        if self._retry_missing_index >= len(self._retry_missing_list):
            self._retry_missing_in_progress = False
            self._finish_loading()
            return
        param_num = self._retry_missing_list[self._retry_missing_index]
        self._retry_missing_index += 1
        self.pending_chunks = []
        self._request_parameter(param_num, 0, now)

    def _finish_loading(self) -> None:
        """Mark loading complete and notify the UI.

        Mirrors finishLoading (scan.js:1633-1644).
        """
        self.is_loading  = False
        self._retry_missing_in_progress = False
        self._param_deadline = None
        self.on_loading_complete(dict(self.parameters))
        # Start ELRS_STATUS polling now that params are loaded.
        # Mirrors startLinkstatPolling (scan.js:1637).
        self._linkstat_poll_at = time.monotonic() + self.LINKSTAT_POLL_S

    def _reload_related_fields(self, param: dict, now: float) -> None:
        """Re-fetch the written field, its parent folder, and editable siblings.

        Mirrors reloadRelatedFields (scan.js:1717-1756).
        Queue order: parent folder → editable siblings → the field itself.
        """
        parent = param['parentFolder']
        queue: List[int] = []

        if parent != 0 and parent in self.parameters:
            queue.append(parent)

        for fid, p in sorted(self.parameters.items()):
            if p['parentFolder'] == parent and p['number'] != param['number']:
                if self._is_reload_sibling(p):
                    queue.append(p['number'])

        queue.append(param['number'])

        self._reload_in_progress = True
        self._reload_queue       = queue
        self._reload_index       = 0
        self._continue_reload_chain(now)

    def _is_reload_sibling(self, p: dict) -> bool:
        """True if the param type is included in post-write reload set.

        Web rule: type < PARAM_TYPE_STRING (0x0A) || type == PARAM_TYPE_FOLDER.
        Excludes STRING (0x0A), INFO (0x0C), COMMAND (0x0D).
        """
        t = p['type']
        return t < PARAM_TYPE_STRING or t == PARAM_TYPE_FOLDER

    def _continue_reload_chain(self, now: float) -> None:
        """Advance to the next field in the reload chain."""
        if self._reload_index >= len(self._reload_queue):
            self._finish_reload()
            return
        param_num = self._reload_queue[self._reload_index]
        self._reload_index += 1
        self.pending_chunks = []
        self._request_parameter(param_num, 0, now)

    def _finish_reload(self) -> None:
        """Complete a post-write reload and refresh the UI.

        Mirrors finish logic in reloadRelatedFields (scan.js:1763-1769).
        """
        for entry in self.folder_stack:
            fid = entry['id']
            if fid in self.parameters:
                entry['name'] = self.parameters[fid]['name']

        self._reload_in_progress = False
        self.on_loading_complete(dict(self.parameters))

    def _handle_command_status(self, param_num: int, data: bytes) -> None:
        """Process a spontaneous PARAM_ENTRY for the active command.

        Mirrors handleCommandStatusUpdate (scan.js:1993-2045).
        Called when the firmware sends an unsolicited status update, e.g.
        status=3 (CONFIRM_REQUIRED) for "Enable WiFi while connected".
        """
        param = parse_parameter(param_num, data)
        if param is None or param['type'] != PARAM_TYPE_COMMAND:
            return
        status = param.get('status') or 0
        msg    = param.get('value') or ''
        self.on_debug(f"[LUA] COMMAND param={param_num} status={status} msg={msg!r}")
        if status == 3:  # lcsConfirmation — matching scan.js:2009
            self.on_elrs_confirm(msg, param_num)
        elif status == 0:  # done / idle
            self._pending_command_num = None

    def confirm_command(self, param_num: int) -> None:
        """Send status=4 (CONFIRMED) for the active command.

        Mirrors the confirm branch in handleCommandStatusUpdate (scan.js:2018-2025).
        """
        self._pending_command_num = None
        if not self.selected_device:
            return
        frame = build_param_write(
            device_addr=self.selected_device['address'],
            origin_addr=self.origin_address,
            param_index=param_num,
            value=4,  # lcsConfirmed
        )
        self._send(frame)

    def cancel_command(self) -> None:
        """Discard the active command without sending a confirmation.

        Mirrors the cancel branch in handleCommandStatusUpdate (scan.js:2027-2030):
        LUA just clears the popup; does NOT send status=5.
        """
        self._pending_command_num = None

    def _on_elrs_status(self, payload: bytes) -> None:
        """Handle ELRS_STATUS (0x2E) frame — error popup and flag tracking.

        Mirrors handleELRSStatus (scan.js:1053-1126).
        Payload layout from CRSFFrameDecoder (extended frame, dst+src at [0:2]):
            [0]    dst
            [1]    src
            [2]    bad_pkt (uint8)
            [3]    good_pkt high byte
            [4]    good_pkt low byte  (big-endian uint16)
            [5]    flags (uint8)
            [6:]   null-terminated error message
        """
        self.on_debug(f"[LUA] ELRS_STATUS raw: len={len(payload)} hex={payload.hex()}")
        if not self.selected_device:
            self.on_debug("[LUA] ELRS_STATUS dropped: no selected device")
            return
        if len(payload) < 6:
            self.on_debug(f"[LUA] ELRS_STATUS dropped: too short ({len(payload)} bytes)")
            return
        src = payload[1]
        if src != self.selected_device.get('address'):
            self.on_debug(f"[LUA] ELRS_STATUS dropped: src={src:#04x} != device={self.selected_device.get('address'):#04x}")
            return

        data = payload[2:]
        bad_pkt  = data[0]
        good_pkt = (data[1] << 8) | data[2]
        new_flags = data[3]

        msg = ''
        if len(data) > 4:
            msg_bytes = data[4:]
            null_pos = msg_bytes.find(b'\x00')
            if null_pos >= 0:
                msg_bytes = msg_bytes[:null_pos]
            msg = msg_bytes.decode('ascii', errors='replace')

        self.on_debug(
            f"[LUA] ELRS_STATUS: bad={bad_pkt} good={good_pkt} "
            f"flags={new_flags:#04x} msg={msg!r}"
        )

        flags_changed = new_flags != self.elrs_flags
        self.elrs_flags      = new_flags
        self.elrs_flags_info = msg

        self.on_elrs_status(bad_pkt, good_pkt, new_flags)

        if flags_changed and new_flags > 0x1F and msg:
            self.on_elrs_error(msg)

    def clear_elrs_error(self) -> None:
        """Send the error-clear command after the user dismisses the error popup.

        Mirrors the .then() in handleELRSStatus (scan.js:1120-1123):
            clearCmd = [0x2E, 0x00]  (PARAM_WRITE, param_index=0x2E, value=0x00)
        """
        if not self.selected_device:
            return
        frame = build_param_write(
            device_addr=self.selected_device['address'],
            origin_addr=self.origin_address,
            param_index=0x2E,
            value=0x00,
        )
        self._send(frame)

    def _abort_loading(self, message: str) -> None:
        """Abort the load sequence and notify the UI.

        Mirrors abortLoading (scan.js:1606-1631).
        """
        self.is_loading      = False
        self._param_deadline = None
        self._cancel_pending()
        self.on_loading_aborted(message)
