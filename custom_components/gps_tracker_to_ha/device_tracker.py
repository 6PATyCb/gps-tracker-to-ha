"""Платформа device_tracker: по трекеру на каждое устройство."""

from __future__ import annotations

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_BATTERY,
    ATTR_COURSE,
    ATTR_HEADING,
    ATTR_IMEI,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_POSITION_VALID,
    ATTR_SATELLITES,
    ATTR_TIME,
    ATTR_VELOCITY,
    CONF_DEVICES,
    CONF_DEVICE_NAME,
    CONF_IMEI,
    DEFAULT_GPS_ACCURACY,
    DOMAIN,
)
from .coordinator import GpsDeviceData, GpsTrackerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Создать device_tracker сущности для всех устройств записи."""
    coordinator: GpsTrackerCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    devices_config = config_entry.data.get(CONF_DEVICES, [])

    entities = []
    for device in devices_config:
        imei = str(device[CONF_IMEI])
        name = str(device.get(CONF_DEVICE_NAME) or imei)
        entities.append(GpsTrackerDeviceTracker(coordinator, imei, name))

    if entities:
        async_add_entities(entities)


class GpsTrackerDeviceTracker(
    CoordinatorEntity[GpsTrackerCoordinator], TrackerEntity, RestoreEntity
):
    """device_tracker, положение которого обновляется координатором."""

    _attr_has_entity_name = True
    _attr_source_type = SourceType.GPS

    def __init__(self, coordinator: GpsTrackerCoordinator, imei: str, name: str) -> None:
        """Инициализировать трекер."""
        super().__init__(coordinator)
        self._imei = imei
        self._device_name = name
        self._snapshot: GpsDeviceData | None = None
        self._attr_unique_id = f"{imei}_tracker"

    @property
    def device_info(self) -> DeviceInfo:
        """Связать с устройством в registry (одно на IMEI)."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._imei)},
            name=self._device_name,
            manufacturer="GPS Tracker",
            model=f"IMEI {self._imei}",
        )

    @property
    def _device_data(self) -> GpsDeviceData | None:
        """Текущие данные из координатора."""
        return self.coordinator.data.get(self._imei)

    async def async_added_to_hass(self) -> None:
        """Восстановить последнюю известную позицию после перезапуска."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return
        try:
            lat = last_state.attributes.get("latitude")
            lon = last_state.attributes.get("longitude")
            if lat is not None and lon is not None:
                self._attr_latitude = float(lat)
                self._attr_longitude = float(lon)
                self._attr_location_accuracy = float(
                    last_state.attributes.get("gps_accuracy", DEFAULT_GPS_ACCURACY)
                )
        except (TypeError, ValueError):
            pass

    @callback
    def _handle_coordinator_update(self) -> None:
        """Обновить сущность при новых данных."""
        data = self.coordinator.data.get(self._imei)
        if data is None:
            self._attr_available = False
            self._snapshot = None
            self.async_write_ha_state()
            return

        changed = data.payload_changed(self._snapshot)
        self._attr_available = data.available

        if data.available and data.latitude is not None and data.longitude is not None:
            self._attr_latitude = data.latitude
            self._attr_longitude = data.longitude
            self._attr_location_accuracy = DEFAULT_GPS_ACCURACY

        if changed:
            self._snapshot = data
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Дополнительные атрибуты трекера."""
        data = self._device_data
        attrs: dict[str, object] = {
            ATTR_IMEI: self._imei,
        }
        if data is not None:
            attrs.update(
                {
                    ATTR_VELOCITY: data.speed,
                    ATTR_COURSE: data.course,
                    ATTR_HEADING: data.heading,
                    ATTR_SATELLITES: data.satellites,
                    ATTR_BATTERY: data.battery,
                    ATTR_LATITUDE: data.latitude,
                    ATTR_LONGITUDE: data.longitude,
                    ATTR_POSITION_VALID: data.position_valid,
                    ATTR_TIME: data.time.strftime("%Y-%m-%d %H:%M:%S") if data.time else None,
                }
            )
        return attrs