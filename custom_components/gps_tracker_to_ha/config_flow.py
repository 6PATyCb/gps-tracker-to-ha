"""Config flow для интеграции GPS Tracker to HA."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import SelectSelector, SelectSelectorConfig

from .const import (
    CONF_DEVICES,
    CONF_DEVICE_NAME,
    CONF_IMEI,
    CONF_SCAN_INTERVAL,
    CONF_TRUST_ALL,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    REQUEST_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


def _invalid_imei(imei: str) -> bool:
    """Проверить, что строка выглядит как IMEI."""
    if not imei or not imei.isdigit():
        return True
    return not (8 <= len(imei) <= 16)


def _unique_id_from_host_port(host: str, port: int) -> str:
    """Уникальный идентификатор записи: хост_порт."""
    return f"{host}_{port}"


def _parse_server_url(value: str, default_port: int) -> tuple[str, int]:
    """Извлечь host и порт из строки (вставлять можно и просто хост, и URL целиком)."""
    raw = str(value).strip().strip("/")
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    port = parsed.port if parsed.port else default_port
    return host, port


async def _async_test_connection(
    hass: HomeAssistant, host: str, port: int, trust_all: bool
) -> bool:
    """Проверить, что REST-сервер доступен."""
    url = f"https://{host}:{port}/gps/0"
    session = async_get_clientsession(hass)
    try:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with session.get(url, ssl=not trust_all, timeout=timeout) as response:
            # Любой HTTP-статус (200, 404, ...) означает, что сервер жив
            await response.read()
        return True
    except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
        return False


class GpsTrackerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Настройка интеграции GPS Tracker to HA."""

    VERSION = 1

    def __init__(self) -> None:
        """Подготовить состояние флоу."""
        self._host: str | None = None
        self._port: int | None = None
        self._scan_interval = DEFAULT_SCAN_INTERVAL
        self._trust_all = False
        self._devices: list[dict] = []

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Создать флоу опций."""
        return GpsTrackerOptionsFlow(config_entry)

    def _user_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.port,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): vol.All(cv.positive_int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
                vol.Optional(CONF_TRUST_ALL, default=False): bool,
            }
        )

    def _device_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_IMEI): str,
                vol.Optional(CONF_DEVICE_NAME, default=""): str,
                vol.Optional("add_another", default=False): bool,
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Первый шаг: параметры сервера."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = _parse_server_url(
                str(user_input.get(CONF_HOST, DEFAULT_HOST)),
                int(user_input.get(CONF_PORT, DEFAULT_PORT)),
            )
            scan_interval = user_input[CONF_SCAN_INTERVAL]
            trust_all = user_input.get(CONF_TRUST_ALL, False)

            if not host:
                errors[CONF_HOST] = "invalid_host"
            elif not await _async_test_connection(self.hass, host, port, trust_all):
                errors["base"] = "cannot_connect"

            if not errors:
                await self.async_set_unique_id(_unique_id_from_host_port(host, port))
                self._host = host
                self._port = port
                self._scan_interval = scan_interval
                self._trust_all = trust_all
                if self._existing_entry() is not None:
                    # Хаб для этого сервера уже настроен — повторный ввод
                    # того же сервера добавляет новое устройство к нему.
                    return await self.async_step_add_device_to_hub()
                return await self.async_step_devices()

        return self.async_show_form(
            step_id="user", data_schema=self._user_schema(), errors=errors
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Шаг добавления устройств (повторяется, пока add_another)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            imei = str(user_input.get(CONF_IMEI, "")).strip()
            name = str(user_input.get(CONF_DEVICE_NAME, "")).strip()
            if _invalid_imei(imei):
                errors[CONF_IMEI] = "invalid_imei"
            elif self._devices and any(
                device[CONF_IMEI] == imei for device in self._devices
            ):
                errors[CONF_IMEI] = "duplicate_imei"
            else:
                self._devices.append(
                    {CONF_IMEI: imei, CONF_DEVICE_NAME: name or imei}
                )
                if user_input.get("add_another", False):
                    return await self.async_step_devices()
                return self.async_create_entry(
                    title=f"GPS Tracker ({self._host}:{self._port})",
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_SCAN_INTERVAL: self._scan_interval,
                        CONF_TRUST_ALL: self._trust_all,
                        CONF_DEVICES: self._devices,
                    },
                )

        return self.async_show_form(
            step_id="devices", data_schema=self._device_schema(), errors=errors
        )

    def _existing_entry(self) -> ConfigEntry | None:
        """Найти уже существующую запись с текущим unique_id."""
        if not self.unique_id:
            return None
        for entry in self._async_current_entries():
            if entry.unique_id == self.unique_id:
                return entry
        return None

    async def async_step_add_device_to_hub(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Добавить устройство к уже настроенному хабу."""
        errors: dict[str, str] = {}
        if user_input is not None:
            imei = str(user_input.get(CONF_IMEI, "")).strip()
            name = str(user_input.get(CONF_DEVICE_NAME, "")).strip()
            entry = self._existing_entry()
            if entry is None:
                return self.async_abort(reason="already_configured")
            devices = list(entry.data.get(CONF_DEVICES, []))
            if _invalid_imei(imei):
                errors[CONF_IMEI] = "invalid_imei"
            elif any(device.get(CONF_IMEI) == imei for device in devices):
                errors[CONF_IMEI] = "duplicate_imei"
            else:
                devices.append({CONF_IMEI: imei, CONF_DEVICE_NAME: name or imei})
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_DEVICES: devices},
                )
                if user_input.get("add_another", False):
                    return await self.async_step_add_device_to_hub()
                return self.async_abort(reason="devices_added")

        return self.async_show_form(
            step_id="add_device_to_hub",
            data_schema=self._device_schema(),
            errors=errors,
        )


class GpsTrackerOptionsFlow(OptionsFlow):
    """Настройка уже созданной записи."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Сохранить ссылку на запись."""
        self._entry = config_entry
        self._host = str(config_entry.data.get(CONF_HOST, DEFAULT_HOST))
        self._port = int(config_entry.data.get(CONF_PORT, DEFAULT_PORT))
        self._scan_interval = int(
            config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        self._trust_all = bool(config_entry.data.get(CONF_TRUST_ALL, False))
        self._devices = list(config_entry.data.get(CONF_DEVICES, []))

    def _base_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host): str,
                vol.Required(CONF_PORT, default=self._port): cv.port,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=self._scan_interval
                ): vol.All(cv.positive_int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
                vol.Optional(CONF_TRUST_ALL, default=self._trust_all): bool,
            }
        )

    def _manage_schema(self) -> vol.Schema:
        options = [
            f"{device.get(CONF_IMEI)} ({device.get(CONF_DEVICE_NAME, device.get(CONF_IMEI))})"
            for device in self._devices
        ]
        return vol.Schema(
            {
                vol.Optional("device_to_remove", default=[]): SelectSelector(
                    SelectSelectorConfig(
                        multiple=True,
                        options=options,
                        mode="dropdown",
                    )
                ),
                vol.Optional("add_device", default=False): bool,
            }
        )

    def _add_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_IMEI): str,
                vol.Optional(CONF_DEVICE_NAME, default=""): str,
                vol.Optional("add_another", default=False): bool,
            }
        )

    async def _async_commit(self) -> dict[str, Any]:
        """Сохранить новую конфигурацию в entry.data и завершить флоу."""
        self.hass.config_entries.async_update_entry(
            self._entry,
            data={
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_SCAN_INTERVAL: self._scan_interval,
                CONF_TRUST_ALL: self._trust_all,
                CONF_DEVICES: self._devices,
            },
        )
        return self.async_create_entry(title="", data={})

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Параметры сервера."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host, self._port = _parse_server_url(
                str(user_input.get(CONF_HOST, self._host)),
                int(user_input.get(CONF_PORT, self._port)),
            )
            self._scan_interval = user_input[CONF_SCAN_INTERVAL]
            self._trust_all = user_input.get(CONF_TRUST_ALL, False)

            if not self._host:
                errors[CONF_HOST] = "invalid_host"
            elif not await _async_test_connection(self.hass, self._host, self._port, self._trust_all):
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_manage_devices()

        return self.async_show_form(
            step_id="init", data_schema=self._base_schema(), errors=errors
        )

    async def async_step_manage_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Добавить или удалить устройство; далее — к следующему действию."""
        errors: dict[str, str] = {}
        if user_input is not None:
            to_remove = set(user_input.get("device_to_remove", []) or [])
            if to_remove:
                remaining = [
                    device
                    for device in self._devices
                    if f"{device.get(CONF_IMEI)} ({device.get(CONF_DEVICE_NAME, device.get(CONF_IMEI))})"
                    not in to_remove
                ]
                if not remaining and not user_input.get("add_device", False):
                    errors["base"] = "last_device"
                else:
                    self._devices = remaining
            if not errors and user_input.get("add_device", False):
                return await self.async_step_add_device()
            if not errors:
                return await self._async_commit()

        return self.async_show_form(
            step_id="manage_devices", data_schema=self._manage_schema(), errors=errors
        )

    async def async_step_add_device(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Добавить новое устройство."""
        errors: dict[str, str] = {}
        if user_input is not None:
            imei = str(user_input.get(CONF_IMEI, "")).strip()
            name = str(user_input.get(CONF_DEVICE_NAME, "")).strip()
            if _invalid_imei(imei):
                errors[CONF_IMEI] = "invalid_imei"
            elif any(device.get(CONF_IMEI) == imei for device in self._devices):
                errors[CONF_IMEI] = "duplicate_imei"
            else:
                self._devices.append({CONF_IMEI: imei, CONF_DEVICE_NAME: name or imei})
                if user_input.get("add_another", False):
                    return await self.async_step_add_device()
                return await self.async_step_manage_devices()

        return self.async_show_form(
            step_id="add_device", data_schema=self._add_schema(), errors=errors
        )