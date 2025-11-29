"""
Joystick Input Handling

This module manages joystick device detection, connection, and input reading.
"""

import pygame
from PyQt5 import QtCore


class JoystickHandler(QtCore.QObject):
    """Handles joystick connection and input reading with hotplug support."""

    status = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        pygame.init()
        pygame.joystick.init()
        self.j = None
        self.name = "None"

        # Pygame 2 provides joystick hotplug events
        self._joy_events_supported = hasattr(pygame, "JOYDEVICEADDED") and hasattr(
            pygame, "JOYDEVICEREMOVED"
        )

        # Periodic scanner for older pygame versions
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._scan)
        self.timer.start(2000)

        self.status.emit("Scanning for joystick...")

    def _handle_device_added(self, device_index: int):
        """Handle a newly added joystick device (pygame 2+).

        Args:
            device_index: Index of the newly added device
        """
        try:
            # Ensure subsystem is initialized
            if not pygame.joystick.get_init():
                pygame.joystick.init()
            # If we already have a joystick, ignore additional ones
            if self.j is not None:
                return
            js = pygame.joystick.Joystick(device_index)
            js.init()
            self.j = js
            try:
                self.name = self.j.get_name()
            except Exception:
                self.name = "Joystick"
            self.status.emit(f"Joystick connected: {self.name}")
        except Exception as e:
            self.status.emit(f"Joystick init error: {e}")
            self.j = None
            self.name = "None"

    def _handle_device_removed(self, instance_id):
        """Handle joystick removal (pygame 2+).

        Args:
            instance_id: Instance ID of the removed device
        """
        try:
            current_id = (
                self.j.get_instance_id()
                if (self.j is not None and hasattr(self.j, "get_instance_id"))
                else None
            )
        except Exception:
            current_id = None
        # If we don't know ids, or it matches our current one, drop it
        if self.j is None or current_id is None or instance_id == current_id:
            self.status.emit(f"Joystick '{self.name}' disconnected. Scanning...")
            self.j = None
            self.name = "None"
            # Kick an immediate scan to pick up any other available device
            self._scan()

    def _scan(self):
        """Periodically scan for joystick devices."""
        # Only scan if no joystick is currently active
        if self.j is not None:
            # Check that it still exists
            try:
                # Safely access name to confirm it hasn't gone invalid
                _ = self.j.get_name()
            except pygame.error:
                # joystick object became invalid
                self.status.emit(f"Joystick '{self.name}' disconnected. Scanning...")
                self.j = None
                self.name = "None"
                pygame.joystick.quit()
                pygame.joystick.init()
            return

        # If we get here, there is no active joystick — scan for one
        pygame.joystick.quit()
        pygame.joystick.init()
        count = pygame.joystick.get_count()
        if count == 0:
            self.status.emit("Scanning for joystick...")
        else:
            try:
                self.j = pygame.joystick.Joystick(0)
                self.j.init()
                self.name = self.j.get_name()
                self.status.emit(f"Joystick connected: {self.name}")
            except Exception as e:
                self.status.emit(f"Joystick init error: {e}")
                self.j = None
                self.name = "None"

    def read(self):
        """Read current joystick state.

        Returns:
            Tuple of (axes, buttons) where:
            - axes: List of axis values (-1.0 to 1.0), includes hat as last 2 axes (x, y)
            - buttons: List of button states (0 or 1)
        """
        # Prefer hotplug events if available for immediate reconnect
        if self._joy_events_supported:
            try:
                for ev in pygame.event.get([pygame.JOYDEVICEADDED, pygame.JOYDEVICEREMOVED]):
                    if ev.type == pygame.JOYDEVICEADDED:
                        self._handle_device_added(getattr(ev, "device_index", 0))
                    elif ev.type == pygame.JOYDEVICEREMOVED:
                        self._handle_device_removed(getattr(ev, "instance_id", None))
            except Exception:
                # Fall back to simple pumping if anything goes wrong
                pygame.event.pump()
        else:
            pygame.event.pump()

        axes, btns = [], []
        if self.j:
            try:
                for i in range(self.j.get_numaxes()):
                    axes.append(self.j.get_axis(i))
                for i in range(self.j.get_numbuttons()):
                    btns.append(1 if self.j.get_button(i) else 0)
                # Read POV hat as two separate axes (left/right and up/down)
                for i in range(self.j.get_numhats()):
                    hat = self.j.get_hat(i)
                    axes.append(float(hat[0]))  # Left/right (-1, 0, 1)
                    axes.append(float(hat[1]))  # Up/down (-1, 0, 1)
            except pygame.error:
                # Lost joystick during read
                self.status.emit(f"Joystick '{self.name}' lost. Scanning...")
                self.j = None
                self.name = "None"
                # Trigger a quick rescan
                self._scan()
        return axes, btns
