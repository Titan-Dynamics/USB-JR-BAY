"""
Configuration Management

This module handles loading and saving application configuration.
"""

import json
import sys
import serial.tools.list_ports


# Default configuration
DEFAULT_PORT = "COM15" if sys.platform.startswith("win") else "/dev/ttyACM0"
DEFAULT_BAUD = 5250000
CHANNELS = 16

DEFAULT_CFG = {
    "serial_port": DEFAULT_PORT,
    "display_mode": "Channels",
    "channels": [
        {"src": "none", "idx": 0, "inv": False, "min": 1000, "center": 1500, "max": 2000}
        for _ in range(CHANNELS)
    ],
}


def get_available_ports():
    """Get list of available serial ports.

    Returns:
        List of tuples (port, description)
    """
    ports = []
    for port, desc, hwid in sorted(serial.tools.list_ports.comports()):
        ports.append((port, desc))
    return ports


class ConfigManager:
    """Manages application configuration persistence."""

    def __init__(self, config_file="calib.json"):
        """Initialize configuration manager.

        Args:
            config_file: Path to configuration file
        """
        self.config_file = config_file
        self.cfg = DEFAULT_CFG.copy()

    def load(self):
        """Load configuration from disk.

        Returns:
            Configuration dictionary
        """
        try:
            with open(self.config_file, "r") as f:
                disk = json.load(f)
            self.cfg.update(disk)
            chs = self.cfg.get("channels", [])
            if len(chs) < CHANNELS:
                chs += [DEFAULT_CFG["channels"][0]] * (CHANNELS - len(chs))
            self.cfg["channels"] = chs[:CHANNELS]
        except Exception:
            # If load fails, use defaults
            pass
        return self.cfg

    def save(self, cfg=None):
        """Save configuration to disk.

        Args:
            cfg: Configuration dictionary to save (uses current if None)

        Returns:
            True on success, False on error
        """
        if cfg is not None:
            self.cfg = cfg
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.cfg, f, indent=2)
            return True
        except Exception:
            return False

    def get(self):
        """Get current configuration.

        Returns:
            Configuration dictionary
        """
        return self.cfg
