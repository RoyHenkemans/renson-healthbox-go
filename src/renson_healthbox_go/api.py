"""Async client for the local Renson Healthbox Go HTTP API."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import REQUEST_TIMEOUT, SCHEDULE_DAYS


class HealthboxGoError(Exception):
    """Base API error."""


class HealthboxGoConnectionError(HealthboxGoError):
    """The Healthbox could not be reached."""


class HealthboxGoInvalidResponse(HealthboxGoError):
    """The Healthbox returned an unexpected response."""


@dataclass(slots=True)
class HealthboxGoInfo:
    """Identity returned by the Healthbox."""

    serial: str
    name: str
    model: str = "Healthbox Go"
    firmware: str | None = None
    pcb_version: str | None = None


def _walk(value: Any):
    """Yield all nested dictionaries."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def find_first(data: Any, *keys: str) -> Any:
    """Find the first non-empty value for a set of possible keys."""
    lowered = {key.lower() for key in keys}
    for mapping in _walk(data):
        for key, value in mapping.items():
            if key.lower() in lowered and value not in (None, ""):
                if isinstance(value, dict) and "value" in value:
                    return value["value"]
                return value
    return None


def parameter_value(data: dict[str, Any], parameter: str) -> Any:
    """Find a parameter value in constellation actuator/sensor records."""
    for collection_name in ("actuator", "sensor"):
        collection = data.get(collection_name, {})
        records = collection.values() if isinstance(collection, dict) else collection
        if not isinstance(records, Iterable):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            parameters = record.get("parameter", {})
            if parameter in parameters:
                value = parameters[parameter]
                return value.get("value") if isinstance(value, dict) else value
    return None


class HealthboxGoApi:
    """Small client for the unauthenticated local API."""

    def __init__(self, host: str, session: ClientSession) -> None:
        self.host = host.strip().rstrip("/")
        if self.host.startswith(("http://", "https://")):
            self.base_url = self.host
        else:
            self.base_url = f"http://{self.host}"
        self._session = session

    async def _request(
        self, method: str, path: str, payload: Any | None = None
    ) -> Any:
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                response = await self._session.request(
                    method,
                    f"{self.base_url}{path}",
                    json=payload,
                    headers={"Accept": "application/json", "X-API-Key": ""},
                    timeout=ClientTimeout(total=REQUEST_TIMEOUT),
                )
                if response.status >= 400:
                    body = await response.text()
                    raise HealthboxGoInvalidResponse(
                        f"{method} {path} returned HTTP {response.status}: {body[:200]}"
                    )
                if response.content_length == 0:
                    return None
                text = await response.text()
                if not text.strip():
                    return None
                try:
                    return await response.json(content_type=None)
                except (ValueError, TypeError) as err:
                    raise HealthboxGoInvalidResponse(
                        f"{method} {path} did not return JSON"
                    ) from err
        except HealthboxGoInvalidResponse:
            raise
        except (TimeoutError, ClientError, OSError) as err:
            raise HealthboxGoConnectionError(str(err)) from err

    async def get(self, path: str) -> dict[str, Any] | list[Any]:
        result = await self._request("GET", path)
        if not isinstance(result, (dict, list)):
            raise HealthboxGoInvalidResponse(f"GET {path} returned no JSON object or array")
        return result

    async def put(self, path: str, payload: Any) -> None:
        await self._request("PUT", path, payload)

    async def async_get_info(self) -> HealthboxGoInfo:
        data = await self.get("/v1/constellation/global")
        serial = find_first(data, "serial", "serial_number", "serialnumber")
        if serial is None:
            raise HealthboxGoInvalidResponse("No serial number in constellation/global")
        device_type = str(find_first(data, "device_type", "product_type", "type") or "")
        model = "Healthbox Go" if "HEALTHBOX_GO" in device_type.upper() else device_type
        return HealthboxGoInfo(
            serial=str(serial),
            name=str(find_first(data, "device_name", "name") or "Healthbox Go"),
            model=model or "Healthbox Go",
            firmware=_as_string(find_first(data, "firmware", "firmware_version", "fw_version")),
            pcb_version=_as_string(find_first(data, "pcb_version", "pcb")),
        )

    async def async_update(self) -> dict[str, Any]:
        """Read all supported endpoints; optional endpoints may be absent."""
        paths = {
            "constellation": "/v1/constellation",
            "global": "/v1/constellation/global",
            "room": "/v1/decision/room",
            "breeze": "/v1/decision/breeze",
            "silent": "/v1/decision/silent",
            "uptime": "/v1/global/uptime",
            "wifi": "/v1/wifi/client/status",
            "sensor_presets": "/v1/decision/room/sensor_presets",
        }
        results = await asyncio.gather(
            *(self.get(path) for path in paths.values()), return_exceptions=True
        )
        data: dict[str, Any] = {}
        required_ok = False
        for key, result in zip(paths, results, strict=True):
            if isinstance(result, dict) or (
                key == "sensor_presets" and isinstance(result, list)
            ):
                data[key] = result
                if key in ("constellation", "room"):
                    required_ok = True
            elif key in ("constellation", "room") and isinstance(
                result, HealthboxGoConnectionError
            ):
                raise result
        if not required_ok:
            raise HealthboxGoInvalidResponse("No usable data returned by the Healthbox")
        return data

    async def set_profile(self, profile: int) -> None:
        await self.put("/v1/decision/room", {"profile": profile})

    async def set_normal_level(self, percentage: float, nominal: float = 60.0) -> None:
        # `nominal` is the room's calibrated base specification. The Sense UI
        # expresses `minimum` as a percentage of that calibrated value.
        await self.put(
            "/v1/decision/room",
            {"minimum": round(percentage * nominal / 100.0, 1)},
        )

    async def set_manual_override(self, percentage: float, duration: int) -> None:
        await self.put(
            "/v1/decision/room/boost",
            {
                "enable": True,
                "level": float(percentage),
                "timeout": int(duration),
                "remaining": 0,
            },
        )

    async def stop_manual_override(self) -> None:
        await self.put(
            "/v1/decision/room/boost",
            {"enable": False, "level": 0.0, "timeout": 0, "remaining": 0},
        )

    async def set_co2_threshold(self, ppm: int) -> None:
        await self.put(
            "/v1/decision/room/demand",
            {"CO2": {"static": {"minimum": float(ppm - 250), "maximum": float(ppm)}}},
        )

    async def set_rh_sensitivity(self, sensitivity: int) -> None:
        await self.put(
            "/v1/decision/room/sensor_presets",
            [{"sensor_type": "rh", "sensitivity": sensitivity}],
        )

    async def set_breeze(
        self, *, enable: bool | None = None, threshold: float | None = None
    ) -> None:
        payload: dict[str, Any] = {}
        if enable is not None:
            payload["enable"] = enable
        if threshold is not None:
            payload["temp_threshold"] = float(threshold)
        await self.put("/v1/decision/breeze", payload)

    async def set_silent(
        self,
        current: dict[str, Any] | None,
        *,
        enable: bool | None = None,
        reduction: float | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> None:
        payload = deepcopy(current or {})
        if enable is not None:
            payload["enable"] = enable
        if reduction is not None:
            payload["reduction"] = float(reduction)
        for day in SCHEDULE_DAYS:
            schedule = payload.get(day)
            if not isinstance(schedule, list) or len(schedule) < 2:
                schedule = [
                    {"silent": True, "time": start or "22:00"},
                    {"silent": False, "time": end or "08:00"},
                ]
                payload[day] = schedule
            else:
                if start is not None:
                    schedule[0] = {"silent": True, "time": start}
                if end is not None:
                    schedule[1] = {"silent": False, "time": end}
        await self.put("/v1/decision/silent", payload)


def _as_string(value: Any) -> str | None:
    return None if value in (None, "") else str(value)
