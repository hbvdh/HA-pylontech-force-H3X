"""Constants for the Pylontech Force H3X integration."""
DOMAIN = "pylon_fh3x"
MANUFACTURER = "Pylontech"
MODEL = "Force H3X"

DEFAULT_NAME = "Pylontech Force H3X"
DEFAULT_PORT = 502
DEFAULT_SCAN_INTERVAL = 5

CONF_HOST = "host"
CONF_PORT = "port"

# Host the entity unique IDs were first created with. Stored on Reconfigure so
# that a new IP address or hostname does not change the unique IDs (which would
# make Home Assistant create new entities and orphan the existing ones).
CONF_UNIQUE_ID_BASE = "unique_id_base"

# Serial number of the H3X this entry belongs to, learned from the device. Used
# only to check that the right device answers; never part of an entity ID.
CONF_SERIAL = "serial"

# Options (Configure): extended battery statistics (BMS summary 0x1100,
# cell voltages 0x1500, cell temperatures 0x1800). Off by default.
CONF_BATTERY_DETAILS = "battery_details"
DEFAULT_BATTERY_DETAILS = False
