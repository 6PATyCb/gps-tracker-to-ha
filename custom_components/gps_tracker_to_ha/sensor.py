"""Платформа sensor: battery, speed, satellites, course и статус опроса."""

from __future__ import annotations

from enum import StrEnum

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICES,
    CONF_DEVICE_NAME,
    CONF_IMEI,
    DOMAIN,
)
from .coordinator import GpsDeviceData, GpsTrackerCoordinator


class SensorKind(StrEnum):
    """Типы датчиков."""

    BATTERY = "battery"
    SPEED = "speed"
    SATELLITES = "satellites"
    COURSE = "course"
    LAST_POLL = "last_poll"


SENSOR_DEFINITIONS = {
    SensorKind.BATTERY: {
        "name": "Battery",
        "device_class": SensorDeviceClass.BATTERY,
        "unit": "%",
        "suffix": "battery",
    },
    SensorKind.SPEED: {
        "name": "Speed",
        "device_class": SensorDeviceClass.SPEED,
        "unit": "km/h",
        "suffix": "speed",
    },
    SensorKind.SATELLITES: {
        "name": "Satellites",
        "device_class": None,
        "unit": "sat",
        "suffix": "satellites",
    },
    SensorKind.COURSE: {
        "name": "Course",
        "device_class": None,
        "unit": "°",
        "suffix": "course",
    },
    SensorKind.LAST_POLL: {
        "name": "Last poll",
        "device_class": None,
        "unit": None,
        "suffix": "last_poll",
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Создать sensor-сущности для всех устройств записи."""
    coordinator: GpsTrackerCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    devices_config = config_entry.data.get(CONF_DEVICES, [])

    entities = []
    for device in devices_config:
        imei = str(device[CONF_IMEI])
        name = str(device.get(CONF_DEVICE_NAME) or imei)
        for kind in SensorKind:
            entities.append(GpsTrackerSensor(coordinator, imei, name, kind))

    if entities:
        async_add_entities(entities)


class GpsTrackerSensor(
    CoordinatorEntity[GpsTrackerCoordinator], SensorEntity
):
    """Датчик параметра одного трекера."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GpsTrackerCoordinator,
        imei: str,
        name: str,
        kind: SensorKind,
    ) -> None:
        """Инициализировать датчик."""
        super().__init__(coordinator)
        definition = SENSOR_DEFINITIONS[kind]
        self._imei = imei
        self._kind = kind
        self._snapshot: GpsDeviceData | None = None
        self._attr_name = definition["name"]
        self._attr_unique_id = f"{imei}_{definition['suffix']}"
        if definition["device_class"] is not None:
            self._attr_device_class = definition["device_class"]
        self._attr_native_unit_of_measurement = definition["unit"]
        if kind != SensorKind.LAST_POLL:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def device_info(self) -> DeviceInfo:
        """Связать с тем же устройством, что и device_tracker."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._imei)},
            name=self._device_name,
            manufacturer="GPS Tracker",
            model=f"IMEI {self._imei}",
        )

    @property
    def _device_name(self) -> str:
        """Имя устройства из последних данных координатора."""
        data = self.coordinator.data.get(self._imei)
        return data.name if data is not None else self._imei

    @callback
    def _handle_coordinator_update(self) -> None:
        """Обновить значение при новых данных."""
        data = self.coordinator.data.get(self._imei)
        if data is None:
            self._attr_available = False
            self._snapshot = None
            self.async_write_ha_state()
            return

        changed = data != self._snapshot
        if self._kind == SensorKind.LAST_POLL:
            # Сенсор статуса опроса всегда доступен, даже когда устройство офлайн.
            self._attr_available = True
        else:
            self._attr_available = data.available

        if self._kind == SensorKind.BATTERY:
            self._attr_native_value = data.battery if data.battery is not None else None
        elif self._kind == SensorKind.SPEED:
            self._attr_native_value = data.speed
        elif self._kind == SensorKind.SATELLITES:
            self._attr_native_value = data.satellites
        elif self._kind == SensorKind.COURSE:
            self._attr_native_value = data.course
        elif self._kind == SensorKind.LAST_POLL:
            self._attr_native_value = data.poll_status

        if changed:
            self._snapshot = data
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Детали последнего опроса для сенсора status'а опроса."""
        if self._kind != SensorKind.LAST_POLL:
            return {}
        data = self.coordinator.data.get(self._imei)
        if data is None:
            return {}
        return {
            "imei": self._imei,
            "last_poll_time": data.last_poll_time.isoformat(timespec="seconds")
            if data.last_poll_time
            else None,
            "last_http_status": data.last_http_status,
            "poll_error": data.poll_error,
            "available": data.available,
        }