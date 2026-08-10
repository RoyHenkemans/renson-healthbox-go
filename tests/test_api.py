"""Tests for protocol mappings."""

from unittest.mock import AsyncMock

from renson_healthbox_go import HealthboxGoApi, parameter_value


async def test_write_mappings() -> None:
    api = HealthboxGoApi("192.0.2.1", AsyncMock())
    api.put = AsyncMock()
    await api.set_normal_level(30, 60)
    api.put.assert_awaited_with("/v1/decision/room", {"minimum": 18.0})
    await api.set_co2_threshold(950)
    api.put.assert_awaited_with(
        "/v1/decision/room/demand",
        {"CO2": {"static": {"minimum": 700.0, "maximum": 950.0}}},
    )


def test_parameter_value() -> None:
    data = {"sensor": {"3": {"parameter": {"concentration": {"value": 567}}}}}
    assert parameter_value(data, "concentration") == 567

