from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentscope_api.schemas.automatic_drift import (
    AutomaticDriftCheckListParams,
    AutomaticDriftConfigurationCreate,
    AutomaticDriftConfigurationListParams,
    MonitoringIncidentEventListParams,
    MonitoringIncidentListParams,
)


def test_configuration_defaults_and_bounds() -> None:
    payload = AutomaticDriftConfigurationCreate(
        name="Hourly checkout drift",
        monitoring_definition_id="00000000-0000-0000-0000-000000000001",
        drift_policy_id="00000000-0000-0000-0000-000000000002",
    )
    assert payload.baseline_strategy == "previous_window"
    assert payload.cooldown_seconds == 3_600
    assert payload.resolve_after_clean_windows == 2

    for field, value in (
        ("cooldown_seconds", -1),
        ("cooldown_seconds", 604_801),
        ("resolve_after_clean_windows", 0),
        ("resolve_after_clean_windows", 21),
    ):
        with pytest.raises(ValidationError):
            AutomaticDriftConfigurationCreate(
                name="invalid",
                monitoring_definition_id="00000000-0000-0000-0000-000000000001",
                drift_policy_id="00000000-0000-0000-0000-000000000002",
                **{field: value},
            )


@pytest.mark.parametrize(
    "model",
    (
        AutomaticDriftConfigurationListParams,
        AutomaticDriftCheckListParams,
        MonitoringIncidentListParams,
        MonitoringIncidentEventListParams,
    ),
)
def test_list_contracts_are_bounded(model: type[object]) -> None:
    with pytest.raises(ValidationError):
        model(page_size=101)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        model(offset=100_001)  # type: ignore[call-arg]
