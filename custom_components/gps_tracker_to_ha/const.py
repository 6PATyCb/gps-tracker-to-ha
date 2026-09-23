"""Constants for the GPS Tracker to Home Assistant integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT  # noqa: F401

DOMAIN = "gps_tracker_to_ha"

CONF_SCAN_INTERVAL = "scan_interval"
CONF_TRUST_ALL = "trust_all"
CONF_DEVICES = "devices"
CONF_IMEI = "imei"
CONF_DEVICE_NAME = "name"

# Значения по умолчанию
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9080
DEFAULT_SCAN_INTERVAL = 10
MIN_SCAN_INTERVAL = 3
MAX_SCAN_INTERVAL = 3600

# HTTP-таймаут на один запрос к серверу, секунды
REQUEST_TIMEOUT = 10

# Атрибуты устройства (соответствуют JSON, который отдаёт REST-сервер)
ATTR_TIME = "time"
ATTR_SATELLITES = "satellites"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_SPEED = "speed"
ATTR_POSITION_VALID = "position_valid"
ATTR_COURSE = "course"
ATTR_HEADING = "heading"
ATTR_BATTERY = "battery"
ATTR_IMEI = "imei"
# Скорость в км/ч как velocity: home-tracker ожидает на device_tracker атрибут
# velocity (км/ч) и сам переводит в м/с; атрибут speed он трактует как м/с.
ATTR_VELOCITY = "velocity"

# Точность GPS в метрах, которая пишется в атрибут gps_accuracy
DEFAULT_GPS_ACCURACY = 2