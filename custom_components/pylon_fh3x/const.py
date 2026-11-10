"""Constants for the Pylontech Force H3X integration."""
DOMAIN = "pylon_fh3x"
MANUFACTURER = "Pylontech"
MODEL = "Force H3X"

DEFAULT_NAME = "Pylontech Force H3X"
DEFAULT_PORT = 502
DEFAULT_SCAN_INTERVAL = 5

CONF_HOST = "host"
CONF_PORT = "port"

# Options (Configure): extended battery statistics (BMS summary 0x1100,
# cell voltages 0x1500, cell temperatures 0x1800). Off by default.
CONF_BATTERY_DETAILS = "battery_details"
DEFAULT_BATTERY_DETAILS = False
