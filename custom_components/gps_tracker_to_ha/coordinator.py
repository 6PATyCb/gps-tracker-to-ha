"""Поллинг-координатор для опроса REST-API GPS-сервера."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    ATTR_BATTERY,
    ATTR_COURSE,
    ATTR_HEADING,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_POSITION_VALID,
    ATTR_SATELLITES,
    ATTR_SPEED,
    ATTR_TIME,
    CONF_DEVICE_NAME,
    CONF_IMEI,
    DOMAIN,
    REQUEST_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

# Формат времени, который присылает REST-сервер (LocalDateTime, без таймзоны)
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class GpsDeviceData:
    """Текущее состояние одного трекера."""

    imei: str
    name: str
    time: datetime | None = None
    satellites: int = 0
    latitude: float | None = None
    longitude: float | None = None
    speed: int = 0
    position_valid: bool = False
    course: int = 0
    heading: str | None = None
    battery: int | None = None
    available: bool = False
    poll_status: str = "no_data"
    last_poll_time: datetime | None = None
    last_success_time: datetime | None = None
    last_http_status: int | None = None
    poll_error: str | None = None

    def payload_changed(self, other: "GpsDeviceData | None") -> bool:
        """Изменились ли данные позиции по сравнению с other."""
        if other is None:
            return True
        return (
            self.time != other.time
            or self.satellites != other.satellites
            or self.latitude != other.latitude
            or self.longitude != other.longitude
            or self.speed != other.speed
            or self.position_valid != other.position_valid
            or self.course != other.course
            or self.heading != other.heading
            or self.battery != other.battery
            or self.available != other.available
        )


def _parse_time(value: object) -> datetime | None:
    """Распарсить строку времени от сервера."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), TIME_FORMAT)
    except ValueError:
        _LOGGER.warning("Некорректное значение времени: %s", value)
        return None


class GpsTrackerCoordinator(DataUpdateCoordinator[dict[str, GpsDeviceData]]):
    """Опросить все устройства и отдать актуальное состояние по каждому."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        scan_interval: int,
        trust_all: bool,
        devices_config: list[dict],
    ) -> None:
        self._host = host
        self._port = port
        self._trust_all = trust_all
        self._session = async_get_clientsession(hass)
        self._devices = [
            GpsDeviceData(
                imei=str(device[CONF_IMEI]),
                name=str(device.get(CONF_DEVICE_NAME) or device[CONF_IMEI]),
            )
            for device in devices_config
        ]
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )

    @property
    def base_url(self) -> str:
        """Базовый URL REST-сервера."""
        return f"https://{self._host}:{self._port}"

    @property
    def devices(self) -> list[GpsDeviceData]:
        """Список сконфигурированных устройств."""
        return self._devices

    async def _async_fetch_device(self, device: GpsDeviceData) -> GpsDeviceData:
        """Получить данные одного устройства из REST API."""
        url = f"{self.base_url}/gps/{device.imei}"
        ssl = not self._trust_all
        device.last_poll_time = datetime.now()
        try:
            timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            async with self._session.get(url, ssl=ssl, timeout=timeout) as response:
                device.last_http_status = response.status
                if response.status == 200:
                    payload = await response.json(content_type=None)
                    parsed = self._parse_payload(device, payload)
                    if parsed.poll_status != "invalid_data":
                        parsed.poll_status = "ok"
                        parsed.poll_error = None
                        parsed.last_success_time = datetime.now()
                    return parsed
                if response.status == 404:
                    _LOGGER.debug(
                        "Устройство %s не подключено к серверу (HTTP 404)", device.imei
                    )
                    device.poll_status = "http_404"
                    device.poll_error = "Трекер не подключён к серверу"
                else:
                    _LOGGER.warning(
                        "Неожиданный HTTP %s для %s", response.status, url
                    )
                    device.poll_status = "http_error"
                    device.poll_error = f"Неожиданный HTTP {response.status}"
                device.available = False
                return device
        except (asyncio.TimeoutError, aiohttp.ClientError, OSError) as err:
            device.poll_status = (
                "timeout" if isinstance(err, asyncio.TimeoutError) else "client_error"
            )
            device.poll_error = str(err)
            _LOGGER.warning("Ошибка запроса %s: %s", url, err)
            device.available = False
            return device

    def _parse_payload(
        self, device: GpsDeviceData, payload: dict
    ) -> GpsDeviceData:
        """Преобразовать JSON-ответ сервера в GpsDeviceData."""
        if not isinstance(payload, dict):
            device.poll_status = "invalid_data"
            device.poll_error = "Сервер вернул неожиданный формат ответа"
            _LOGGER.warning("Неожиданный формат ответа для %s: %s", device.imei, payload)
            device.available = False
            return device
        try:
            latitude = payload.get(ATTR_LATITUDE)
            longitude = payload.get(ATTR_LONGITUDE)
            battery = payload.get(ATTR_BATTERY)
            parsed = GpsDeviceData(
                imei=device.imei,
                name=device.name,
                time=_parse_time(payload.get(ATTR_TIME)),
                satellites=int(payload.get(ATTR_SATELLITES, 0) or 0),
                latitude=float(latitude) if latitude is not None else None,
                longitude=float(longitude) if longitude is not None else None,
                speed=int(payload.get(ATTR_SPEED, 0) or 0),
                position_valid=bool(payload.get(ATTR_POSITION_VALID, False)),
                course=int(payload.get(ATTR_COURSE, 0) or 0),
                heading=payload.get(ATTR_HEADING),
                battery=int(battery) if battery is not None else None,
                available=True,
                poll_status=device.poll_status,
                last_poll_time=device.last_poll_time,
                last_http_status=device.last_http_status,
                poll_error=device.poll_error,
            )
            return parsed
        except (TypeError, ValueError) as err:
            device.poll_status = "invalid_data"
            device.poll_error = str(err)
            _LOGGER.warning(
                "Некорректные данные GPS для %s: %s", device.imei, err
            )
            device.available = False
            return device

    async def _async_update_data(self) -> dict[str, GpsDeviceData]:
        """Запросить данные всех устройств параллельно."""
        results = await asyncio.gather(
            *(self._async_fetch_device(device) for device in self._devices)
        )
        return {device.imei: device for device in results}