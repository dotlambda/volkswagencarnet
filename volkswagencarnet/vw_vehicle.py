#!/usr/bin/env python3
"""Vehicle class for We Connect."""
from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from json import dumps as to_json
from typing import Any

from volkswagencarnet.vw_timer import TimerData, Timer, BasicSettings
from .vw_const import VehicleStatusParameter as P, Services
from .vw_utilities import find_path, is_valid_path

# TODO
# Images (https://emea.bff.cariad.digital/media/v2/vehicle-images/WVWZZZ3HZPK002581?resolution=3x)
# {"data":[{"id":"door_right_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_front_overlay.png","fileName":"image_door_right_front_overlay.png"},{"id":"light_right","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_light_right.png","fileName":"image_light_right.png"},{"id":"sunroof_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_sunroof_overlay.png","fileName":"image_sunroof_overlay.png"},{"id":"trunk_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_trunk_overlay.png","fileName":"image_trunk_overlay.png"},{"id":"car_birdview","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_car_birdview.png","fileName":"image_car_birdview.png"},{"id":"door_left_front","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_front.png","fileName":"image_door_left_front.png"},{"id":"door_right_front","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_front.png","fileName":"image_door_right_front.png"},{"id":"sunroof","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_sunroof.png","fileName":"image_sunroof.png"},{"id":"window_right_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_right_front_overlay.png","fileName":"image_window_right_front_overlay.png"},{"id":"car_34view","url":"https://media.volkswagen.com/Vilma/V/3H9/2023/Front_Right/c8ca31fcf999b04d42940620653c494215e0d49756615f3524499261d96ccdce.png?width=1163","fileName":""},{"id":"door_left_back","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_back.png","fileName":"image_door_left_back.png"},{"id":"door_right_back","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_back.png","fileName":"image_door_right_back.png"},{"id":"window_left_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_left_back_overlay.png","fileName":"image_window_left_back_overlay.png"},{"id":"window_right_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_right_back_overlay.png","fileName":"image_window_right_back_overlay.png"},{"id":"bonnet_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_bonnet_overlay.png","fileName":"image_bonnet_overlay.png"},{"id":"door_left_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_back_overlay.png","fileName":"image_door_left_back_overlay.png"},{"id":"door_left_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_left_front_overlay.png","fileName":"image_door_left_front_overlay.png"},{"id":"door_right_back_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_door_right_back_overlay.png","fileName":"image_door_right_back_overlay.png"},{"id":"light_left","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_light_left.png","fileName":"image_light_left.png"},{"id":"window_left_front_overlay","url":"https://emea.bff.cariad.digital/media/v2/image/arteon_shooting_brake/3x/image_window_left_front_overlay.png","fileName":"image_window_left_front_overlay.png"}]}
#
# Model Year (unclear, seems to only be available via the web API, language dependent and with separate authentication)

BACKEND_RECEIVED_TIMESTAMP = "BACKEND_RECEIVED_TIMESTAMP"

_LOGGER = logging.getLogger(__name__)

ENGINE_TYPE_ELECTRIC = "electric"
ENGINE_TYPE_DIESEL = "diesel"
ENGINE_TYPE_GASOLINE = "gasoline"
ENGINE_TYPE_COMBUSTION = [ENGINE_TYPE_DIESEL, ENGINE_TYPE_GASOLINE]
DEFAULT_TARGET_TEMP = 24


class Vehicle:
    """Vehicle contains the state of sensors and methods for interacting with the car."""

    def __init__(self, conn, url):
        """Initialize the Vehicle with default values."""
        self._connection = conn
        self._url = url
        self._homeregion = "https://msg.volkswagen.de"
        self._discovered = False
        self._states = {}
        self._requests: dict[str, Any] = {
            "departuretimer": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "batterycharge": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "climatisation": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "refresh": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "lock": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "preheater": {"status": "", "timestamp": datetime.now(timezone.utc)},
            "remaining": -1,
            "latest": "",
            "state": "",
        }
        self._climate_duration: int = 30
        self._climatisation_target_temperature: float | None = None

        # API Endpoints that might be enabled for car (that we support)
        self._services: dict[str, dict[str, Any]] = {
            # TODO needs a complete rework...
            Services.ACCESS: {"active": False},
            Services.TRIP_STATISTICS: {"active": False},
            Services.MEASUREMENTS: {"active": False},
            Services.HONK_AND_FLASH: {"active": False},
            Services.PARKING_POSITION: {"active": False},
            Services.CLIMATISATION: {"active": False},
            Services.CHARGING: {"active": False},
            Services.PARAMETERS: {},
            # "rheating_v1": {"active": False},
            # "rclima_v1": {"active": False},
            # "statusreport_v1": {"active": False},
            # "rbatterycharge_v1": {"active": False},
            # "carfinder_v1": {"active": False},
            # "timerprogramming_v1": {"active": False},
            # "jobs_v1": {"active": False},
            # "owner_v1": {"active": False},
            # vehicles_v1_cai, services_v1, vehicletelemetry_v1
        }

    def _in_progress(self, topic: str, unknown_offset: int = 0) -> bool:
        """Check if request is already in progress."""
        if self._requests.get(topic, {}).get("id", False):
            timestamp = self._requests.get(topic, {}).get(
                "timestamp", datetime.now(timezone.utc) - timedelta(minutes=unknown_offset)
            )
            if timestamp + timedelta(minutes=3) < datetime.now(timezone.utc):
                self._requests.get(topic, {}).pop("id")
            else:
                _LOGGER.info(f"Action ({topic}) already in progress")
                return True
        return False

    async def _handle_response(self, response, topic: str, error_msg: str | None = None) -> bool:
        """Handle errors in response and get requests remaining."""
        if not response:
            self._requests[topic] = {"status": "Failed", "timestamp": datetime.now(timezone.utc)}
            _LOGGER.error(error_msg if error_msg is not None else f"Failed to perform {topic} action")
            raise Exception(error_msg if error_msg is not None else f"Failed to perform {topic} action")
        else:
            remaining = response.get("rate_limit_remaining", -1)
            if remaining != -1:
                _LOGGER.info(f"{remaining} requests")
                self._requests["remaining"] = remaining
            self._requests[topic] = {
                "timestamp": datetime.now(timezone.utc),
                "status": response.get("state", "Unknown"),
                "id": response.get("id", 0),
            }
            if response.get("state", None) == "Throttled":
                status = "Throttled"
                _LOGGER.warning(f"Request throttled ({topic}")
            else:
                status = await self.wait_for_request(request=response.get("id", 0))
            self._requests[topic] = {"status": status, "timestamp": datetime.now(timezone.utc)}
        return True

    # API get and set functions #
    # Init and update vehicle data
    async def discover(self):
        """Discover vehicle and initial data."""

        _LOGGER.debug("Attempting discovery of supported API endpoints for vehicle.")
        capabilities_response = await self._connection.getOperationList(self.vin)
        parameters_list = capabilities_response.get("parameters", {})
        capabilities_list = capabilities_response.get("capabilities", {})
        if parameters_list:
            self._services[Services.PARAMETERS].update(parameters_list)
        if capabilities_list:
            for service_id in capabilities_list.keys():
                try:
                    if service_id in self._services.keys():
                        service = capabilities_list[service_id]
                        data = {}
                        service_name = service.get("id", None)
                        if service.get("isEnabled", False):
                            _LOGGER.debug(f"Discovered enabled service: {service_name}")
                            data["active"] = True
                            if service.get("expirationDate", False):
                                data["expiration"] = service.get("expirationDate", None)
                            if service.get("operations", False):
                                data.update({"operations": []})
                                for operation_id in service.get("operations", []).keys():
                                    operation = service.get("operations").get(operation_id)
                                    data["operations"].append(operation.get("id", None))
                            if service.get("parameters", False):
                                data.update({"parameters": []})
                                for parameter in service.get("parameters", []):
                                    data["parameters"].append(parameter)
                        else:
                            reason = service.get("status", "Unknown")
                            _LOGGER.debug(f"Service: {service_name} is disabled because of reason: {reason}")
                            data["active"] = False
                        self._services[service_name].update(data)
                except Exception as error:
                    _LOGGER.warning(f'Encountered exception: "{error}" while parsing service item: {service}')
        else:
            _LOGGER.warning(f"Could not determine available API endpoints for {self.vin}")
        _LOGGER.debug(f"API endpoints: {self._services}")
        self._discovered = True

    async def update(self):
        """Try to fetch data for all known API endpoints."""
        if not self._discovered:
            await self.discover()
        if not self.deactivated:
            await asyncio.gather(
                # TODO: we don't check against capabilities currently, but this also doesn't seem to be necessary
                # to be checked if we should still do it for UI purposes
                self.get_selectivestatus(
                    [
                        Services.ACCESS,
                        Services.FUEL_STATUS,
                        Services.VEHICLE_LIGHTS,
                        Services.VEHICLE_HEALTH_INSPECTION,
                        Services.MEASUREMENTS,
                        Services.CHARGING,
                        Services.CLIMATISATION,
                    ]
                ),
                self.get_vehicle(),
                self.get_parkingposition(),
                self.get_trip_last(),
                #     return_exceptions=True,
            )
            await asyncio.gather(self.get_service_status())
        else:
            _LOGGER.info(f"Vehicle with VIN {self.vin} is deactivated.")

    # Data collection functions
    async def get_selectivestatus(self, services):
        """Fetch selective status for specified services."""
        data = await self._connection.getSelectiveStatus(self.vin, services)
        if data:
            self._states.update(data)

    async def get_vehicle(self):
        """Fetch car masterdata."""
        data = await self._connection.getVehicleData(self.vin)
        if data:
            self._states.update(data)

    async def get_parkingposition(self):
        """Fetch parking position if supported."""
        if self._services.get(Services.PARKING_POSITION, {}).get("active", False):
            data = await self._connection.getParkingPosition(self.vin)
            if data:
                self._states.update(data)

    async def get_trip_last(self):
        """Fetch last trip statistics if supported."""
        if self._services.get(Services.TRIP_STATISTICS, {}).get("active", False):
            data = await self._connection.getTripLast(self.vin)
            if data:
                self._states.update(data)

    async def get_service_status(self):
        """Fetch service status."""
        data = await self._connection.get_service_status()
        if data:
            self._states.update({Services.SERVICE_STATUS: data})

    async def wait_for_request(self, request, retry_count=18):
        """Update status of outstanding requests."""
        retry_count -= 1
        if retry_count == 0:
            _LOGGER.info(f"Timeout while waiting for result of {request.requestId}.")
            return "Timeout"
        try:
            status = await self._connection.get_request_status(self.vin, request)
            _LOGGER.debug(f"Request ID {request}: {status}")
            self._requests["state"] = status
            if status == "In Progress":
                await asyncio.sleep(10)
                return await self.wait_for_request(request, retry_count)
            else:
                return status
        except Exception as error:
            _LOGGER.warning(f"Exception encountered while waiting for request status: {error}")
            return "Exception"

    async def wait_for_data_refresh(self, retry_count=18):
        """Update status of outstanding requests."""
        retry_count -= 1
        if retry_count == 0:
            _LOGGER.info("Timeout while waiting for data refresh.")
            return "Timeout"
        try:
            await self.get_selectivestatus([Services.MEASUREMENTS])
            refresh_trigger_time = self._requests.get("refresh", {}).get("timestamp")
            if self.last_connected < refresh_trigger_time:
                await asyncio.sleep(10)
                return await self.wait_for_data_refresh(retry_count)
            else:
                return "successful"
        except Exception as error:
            _LOGGER.warning(f"Exception encountered while waiting for data refresh: {error}")
            return "Exception"

    # Data set functions
    # Charging (BATTERYCHARGE)
    async def set_charger_current(self, value) -> bool:
        """Set charger current."""
        if self.is_charging_supported:
            if 1 <= int(value) <= 255:
                data = {"action": {"settings": {"maxChargeCurrent": int(value)}, "type": "setSettings"}}
            else:
                _LOGGER.error(f"Set charger maximum current to {value} is not supported.")
                raise Exception(f"Set charger maximum current to {value} is not supported.")
            return await self.set_charger(data)
        else:
            _LOGGER.error("No charger support.")
            raise Exception("No charger support.")

    async def set_charge_min_level(self, level: int):
        """Set the desired minimum charge level for departure schedules."""
        raise Exception("Should have to be re-implemented")

    async def set_charger(self, action) -> bool:
        """Turn on/off charging."""
        if self.is_charging_supported:
            if action not in ["start", "stop"]:
                _LOGGER.error(f'Charging action "{action}" is not supported.')
                raise Exception(f'Charging action "{action}" is not supported.')
            response = await self._connection.setCharging(self.vin, (action == "start"))
            return await self._handle_response(
                response=response, topic="charging", error_msg=f"Failed to {action} charging"
            )
        else:
            _LOGGER.error("No charging support.")
            raise Exception("No charging support.")

    async def set_charging_settings(self, action) -> bool:
        """Turn on/off reduced charging."""
        if self.is_charge_max_ac_setting_supported:
            if action not in ["reduced", "maximum"]:
                _LOGGER.error(f'Charging setting "{action}" is not supported.')
                raise Exception(f'Charging setting "{action}" is not supported.')
            data = {"maxChargeCurrentAC": action}
            response = await self._connection.setChargingSettings(self.vin, data)
            return await self._handle_response(
                response=response, topic="charging", error_msg=f"Failed to {action} charging"
            )
        else:
            _LOGGER.error("Charging settings are not supported.")
            raise Exception("Charging settings are not supported.")

    # Climatisation electric/auxiliary/windows (CLIMATISATION)
    async def set_climatisation_temp(self, temperature=20):
        """Set climatisation target temp."""
        if self.is_electric_climatisation_supported or self.is_auxiliary_climatisation_supported:
            if 15.5 <= float(temperature) <= 30:
                data = {
                    "targetTemperature": float(temperature),
                    "targetTemperatureUnit": "celsius",
                    "climatisationWithoutExternalPower": self.climatisation_without_external_power,
                }
            else:
                _LOGGER.error(f"Set climatisation target temp to {temperature} is not supported.")
                raise Exception(f"Set climatisation target temp to {temperature} is not supported.")
            self._requests["latest"] = "Climatisation"
            response = await self._connection.setClimaterSettings(self.vin, data)
            return await self._handle_response(
                response=response, topic="climatisation", error_msg="Failed to set temperature"
            )
        else:
            _LOGGER.error("No climatisation support.")
            raise Exception("No climatisation support.")

    async def set_window_heating(self, action="stop"):
        """Turn on/off window heater."""
        if self.is_window_heater_supported:
            if action not in ["start", "stop"]:
                _LOGGER.error(f'Window heater action "{action}" is not supported.')
                raise Exception(f'Window heater action "{action}" is not supported.')
            self._requests["latest"] = "Climatisation"
            response = await self._connection.setWindowHeater(self.vin, (action == "start"))
            return await self._handle_response(
                response=response, topic="climatisation", error_msg=f"Failed to {action} window heating"
            )
        else:
            _LOGGER.error("No climatisation support.")
            raise Exception("No climatisation support.")

    async def set_battery_climatisation(self, mode=False):
        """Turn on/off electric climatisation from battery."""
        if self.is_climatisation_without_external_power_supported:
            if mode in [True, False]:
                temperature = (
                    self.climatisation_target_temperature
                    if self.climatisation_target_temperature is not None
                    else DEFAULT_TARGET_TEMP
                )
                data = {
                    "targetTemperature": temperature,
                    "targetTemperatureUnit": "celsius",
                    "climatisationWithoutExternalPower": mode,
                }
                self._requests["latest"] = "Climatisation"
                response = await self._connection.setClimaterSettings(self.vin, data)
                return await self._handle_response(
                    response=response,
                    topic="climatisation",
                    error_msg="Failed to set climatisation without external power",
                )
            else:
                _LOGGER.error(f'Set climatisation without external power to "{mode}" is not supported.')
                raise Exception(f'Set climatisation without external power to "{mode}" is not supported.')
        else:
            _LOGGER.error("No climatisation support.")
            raise Exception("No climatisation support.")

    async def set_climatisation(self, action="stop"):
        """Turn on/off climatisation with electric/auxiliary heater."""
        if self.is_electric_climatisation_supported:
            if action == "start":
                data = {
                    "climatisationWithoutExternalPower": self.climatisation_without_external_power,
                    "targetTemperature": self.climatisation_target_temperature,
                    "targetTemperatureUnit": "celsius",
                }
            elif action == "stop":
                data = {}
            else:
                _LOGGER.error(f"Invalid climatisation action: {action}")
                raise Exception(f"Invalid climatisation action: {action}")
            self._requests["latest"] = "Climatisation"
            response = await self._connection.setClimater(self.vin, data, (action == "start"))
            return await self._handle_response(
                response=response,
                topic="climatisation",
                error_msg=f"Failed to {action} climatisation with electric/auxiliary heater.",
            )
        else:
            _LOGGER.error("No climatisation support.")
            raise Exception("No climatisation support.")

    # Parking heater heating/ventilation (RS)
    async def set_pheater(self, mode, spin):
        """Set the mode for the parking heater."""
        raise Exception("Should have to be re-implemented")

    # Lock (RLU)
    async def set_lock(self, action, spin):
        """Remote lock and unlock actions."""
        if not self._services.get(Services.ACCESS, {}).get("active", False):
            _LOGGER.info("Remote lock/unlock is not supported.")
            raise Exception("Remote lock/unlock is not supported.")
        if self._in_progress("lock", unknown_offset=-5):
            return False
        if action not in ["lock", "unlock"]:
            _LOGGER.error(f"Invalid lock action: {action}")
            raise Exception(f"Invalid lock action: {action}")

        try:
            self._requests["latest"] = "Lock"
            response = await self._connection.setLock(self.vin, (action == "lock"), spin)
            return await self._handle_response(
                response=response, topic="access", error_msg=f"Failed to {action} vehicle"
            )
        except Exception as error:
            _LOGGER.warning(f"Failed to {action} vehicle - {error}")
            self._requests["lock"] = {"status": "Exception", "timestamp": datetime.now(timezone.utc)}
        raise Exception("Lock action failed")

    # Refresh vehicle data (VSR)
    async def set_refresh(self):
        """Wake up vehicle and update status data."""
        if self._in_progress("refresh", unknown_offset=-5):
            return False
        try:
            self._requests["latest"] = "Refresh"
            response = await self._connection.wakeUpVehicle(self.vin)
            if response:
                if response.status == 204:
                    self._requests["state"] = "in_progress"
                    self._requests["refresh"] = {
                        "timestamp": datetime.now(timezone.utc),
                        "status": "in_progress",
                        "id": 0,
                    }
                    status = await self.wait_for_data_refresh()
                elif response.status == 429:
                    status = "Throttled"
                    _LOGGER.debug("Server side throttled. Try again later.")
                else:
                    _LOGGER.debug(f"Unable to refresh the data. Incorrect response code: {response.status}")
                self._requests["state"] = status
                self._requests["refresh"] = {"status": status, "timestamp": datetime.now(timezone.utc)}
                return True
            else:
                _LOGGER.debug("Unable to refresh the data.")
        except Exception as error:
            _LOGGER.warning(f"Failed to execute data refresh - {error}")
            self._requests["refresh"] = {"status": "Exception", "timestamp": datetime.now(timezone.utc)}
        raise Exception("Data refresh failed")

    async def set_schedule(self, data: TimerData) -> bool:
        """Store schedule."""
        raise Exception("Should have to be re-implemented")

    # Vehicle class helpers #
    # Vehicle info
    @property
    def attrs(self):
        """
        Return all attributes.

        :return:
        """
        return self._states

    def has_attr(self, attr) -> bool:
        """
        Return true if attribute exists.

        :param attr:
        :return:
        """
        return is_valid_path(self.attrs, attr)

    def get_attr(self, attr):
        """
        Return a specific attribute.

        :param attr:
        :return:
        """
        return find_path(self.attrs, attr)

    async def expired(self, service):
        """Check if access to service has expired."""
        try:
            now = datetime.utcnow()
            if self._services.get(service, {}).get("expiration", False):
                expiration = self._services.get(service, {}).get("expiration", False)
                if not expiration:
                    expiration = datetime.utcnow() + timedelta(days=1)
            else:
                _LOGGER.debug(f"Could not determine end of access for service {service}, assuming it is valid")
                expiration = datetime.utcnow() + timedelta(days=1)
            expiration = expiration.replace(tzinfo=None)
            if now >= expiration:
                _LOGGER.warning(f"Access to {service} has expired!")
                self._discovered = False
                return True
            else:
                return False
        except Exception:
            _LOGGER.debug(f"Exception. Could not determine end of access for service {service}, assuming it is valid")
            return False

    def dashboard(self, **config):
        """
        Return dashboard with specified configuration.

        :param config:
        :return:
        """
        # Classic python notation
        from .vw_dashboard import Dashboard

        return Dashboard(self, **config)

    @property
    def vin(self) -> str:
        """
        Vehicle identification number.

        :return:
        """
        return self._url

    @property
    def unique_id(self) -> str:
        """
        Return unique id for the vehicle (vin).

        :return:
        """
        return self.vin

    # Information from vehicle states #
    # Car information
    @property
    def nickname(self) -> str | None:
        """
        Return nickname of the vehicle.

        :return:
        """
        return self.attrs.get("vehicle", {}).get("nickname", None)

    @property
    def is_nickname_supported(self) -> bool:
        """
        Return true if naming the vehicle is supported.

        :return:
        """
        return self.attrs.get("vehicle", {}).get("nickname", False) is not False

    @property
    def deactivated(self) -> bool | None:
        """
        Return true if service is deactivated.

        :return:
        """
        return self.attrs.get("carData", {}).get("deactivated", None)

    @property
    def is_deactivated_supported(self) -> bool:
        """
        Return true if service deactivation status is supported.

        :return:
        """
        return self.attrs.get("carData", {}).get("deactivated", False) is True

    @property
    def model(self) -> str | None:
        """Return model."""
        return self.attrs.get("vehicle", {}).get("model", None)

    @property
    def is_model_supported(self) -> bool:
        """Return true if model is supported."""
        return self.attrs.get("vehicle", {}).get("modelName", False) is not False

    @property
    def model_year(self) -> bool | None:
        """Return model year."""
        return self.attrs.get("vehicle", {}).get("modelYear", None)

    @property
    def is_model_year_supported(self) -> bool:
        """Return true if model year is supported."""
        return self.attrs.get("vehicle", {}).get("modelYear", False) is not False

    @property
    def model_image(self) -> str:
        # Not implemented
        """Return vehicle model image."""
        return self.attrs.get("imageUrl")

    @property
    def is_model_image_supported(self) -> bool:
        """
        Return true if vehicle model image is supported.

        :return:
        """
        # Not implemented
        return self.attrs.get("imageUrl", False) is not False

    # Lights
    @property
    def parking_light(self) -> bool:
        """Return true if parking light is on."""
        lights = self.attrs.get(Services.VEHICLE_LIGHTS).get("lightsStatus").get("value").get("lights")
        lights_on_count = 0
        for light in lights:
            if light["status"] == "on":
                lights_on_count = lights_on_count + 1
        return lights_on_count == 1

    @property
    def parking_light_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self.attrs.get(Services.VEHICLE_LIGHTS).get("lightsStatus").get("value").get("carCapturedTimestamp")

    @property
    def is_parking_light_supported(self) -> bool:
        """Return true if parking light is supported."""
        return self.attrs.get(Services.VEHICLE_LIGHTS, False) and is_valid_path(
            self.attrs, f"{Services.VEHICLE_LIGHTS}.lightsStatus.value.lights"
        )

    # Connection status
    @property
    def last_connected(self) -> datetime:
        """Return when vehicle was last connected to connect servers in local time."""
        # this field is only a dirty hack, because there is no overarching information for the car anymore,
        # only information per service, so we just use the one for fuelStatus.rangeStatus when car is ideling
        # and charing.batteryStatus when electic car is charging
        """Return attribute last updated timestamp."""
        if self.is_battery_level_supported and self.charging:
            return self.battery_level_last_updated
        elif self.is_distance_supported:
            if type(self.distance_last_updated) is str:
                return (
                    datetime.strptime(self.distance_last_updated, "%Y-%m-%dT%H:%M:%S.%fZ")
                    .replace(microsecond=0)
                    .replace(tzinfo=timezone.utc)
                )
            else:
                return self.distance_last_updated

    @property
    def last_connected_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        if self.is_battery_level_supported and self.charging:
            return self.battery_level_last_updated
        elif self.is_distance_supported:
            if type(self.distance_last_updated) is str:
                return (
                    datetime.strptime(self.distance_last_updated, "%Y-%m-%dT%H:%M:%S.%fZ")
                    .replace(microsecond=0)
                    .replace(tzinfo=timezone.utc)
                )
            else:
                return self.distance_last_updated

    @property
    def is_last_connected_supported(self) -> bool:
        """Return if when vehicle was last connected to connect servers is supported."""
        return self.is_battery_level_supported or self.is_distance_supported

    # Service information
    @property
    def distance(self) -> int | None:
        """Return vehicle odometer."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.odometerStatus.value.odometer")

    @property
    def distance_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.odometerStatus.value.carCapturedTimestamp")

    @property
    def is_distance_supported(self) -> bool:
        """Return true if odometer is supported."""
        return is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.odometerStatus.value.odometer")

    @property
    def service_inspection(self):
        """Return time left for service inspection."""
        return int(
            find_path(self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.inspectionDue_days")
        )

    @property
    def service_inspection_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.carCapturedTimestamp"
        )

    @property
    def is_service_inspection_supported(self) -> bool:
        """
        Return true if days to service inspection is supported.

        :return:
        """
        return is_valid_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.inspectionDue_days"
        )

    @property
    def service_inspection_distance(self):
        """Return distance left for service inspection."""
        return int(
            find_path(self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.inspectionDue_km")
        )

    @property
    def service_inspection_distance_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.carCapturedTimestamp"
        )

    @property
    def is_service_inspection_distance_supported(self) -> bool:
        """
        Return true if distance to service inspection is supported.

        :return:
        """
        return is_valid_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.inspectionDue_km"
        )

    @property
    def oil_inspection(self):
        """Return time left for oil inspection."""
        return int(
            find_path(self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.oilServiceDue_days")
        )

    @property
    def oil_inspection_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.carCapturedTimestamp"
        )

    @property
    def is_oil_inspection_supported(self) -> bool:
        """
        Return true if days to oil inspection is supported.

        :return:
        """
        if not self.has_combustion_engine():
            return False
        return is_valid_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.carCapturedTimestamp"
        )

    @property
    def oil_inspection_distance(self):
        """Return distance left for oil inspection."""
        return int(
            find_path(self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.oilServiceDue_km")
        )

    @property
    def oil_inspection_distance_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.carCapturedTimestamp"
        )

    @property
    def is_oil_inspection_distance_supported(self) -> bool:
        """
        Return true if oil inspection distance is supported.

        :return:
        """
        if not self.has_combustion_engine():
            return False
        return is_valid_path(
            self.attrs, f"{Services.VEHICLE_HEALTH_INSPECTION}.maintenanceStatus.value.oilServiceDue_km"
        )

    @property
    def adblue_level(self) -> int:
        """Return adblue level."""
        return int(find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.adBlueRange"))

    @property
    def adblue_level_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.carCapturedTimestamp")

    @property
    def is_adblue_level_supported(self) -> bool:
        """Return true if adblue level is supported."""
        return is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.adBlueRange")

    # Charger related states for EV and PHEV
    @property
    def charging(self) -> bool:
        """Return charging state."""
        cstate = find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargingState")
        return cstate == "charging"

    @property
    def charging_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp")

    @property
    def is_charging_supported(self) -> bool:
        """Return true if charging is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargingState")

    @property
    def charging_power(self) -> int:
        """Return charging power."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargePower_kW")

    @property
    def charging_power_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp")

    @property
    def is_charging_power_supported(self) -> bool:
        """Return true if charging power is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargePower_kW")

    @property
    def charging_rate(self) -> int:
        """Return charging rate."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargeRate_kmph")

    @property
    def charging_rate_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp")

    @property
    def is_charging_rate_supported(self) -> bool:
        """Return true if charging rate is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargeRate_kmph")

    @property
    def charger_type(self) -> str:
        """Return charger type."""
        charger_type = find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargeType")
        if charger_type == "ac":
            return "AC"
        elif charger_type == "dc":
            return "DC"
        return "Unknown"

    @property
    def charger_type_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp")

    @property
    def is_charger_type_supported(self) -> bool:
        """Return true if charger type is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargeType")

    @property
    def battery_level(self) -> int:
        """Return battery level."""
        return int(find_path(self.attrs, f"{Services.CHARGING}.batteryStatus.value.currentSOC_pct"))

    @property
    def battery_level_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.batteryStatus.value.carCapturedTimestamp")

    @property
    def is_battery_level_supported(self) -> bool:
        """Return true if battery level is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.batteryStatus.value.currentSOC_pct")

    @property
    def battery_target_charge_level(self) -> int:
        """Return target charge level."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.targetSOC_pct")

    @property
    def battery_target_charge_level_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.carCapturedTimestamp")

    @property
    def is_battery_target_charge_level_supported(self) -> bool:
        """Return true if target charge level is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.targetSOC_pct")

    @property
    def charge_max_ac_setting(self) -> str | int:
        """Return charger max ampere setting."""
        value = find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC")
        return value

    @property
    def charge_max_ac_setting_last_updated(self) -> datetime:
        """Return charger max ampere last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.carCapturedTimestamp")

    @property
    def is_charge_max_ac_setting_supported(self) -> bool:
        """Return true if Charger Max Ampere is supported."""
        if is_valid_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC"):
            value = find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC")
            return value in ["reduced", "maximum"]
        return False

    @property
    def charge_max_ac_ampere(self) -> str | int:
        """Return charger max ampere setting."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC_A")

    @property
    def charge_max_ac_ampere_last_updated(self) -> datetime:
        """Return charger max ampere last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.carCapturedTimestamp")

    @property
    def is_charge_max_ac_ampere_supported(self) -> bool:
        """Return true if Charger Max Ampere is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingSettings.value.maxChargeCurrentAC_A")

    @property
    def charging_cable_locked(self) -> bool:
        """Return plug locked state."""
        response = find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.plugLockState")
        return response == "locked"

    @property
    def charging_cable_locked_last_updated(self) -> datetime:
        """Return plug locked state."""
        return find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.carCapturedTimestamp")

    @property
    def is_charging_cable_locked_supported(self) -> bool:
        """Return true if plug locked state is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.plugLockState")

    @property
    def charging_cable_connected(self) -> bool:
        """Return plug connected state."""
        response = find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.plugConnectionState")
        return response == "connected"

    @property
    def charging_cable_connected_last_updated(self) -> datetime:
        """Return plug connected state last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.carCapturedTimestamp")

    @property
    def is_charging_cable_connected_supported(self) -> bool:
        """Return true if supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.plugConnectionState")

    @property
    def charging_time_left(self) -> int:
        """Return minutes to charging complete."""
        if is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.remainingChargingTimeToComplete_min"):
            return int(
                find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.remainingChargingTimeToComplete_min")
            )
        return None

    @property
    def charging_time_left_last_updated(self) -> datetime:
        """Return minutes to charging complete last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.carCapturedTimestamp")

    @property
    def is_charging_time_left_supported(self) -> bool:
        """Return true if charging is supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.chargingStatus.value.chargingState")

    @property
    def external_power(self) -> bool:
        """Return true if external power is connected."""
        check = find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.externalPower")
        return check in ["stationConnected", "available", "ready"]

    @property
    def external_power_last_updated(self) -> datetime:
        """Return external power last updated."""
        return find_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.carCapturedTimestamp")

    @property
    def is_external_power_supported(self) -> bool:
        """External power supported."""
        return is_valid_path(self.attrs, f"{Services.CHARGING}.plugStatus.value.externalPower")

    @property
    def reduced_ac_charging(self) -> bool:
        """Return reduced charging state."""
        return self.charge_max_ac_setting == "reduced"

    @property
    def reduced_ac_charging_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self.charge_max_ac_setting_last_updated

    @property
    def is_reduced_ac_charging_supported(self) -> bool:
        """Return true if reduced charging is supported."""
        return self.is_charge_max_ac_setting_supported

    @property
    def energy_flow(self):
        # TODO untouched
        """Return true if energy is flowing through charging port."""
        check = (
            self.attrs.get("charger", {})
            .get("status", {})
            .get("chargingStatusData", {})
            .get("energyFlow", {})
            .get("content", "off")
        )
        return check == "on"

    @property
    def energy_flow_last_updated(self) -> datetime:
        # TODO untouched
        """Return energy flow last updated."""
        return (
            self.attrs.get("charger", {})
            .get("status", {})
            .get("chargingStatusData", {})
            .get("energyFlow", {})
            .get("timestamp")
        )

    @property
    def is_energy_flow_supported(self) -> bool:
        # TODO untouched
        """Energy flow supported."""
        return self.attrs.get("charger", {}).get("status", {}).get("chargingStatusData", {}).get("energyFlow", False)

    # Vehicle location states
    @property
    def position(self) -> dict[str, str | float | None]:
        """Return  position."""
        output: dict[str, str | float | None]
        try:
            if self.vehicle_moving:
                output = {"lat": None, "lng": None, "timestamp": None}
            else:
                lat = float(find_path(self.attrs, "parkingposition.lat"))
                lng = float(find_path(self.attrs, "parkingposition.lon"))
                parking_time = find_path(self.attrs, "parkingposition.carCapturedTimestamp")
                output = {"lat": lat, "lng": lng, "timestamp": parking_time}
        except Exception:
            output = {
                "lat": "?",
                "lng": "?",
            }
        return output

    @property
    def position_last_updated(self) -> datetime:
        """Return  position last updated."""
        return self.attrs.get("parkingposition", {}).get("carCapturedTimestamp", "Unknown")

    @property
    def is_position_supported(self) -> bool:
        """Return true if position is available."""
        return is_valid_path(self.attrs, "parkingposition.carCapturedTimestamp") or self.attrs.get("isMoving", False)

    @property
    def vehicle_moving(self) -> bool:
        """Return true if vehicle is moving."""
        return self.attrs.get("isMoving", False)

    @property
    def vehicle_moving_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self.position_last_updated

    @property
    def is_vehicle_moving_supported(self) -> bool:
        """Return true if vehicle supports position."""
        return self.is_position_supported

    @property
    def parking_time(self) -> datetime:
        """Return timestamp of last parking time."""
        parking_time_path = "parkingposition.carCapturedTimestamp"
        if is_valid_path(self.attrs, parking_time_path):
            return find_path(self.attrs, parking_time_path)
        return None

    @property
    def parking_time_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self.position_last_updated

    @property
    def is_parking_time_supported(self) -> bool:
        """Return true if vehicle parking timestamp is supported."""
        return self.is_position_supported

    # Vehicle fuel level and range
    @property
    def electric_range(self) -> int:
        """
        Return electric range.

        :return:
        """
        return int(find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.electricRange"))

    @property
    def electric_range_last_updated(self) -> datetime:
        """Return electric range last updated."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.carCapturedTimestamp")

    @property
    def is_electric_range_supported(self) -> bool:
        """
        Return true if electric range is supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.electricRange")

    @property
    def combustion_range(self) -> int:
        """
        Return combustion engine range.

        :return:
        """
        DIESEL_RANGE = f"{Services.MEASUREMENTS}.rangeStatus.value.dieselRange"
        GASOLINE_RANGE = f"{Services.MEASUREMENTS}.rangeStatus.value.gasolineRange"
        if is_valid_path(self.attrs, DIESEL_RANGE):
            return int(find_path(self.attrs, DIESEL_RANGE))
        if is_valid_path(self.attrs, GASOLINE_RANGE):
            return int(find_path(self.attrs, GASOLINE_RANGE))
        return -1

    @property
    def combustion_range_last_updated(self) -> datetime | None:
        """Return combustion engine range last updated."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.carCapturedTimestamp")

    @property
    def is_combustion_range_supported(self) -> bool:
        """
        Return true if combustion range is supported, i.e. false for EVs.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.dieselRange") or is_valid_path(
            self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.gasolineRange"
        )

    @property
    def combined_range(self) -> int:
        """
        Return combined range.

        :return:
        """
        return int(find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.totalRange_km"))

    @property
    def combined_range_last_updated(self) -> datetime | None:
        """Return combined range last updated."""
        return find_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.carCapturedTimestamp")

    @property
    def is_combined_range_supported(self) -> bool:
        """
        Return true if combined range is supported.

        :return:
        """
        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.rangeStatus.value.totalRange_km"):
            return self.is_electric_range_supported and self.is_combustion_range_supported
        return False

    @property
    def fuel_level(self) -> int:
        """
        Return fuel level.

        :return:
        """
        fuel_level_pct = ""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.currentFuelLevel_pct"):
            fuel_level_pct = find_path(
                self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.currentFuelLevel_pct"
            )

        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.currentFuelLevel_pct"):
            fuel_level_pct = find_path(
                self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.currentFuelLevel_pct"
            )
        return int(fuel_level_pct)

    @property
    def fuel_level_last_updated(self) -> datetime:
        """Return fuel level last updated."""
        fuel_level_lastupdated = ""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carCapturedTimestamp"):
            fuel_level_lastupdated = find_path(
                self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.carCapturedTimestamp"
            )

        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carCapturedTimestamp"):
            fuel_level_lastupdated = find_path(
                self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.carCapturedTimestamp"
            )
        return fuel_level_lastupdated

    @property
    def is_fuel_level_supported(self) -> bool:
        """
        Return true if fuel level reporting is supported.

        :return:
        """
        return is_valid_path(
            self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.currentFuelLevel_pct"
        ) or is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.currentFuelLevel_pct")

    # Climatisation settings
    @property
    def climatisation_target_temperature(self) -> float | None:
        """Return the target temperature from climater."""
        # TODO should we handle Fahrenheit??
        return float(find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.targetTemperature_C"))

    @property
    def climatisation_target_temperature_last_updated(self) -> datetime:
        """Return the target temperature from climater last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.carCapturedTimestamp")

    @property
    def is_climatisation_target_temperature_supported(self) -> bool:
        """Return true if climatisation target temperature is supported."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.targetTemperature_C")

    @property
    def climatisation_without_external_power(self):
        """Return state of climatisation from battery power."""
        return find_path(
            self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.climatisationWithoutExternalPower"
        )

    @property
    def climatisation_without_external_power_last_updated(self) -> datetime:
        """Return state of climatisation from battery power last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.carCapturedTimestamp")

    @property
    def is_climatisation_without_external_power_supported(self) -> bool:
        """Return true if climatisation on battery power is supported."""
        return is_valid_path(
            self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.climatisationWithoutExternalPower"
        )

    @property
    def outside_temperature(self) -> float | bool:  # FIXME should probably be Optional[float] instead
        """Return outside temperature."""
        # TODO not found yet
        response = int(self.attrs.get("StoredVehicleDataResponseParsed")[P.OUTSIDE_TEMPERATURE].get("value", None))
        if response is not None:
            return round(float((response / 10) - 273.15), 1)
        else:
            return False

    @property
    def outside_temperature_last_updated(self) -> datetime:
        """Return outside temperature last updated."""
        # TODO not found yet
        return self.attrs.get("StoredVehicleDataResponseParsed")[P.OUTSIDE_TEMPERATURE].get(BACKEND_RECEIVED_TIMESTAMP)

    @property
    def is_outside_temperature_supported(self) -> bool:
        """Return true if outside temp is supported."""
        # TODO not found yet
        if self.attrs.get("StoredVehicleDataResponseParsed", False):
            if P.OUTSIDE_TEMPERATURE in self.attrs.get("StoredVehicleDataResponseParsed"):
                if "value" in self.attrs.get("StoredVehicleDataResponseParsed")[P.OUTSIDE_TEMPERATURE]:
                    return True
        return False

    # Climatisation, electric
    @property
    def electric_climatisation(self) -> bool:
        """Return status of climatisation."""
        status = find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.climatisationState")
        return status in ["ventilation", "heating", "on"]

    @property
    def electric_climatisation_last_updated(self) -> datetime:
        """Return status of climatisation last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.carCapturedTimestamp")

    @property
    def is_electric_climatisation_supported(self) -> bool:
        """Return true if vehicle has climater."""
        return (
            self.is_climatisation_supported
            and self.is_climatisation_target_temperature_supported
            and self.is_climatisation_without_external_power_supported
        )

    @property
    def auxiliary_climatisation(self) -> bool:
        """Return status of auxiliary climatisation."""
        climatisation_state = find_path(
            self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.climatisationState"
        )
        if climatisation_state in ["heating", "heatingAuxiliary", "on"]:
            return True
        return False

    @property
    def auxiliary_climatisation_last_updated(self) -> datetime:
        """Return status of auxiliary climatisation last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.carCapturedTimestamp")

    @property
    def is_auxiliary_climatisation_supported(self) -> bool:
        """Return true if vehicle has auxiliary climatisation."""
        # return (
        #    is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.heaterSource")
        #    or is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationSettings.value.climatizationAtUnlock")
        # )
        # CURRENTLY NOT SUPPORTED
        return False

    @property
    def is_climatisation_supported(self) -> bool:
        """Return true if climatisation has State."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.climatisationState")

    @property
    def is_climatisation_supported_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.climatisationStatus.value.carCapturedTimestamp")

    @property
    def window_heater_front(self) -> bool:
        """Return status of front window heater."""
        window_heating_status = find_path(
            self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.windowHeatingStatus"
        )
        for window_heating_state in window_heating_status:
            if window_heating_state["windowLocation"] == "front":
                return window_heating_state["windowHeatingState"] == "on"

        return False

    @property
    def window_heater_front_last_updated(self) -> datetime:
        """Return front window heater last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.carCapturedTimestamp")

    @property
    def is_window_heater_front_supported(self) -> bool:
        """Return true if vehicle has heater."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.windowHeatingStatus")

    @property
    def window_heater_back(self) -> bool:
        """Return status of rear window heater."""
        window_heating_status = find_path(
            self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.windowHeatingStatus"
        )
        for window_heating_state in window_heating_status:
            if window_heating_state["windowLocation"] == "rear":
                return window_heating_state["windowHeatingState"] == "on"

        return False

    @property
    def window_heater_back_last_updated(self) -> datetime:
        """Return front window heater last updated."""
        return find_path(self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.carCapturedTimestamp")

    @property
    def is_window_heater_back_supported(self) -> bool:
        """Return true if vehicle has heater."""
        return is_valid_path(self.attrs, f"{Services.CLIMATISATION}.windowHeatingStatus.value.windowHeatingStatus")

    @property
    def window_heater(self) -> bool:
        """Return status of window heater."""
        return self.window_heater_front or self.window_heater_back

    @property
    def window_heater_last_updated(self) -> datetime:
        """Return front window heater last updated."""
        return self.window_heater_front_last_updated

    @property
    def is_window_heater_supported(self) -> bool:
        """Return true if vehicle has heater."""
        # ID models detection
        if self._services.get(Services.PARAMETERS, {}).get("supportsStartWindowHeating", "false") == "true":
            return True
        # "Legacy" models detection
        parameters = self._services.get(Services.CLIMATISATION, {}).get("parameters", None)
        if parameters:
            for parameter in parameters:
                if parameter["key"] == "supportsStartWindowHeating" and parameter["value"] == "true":
                    return True
        return False

    # Parking heater, "legacy" auxiliary climatisation
    @property
    def pheater_duration(self) -> int:
        """
        Return heating duration for legacy aux heater.

        :return:
        """
        return self._climate_duration

    @pheater_duration.setter
    def pheater_duration(self, value) -> None:
        if value in [10, 20, 30, 40, 50, 60]:
            self._climate_duration = value
        else:
            _LOGGER.warning(f"Invalid value for duration: {value}")

    @property
    def is_pheater_duration_supported(self) -> bool:
        """
        Return true if legacy aux heater is supported.

        :return:
        """
        return self.is_pheater_heating_supported

    @property
    def pheater_ventilation(self) -> bool:
        """Return status of combustion climatisation."""
        return (
            self.attrs.get("heating", {}).get("climatisationStateReport", {}).get("climatisationState", False)
            == "ventilation"
        )

    @property
    def pheater_ventilation_last_updated(self) -> datetime:
        """Return status of combustion climatisation."""
        return (
            self.attrs.get("heating", {}).get("climatisationStateReport", {}).get("climatisationState", False)
            == "ventilation"
        )

    @property
    def is_pheater_ventilation_supported(self) -> bool:
        """Return true if vehicle has combustion climatisation."""
        return self.is_pheater_heating_supported

    @property
    def pheater_heating(self) -> bool:
        """Return status of combustion engine heating."""
        return (
            self.attrs.get("heating", {}).get("climatisationStateReport", {}).get("climatisationState", False)
            == "heating"
        )

    @property
    def pheater_heating_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self.attrs.get("heating", {}).get("climatisationStateReport", {}).get("FIXME")

    @property
    def is_pheater_heating_supported(self) -> bool:
        """Return true if vehicle has combustion engine heating."""
        return self.attrs.get("heating", {}).get("climatisationStateReport", {}).get("climatisationState", False)

    @property
    def pheater_status(self) -> str:
        """Return status of combustion engine heating/ventilation."""
        return self.attrs.get("heating", {}).get("climatisationStateReport", {}).get("climatisationState", "Unknown")

    @property
    def pheater_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self.attrs.get("heating", {}).get("climatisationStateReport", {}).get("FIXME")

    @property
    def is_pheater_status_supported(self) -> bool:
        """Return true if vehicle has combustion engine heating/ventilation."""
        return self.attrs.get("heating", {}).get("climatisationStateReport", {}).get("climatisationState", False)

    # Windows
    @property
    def windows_closed(self) -> bool:
        """
        Return true if all supported windows are closed.

        :return:
        """
        return (
            (not self.is_window_closed_left_front_supported or self.window_closed_left_front)
            and (not self.is_window_closed_left_back_supported or self.window_closed_left_back)
            and (not self.is_window_closed_right_front_supported or self.window_closed_right_front)
            and (not self.is_window_closed_right_back_supported or self.window_closed_right_back)
        )

    @property
    def windows_closed_last_updated(self) -> datetime:
        """Return timestamp for windows state last updated."""
        return self.window_closed_left_front_last_updated

    @property
    def is_windows_closed_supported(self) -> bool:
        """Return true if window state is supported."""
        return (
            self.is_window_closed_left_front_supported
            or self.is_window_closed_left_back_supported
            or self.is_window_closed_right_front_supported
            or self.is_window_closed_right_back_supported
        )

    @property
    def window_closed_left_front(self) -> bool:
        """
        Return left front window closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "frontLeft":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def window_closed_left_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_window_closed_left_front_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "frontLeft" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def window_closed_right_front(self) -> bool:
        """
        Return right front window closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "frontRight":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def window_closed_right_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_window_closed_right_front_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "frontRight" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def window_closed_left_back(self) -> bool:
        """
        Return left back window closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "rearLeft":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def window_closed_left_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_window_closed_left_back_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "rearLeft" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def window_closed_right_back(self) -> bool:
        """
        Return right back window closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "rearRight":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def window_closed_right_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_window_closed_right_back_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "rearRight" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def sunroof_closed(self) -> bool:
        """
        Return sunroof closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "sunRoof":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def sunroof_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_sunroof_closed_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "sunRoof" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def sunroof_rear_closed(self) -> bool:
        """
        Return sunroof rear closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "sunRoofRear":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def sunroof_rear_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_sunroof_rear_closed_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "sunRoofRear" and "unsupported" not in window["status"]:
                    return True
        return False

    @property
    def roof_cover_closed(self) -> bool:
        """
        Return roof cover closed state.

        :return:
        """
        windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
        for window in windows:
            if window["name"] == "roofCover":
                if not any(valid_status in window["status"] for valid_status in P.VALID_WINDOW_STATUS):
                    return None
                return "closed" in window["status"]
        return False

    @property
    def roof_cover_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_roof_cover_closed_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            windows = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.windows")
            for window in windows:
                if window["name"] == "roofCover" and "unsupported" not in window["status"]:
                    return True
        return False

    # Locks
    @property
    def door_locked_sensor(self) -> bool:
        """Return same state as lock entity, since they are mutually exclusive."""
        return self.door_locked

    @property
    def door_locked(self) -> bool:
        """
        Return true if all doors are locked.

        :return:
        """
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doorLockStatus") == "locked"

    @property
    def door_locked_last_updated(self) -> datetime:
        """Return door lock last updated."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def door_locked_sensor_last_updated(self) -> datetime:
        """Return door lock last updated."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_door_locked_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        # First check that the service is actually enabled
        if not self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        return is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doorLockStatus")

    @property
    def is_door_locked_sensor_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        # Use real lock if the service is actually enabled
        if self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        return is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doorLockStatus")

    @property
    def trunk_locked(self) -> bool:
        """
        Return trunk locked state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "trunk":
                return "locked" in door["status"]
        return False

    @property
    def trunk_locked_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_trunk_locked_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        if not self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "trunk" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def trunk_locked_sensor(self) -> bool:
        """
        Return trunk locked state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "trunk":
                return "locked" in door["status"]
        return False

    @property
    def trunk_locked_sensor_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_trunk_locked_sensor_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        if self._services.get(Services.ACCESS, {}).get("active", False):
            return False
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "trunk" and "unsupported" not in door["status"]:
                    return True
        return False

    # Doors, hood and trunk
    @property
    def hood_closed(self) -> bool:
        """
        Return hood closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "bonnet":
                if not any(valid_status in door["status"] for valid_status in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in door["status"]
        return False

    @property
    def hood_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_hood_closed_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "bonnet" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def door_closed_left_front(self) -> bool:
        """
        Return left front door closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "frontLeft":
                if not any(valid_status in door["status"] for valid_status in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in door["status"]
        return False

    @property
    def door_closed_left_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_door_closed_left_front_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "frontLeft" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def door_closed_right_front(self) -> bool:
        """
        Return right front door closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "frontRight":
                if not any(valid_status in door["status"] for valid_status in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in door["status"]
        return False

    @property
    def door_closed_right_front_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_door_closed_right_front_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "frontRight" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def door_closed_left_back(self) -> bool:
        """
        Return left back door closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "rearLeft":
                if not any(valid_status in door["status"] for valid_status in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in door["status"]
        return False

    @property
    def door_closed_left_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_door_closed_left_back_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "rearLeft" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def door_closed_right_back(self) -> bool:
        """
        Return right back door closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "rearRight":
                if not any(valid_status in door["status"] for valid_status in P.VALID_DOOR_STATUS):
                    return None
                return "closed" in door["status"]
        return False

    @property
    def door_closed_right_back_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_door_closed_right_back_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "rearRight" and "unsupported" not in door["status"]:
                    return True
        return False

    @property
    def trunk_closed(self) -> bool:
        """
        Return trunk closed state.

        :return:
        """
        doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
        for door in doors:
            if door["name"] == "trunk":
                return "closed" in door["status"]
        return False

    @property
    def trunk_closed_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.carCapturedTimestamp")

    @property
    def is_trunk_closed_supported(self) -> bool:
        """Return true if supported."""
        if is_valid_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors"):
            doors = find_path(self.attrs, f"{Services.ACCESS}.accessStatus.value.doors")
            for door in doors:
                if door["name"] == "trunk" and "unsupported" not in door["status"]:
                    return True
        return False

    # Departure timers
    @property
    def departure_timer1(self):
        """
        Return schedule #1.

        :return:
        """
        return self.schedule(1)

    @property
    def departure_timer2(self):
        """
        Return schedule #2.

        :return:
        """
        return self.schedule(2)

    @property
    def departure_timer3(self):
        """
        Return schedule #3.

        :return:
        """
        return self.schedule(3)

    @property
    def departure_timer1_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return self.schedule(1).timestamp

    @property
    def departure_timer2_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return self.schedule(2).timestamp

    @property
    def departure_timer3_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return self.schedule(3).timestamp

    def schedule(self, schedule_id: str | int) -> Timer:
        """
        Return schedule #1.

        :return:
        """
        timer: TimerData = self.attrs.get("timer", None)
        return timer.get_schedule(schedule_id)

    @property
    def schedule_min_charge_level(self) -> int | None:
        """Get charge minimum level."""
        timer: TimerData = self.attrs.get("timer")
        return (
            timer.timersAndProfiles.timerBasicSetting.chargeMinLimit
            if timer.timersAndProfiles.timerBasicSetting
            else None
        )

    @property
    def schedule_min_charge_level_last_updated(self) -> datetime | None:
        """Return attribute last updated timestamp."""
        timer: TimerData = self.attrs.get("timer")
        return (
            timer.timersAndProfiles.timerBasicSetting.timestamp if timer.timersAndProfiles.timerBasicSetting else None
        )

    @property
    def is_schedule_min_charge_level_supported(self) -> bool:
        """Check if charge minimum level is supported."""
        timer: TimerData = self.attrs.get("timer", None)
        return (
            timer.timersAndProfiles.timerBasicSetting is not None
            and timer.timersAndProfiles.timerBasicSetting.chargeMinLimit is not None
        )

    @property
    def schedule_heater_source(self) -> str | None:
        """Get departure schedule heater source."""
        timer: TimerData = self.attrs.get("timer")
        return (
            timer.timersAndProfiles.timerBasicSetting.heaterSource
            if timer.timersAndProfiles.timerBasicSetting
            else None
        )

    @property
    def schedule_heater_source_last_updated(self) -> datetime | None:
        """Return attribute last updated timestamp."""
        timer: TimerData = self.attrs.get("timer")
        return (
            timer.timersAndProfiles.timerBasicSetting.timestamp if timer.timersAndProfiles.timerBasicSetting else None
        )

    @property
    def is_schedule_heater_source_supported(self) -> bool:
        """Check if departure timers heater source is supported."""
        timer: TimerData = self.attrs.get("timer", None)
        return (
            (timer.timersAndProfiles.timerBasicSetting.heaterSource is not None)
            if timer.timersAndProfiles.timerBasicSetting
            else False
        )

    @property
    def timer_basic_settings(self) -> BasicSettings | None:
        """Check if timer basic settings are supported."""
        timer: TimerData = self.attrs.get("timer")
        return timer.timersAndProfiles.timerBasicSetting

    @property
    def is_timer_basic_settings_supported(self) -> bool:
        """Check if timer basic settings are supported."""
        timer: TimerData = self.attrs.get("timer", None)
        return (
            timer is not None
            and timer.timersAndProfiles is not None
            and timer.timersAndProfiles.timerBasicSetting is not None
        )

    @property
    def is_departure_timer1_supported(self) -> bool:
        """Check if timer 1 is supported."""
        # return self.is_schedule_supported(1)
        # CURRENTLY NOT SUPPORTED
        return False

    @property
    def is_departure_timer2_supported(self) -> bool:
        """Check if timer 2is supported."""
        # return self.is_schedule_supported(2)
        # CURRENTLY NOT SUPPORTED
        return False

    @property
    def is_departure_timer3_supported(self) -> bool:
        """Check if timer 3 is supported."""
        # return self.is_schedule_supported(3)
        # CURRENTLY NOT SUPPORTED
        return False

    def is_schedule_supported(self, id: str | int) -> bool:
        """
        Return true if schedule is supported.

        :return:
        """
        # timer: TimerData = self.attrs.get("timer", None)
        # return timer.has_schedule(id)
        # CURRENTLY NOT SUPPORTED
        return False

    # Trip data
    @property
    def trip_last_entry(self):
        """
        Return last trip data entry.

        :return:
        """
        return self.attrs.get(Services.TRIP_LAST, {})

    @property
    def trip_last_average_speed(self):
        """
        Return last trip average speed.

        :return:
        """
        return find_path(self.attrs, f"{Services.TRIP_LAST}.averageSpeed_kmph")

    @property
    def trip_last_average_speed_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_speed_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.averageSpeed_kmph") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.averageSpeed_kmph")
        ) in (float, int)

    @property
    def trip_last_average_electric_engine_consumption(self):
        """
        Return last trip average electric consumption.

        :return:
        """
        return float(find_path(self.attrs, f"{Services.TRIP_LAST}.averageElectricConsumption"))

    @property
    def trip_last_average_electric_engine_consumption_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_electric_engine_consumption_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.averageElectricConsumption") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.averageElectricConsumption")
        ) in (float, int)

    @property
    def trip_last_average_fuel_consumption(self):
        """
        Return last trip average fuel consumption.

        :return:
        """
        return float(find_path(self.attrs, f"{Services.TRIP_LAST}.averageFuelConsumption"))

    @property
    def trip_last_average_fuel_consumption_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_fuel_consumption_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.averageFuelConsumption") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.averageFuelConsumption")
        ) in (float, int)

    @property
    def trip_last_average_auxillary_consumption(self):
        """
        Return last trip average auxiliary consumption.

        :return:
        """
        # no example verified yet
        return self.trip_last_entry.get("averageAuxiliaryConsumption")

    @property
    def trip_last_average_auxillary_consumption_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_auxillary_consumption_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.averageAuxiliaryConsumption") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.averageAuxiliaryConsumption")
        ) in (float, int)

    @property
    def trip_last_average_aux_consumer_consumption(self):
        """
        Return last trip average auxiliary consumer consumption.

        :return:
        """
        # no example verified yet
        return self.trip_last_entry.get("averageAuxConsumerConsumption")

    @property
    def trip_last_average_aux_consumer_consumption_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_aux_consumer_consumption_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.averageAuxConsumerConsumption") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.averageAuxConsumerConsumption")
        ) in (float, int)

    @property
    def trip_last_duration(self):
        """
        Return last trip duration in minutes(?).

        :return:
        """
        return find_path(self.attrs, f"{Services.TRIP_LAST}.travelTime")

    @property
    def trip_last_duration_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_duration_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.travelTime") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.travelTime")
        ) in (float, int)

    @property
    def trip_last_length(self):
        """
        Return last trip length.

        :return:
        """
        return find_path(self.attrs, f"{Services.TRIP_LAST}.mileage_km")

    @property
    def trip_last_length_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_length_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        return is_valid_path(self.attrs, f"{Services.TRIP_LAST}.mileage_km") and type(
            find_path(self.attrs, f"{Services.TRIP_LAST}.mileage_km")
        ) in (float, int)

    @property
    def trip_last_recuperation(self):
        """
        Return last trip recuperation.

        :return:
        """
        # Not implemented
        return self.trip_last_entry.get("recuperation")

    @property
    def trip_last_recuperation_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_recuperation_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        # Not implemented
        response = self.trip_last_entry
        return response and type(response.get("recuperation", None)) in (float, int)

    @property
    def trip_last_average_recuperation(self):
        """
        Return last trip total recuperation.

        :return:
        """
        return self.trip_last_entry.get("averageRecuperation")

    @property
    def trip_last_average_recuperation_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_average_recuperation_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        response = self.trip_last_entry
        return response and type(response.get("averageRecuperation", None)) in (float, int)

    @property
    def trip_last_total_electric_consumption(self):
        """
        Return last trip total electric consumption.

        :return:
        """
        # Not implemented
        return self.trip_last_entry.get("totalElectricConsumption")

    @property
    def trip_last_total_electric_consumption_last_updated(self) -> datetime:
        """Return last updated timestamp."""
        return find_path(self.attrs, f"{Services.TRIP_LAST}.tripEndTimestamp")

    @property
    def is_trip_last_total_electric_consumption_supported(self) -> bool:
        """
        Return true if supported.

        :return:
        """
        # Not implemented
        response = self.trip_last_entry
        return response and type(response.get("totalElectricConsumption", None)) in (float, int)

    # Status of set data requests
    @property
    def refresh_action_status(self):
        """Return latest status of data refresh request."""
        return self._requests.get("refresh", {}).get("status", "None")

    @property
    def charger_action_status(self):
        """Return latest status of charger request."""
        return self._requests.get("batterycharge", {}).get("status", "None")

    @property
    def climater_action_status(self):
        """Return latest status of climater request."""
        return self._requests.get("climatisation", {}).get("status", "None")

    @property
    def pheater_action_status(self):
        """Return latest status of parking heater request."""
        return self._requests.get("preheater", {}).get("status", "None")

    @property
    def lock_action_status(self):
        """Return latest status of lock action request."""
        return self._requests.get("lock", {}).get("status", "None")

    # Requests data
    @property
    def refresh_data(self):
        """Get state of data refresh."""
        return self._requests.get("refresh", {}).get("id", False)

    @property
    def refresh_data_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return self._requests.get("refresh", {}).get("timestamp")

    @property
    def is_refresh_data_supported(self) -> bool:
        """Return true, as data refresh is always supported."""
        return True

    @property
    def request_in_progress(self) -> bool:
        """Check of any requests are currently in progress."""
        try:
            for section in self._requests:
                return self._requests[section].get("id", False)
        except Exception as e:
            _LOGGER.warning(e)
        return False

    @property
    def request_in_progress_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        try:
            for section in self._requests:
                return self._requests[section].get("timestamp")
        except Exception as e:
            _LOGGER.warning(e)
        return datetime.now(timezone.utc)

    @property
    def is_request_in_progress_supported(self):
        """Request in progress is always supported."""
        return True

    @property
    def request_results(self) -> dict:
        """Get last request result."""
        data = {"latest": self._requests.get("latest", None), "state": self._requests.get("state", None)}
        for section in self._requests:
            if section in ["departuretimer", "batterycharge", "climatisation", "refresh", "lock", "preheater"]:
                data[section] = self._requests[section].get("status", "Unknown")
        return data

    @property
    def request_results_last_updated(self) -> datetime | None:
        """Get last updated time."""
        if self._requests.get("latest", "") != "":
            return self._requests.get(str(self._requests.get("latest")), {}).get("timestamp")
        # all requests should have more or less the same timestamp anyway, so
        # just return the first one
        for section in ["departuretimer", "batterycharge", "climatisation", "refresh", "lock", "preheater"]:
            if section in self._requests:
                return self._requests[section].get("timestamp")
        return None

    @property
    def is_request_results_supported(self):
        """Request results is supported if in progress is supported."""
        return self.is_request_in_progress_supported

    @property
    def requests_results_last_updated(self):
        """Return last updated timestamp for attribute."""
        return None

    @property
    def requests_remaining(self):
        """Get remaining requests before throttled."""
        if self.attrs.get("rate_limit_remaining", False):
            self.requests_remaining = self.attrs.get("rate_limit_remaining")
            self.attrs.pop("rate_limit_remaining")
        return self._requests["remaining"]

    @requests_remaining.setter
    def requests_remaining(self, value):
        self._requests["remaining"] = value
        self.requests_remaining_last_updated = datetime.utcnow()

    @property
    def requests_remaining_last_updated(self) -> datetime:
        """Get last updated timestamp."""
        return self._requests["remaining_updated"] if "remaining_updated" in self._requests else None

    @requests_remaining_last_updated.setter
    def requests_remaining_last_updated(self, value):
        self._requests["remaining_updated"] = value

    @property
    def is_requests_remaining_supported(self):
        """
        Return true if requests remaining is supported.

        :return:
        """
        return True if self._requests.get("remaining", False) else False

    # Helper functions #
    def __str__(self):
        """Return the vin."""
        return self.vin

    @property
    def json(self):
        """
        Return vehicle data in JSON format.

        :return:
        """

        def serialize(obj):
            """
            Convert datetime instances back to JSON compatible format.

            :param obj:
            :return:
            """
            return obj.isoformat() if isinstance(obj, datetime) else obj

        return to_json(OrderedDict(sorted(self.attrs.items())), indent=4, default=serialize)

    def is_primary_drive_electric(self):
        """Check if primary engine is electric."""
        return (
            find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType")
            == ENGINE_TYPE_ELECTRIC
        )

    def is_secondary_drive_electric(self):
        """Check if secondary engine is electric."""
        return (
            is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType")
            and find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType")
            == ENGINE_TYPE_ELECTRIC
        )

    def is_primary_drive_combustion(self):
        """Check if primary engine is combustion."""
        engine_type = ""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.type"):
            engine_type = find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.primaryEngine.type")

        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType"):
            engine_type = find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.primaryEngineType")

        return engine_type in ENGINE_TYPE_COMBUSTION

    def is_secondary_drive_combustion(self):
        """Check if secondary engine is combustion."""
        engine_type = ""
        if is_valid_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.secondaryEngine.type"):
            engine_type = find_path(self.attrs, f"{Services.FUEL_STATUS}.rangeStatus.value.secondaryEngine.type")

        if is_valid_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.secondaryEngineType"):
            engine_type = find_path(self.attrs, f"{Services.MEASUREMENTS}.fuelLevelStatus.value.secondaryEngineType")

        return engine_type in ENGINE_TYPE_COMBUSTION

    def has_combustion_engine(self):
        """Return true if car has a combustion engine."""
        return self.is_primary_drive_combustion() or self.is_secondary_drive_combustion()

    @property
    def api_vehicles_status(self) -> bool:
        """Check vehicles API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("vehicles", "Unknown")

    @property
    def api_vehicles_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_vehicles_status_supported(self):
        """Vehicles API status is always supported."""
        return True

    @property
    def api_capabilities_status(self) -> bool:
        """Check capabilities API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("capabilities", "Unknown")

    @property
    def api_capabilities_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_capabilities_status_supported(self):
        """Capabilities API status is always supported."""
        return True

    @property
    def api_trips_status(self) -> bool:
        """Check trips API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("trips", "Unknown")

    @property
    def api_trips_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_trips_status_supported(self):
        """Check if Trips API status is supported."""
        if self._services.get(Services.TRIP_STATISTICS, {}).get("active", False):
            return True
        return False

    @property
    def api_selectivestatus_status(self) -> bool:
        """Check selectivestatus API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("selectivestatus", "Unknown")

    @property
    def api_selectivestatus_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_selectivestatus_status_supported(self):
        """Selectivestatus API status is always supported."""
        return True

    @property
    def api_parkingposition_status(self) -> bool:
        """Check parkingposition API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("parkingposition", "Unknown")

    @property
    def api_parkingposition_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_parkingposition_status_supported(self):
        """Check if Parkingposition API status is supported."""
        if self._services.get(Services.PARKING_POSITION, {}).get("active", False):
            return True
        return False

    @property
    def api_token_status(self) -> bool:
        """Check token API status."""
        return self.attrs.get(Services.SERVICE_STATUS, {}).get("token", "Unknown")

    @property
    def api_token_status_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_api_token_status_supported(self):
        """Parkingposition API status is always supported."""
        return True

    @property
    def last_data_refresh(self) -> datetime:
        """Check when services were refreshed successfully for the last time."""
        last_data_refresh_path = "refreshTimestamp"
        if is_valid_path(self.attrs, last_data_refresh_path):
            return find_path(self.attrs, last_data_refresh_path)
        return None

    @property
    def last_data_refresh_last_updated(self) -> datetime:
        """Return attribute last updated timestamp."""
        return datetime.now(timezone.utc)

    @property
    def is_last_data_refresh_supported(self):
        """Last data refresh is always supported."""
        return True
