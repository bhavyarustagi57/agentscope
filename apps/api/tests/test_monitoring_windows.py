from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from agentscope_api.schemas.monitoring import MonitoringDefinitionCreate, WindowDuration
from agentscope_api.services.monitoring import aligned_window, validate_closed_window


@pytest.mark.parametrize(
    ("duration", "expected_start", "expected_end"),
    [
        (WindowDuration.FIVE_MINUTES, "2026-09-25T12:30:00+00:00", "2026-09-25T12:35:00+00:00"),
        (WindowDuration.FIFTEEN_MINUTES, "2026-09-25T12:15:00+00:00", "2026-09-25T12:30:00+00:00"),
        (WindowDuration.ONE_HOUR, "2026-09-25T11:00:00+00:00", "2026-09-25T12:00:00+00:00"),
        (WindowDuration.SIX_HOURS, "2026-09-25T06:00:00+00:00", "2026-09-25T12:00:00+00:00"),
        (WindowDuration.ONE_DAY, "2026-09-24T00:00:00+00:00", "2026-09-25T00:00:00+00:00"),
    ],
)
def test_latest_window_is_utc_aligned_and_fully_closed(
    duration: WindowDuration, expected_start: str, expected_end: str
) -> None:
    start, end = aligned_window(datetime(2026, 9, 25, 12, 37, tzinfo=UTC), duration.seconds)
    assert start.isoformat() == expected_start
    assert end.isoformat() == expected_end


def test_exact_boundary_selects_the_previous_closed_window() -> None:
    start, end = aligned_window(datetime(2026, 9, 25, 12, 0, tzinfo=UTC), 3_600)
    assert (start, end) == (
        datetime(2026, 9, 25, 11, tzinfo=UTC),
        datetime(2026, 9, 25, 12, tzinfo=UTC),
    )


def test_specific_window_must_be_aligned_and_closed() -> None:
    now = datetime(2026, 9, 25, 12, 37, tzinfo=UTC)
    validate_closed_window(
        datetime(2026, 9, 25, 11, tzinfo=UTC),
        datetime(2026, 9, 25, 12, tzinfo=UTC),
        3_600,
        now,
    )
    with pytest.raises(ValueError, match="aligned"):
        validate_closed_window(
            datetime(2026, 9, 25, 11, 1, tzinfo=UTC),
            datetime(2026, 9, 25, 12, 1, tzinfo=UTC),
            3_600,
            now,
        )
    with pytest.raises(ValueError, match="fully closed"):
        validate_closed_window(
            datetime(2026, 9, 25, 12, tzinfo=UTC),
            datetime(2026, 9, 25, 13, tzinfo=UTC),
            3_600,
            now,
        )


def test_definition_contract_rejects_arbitrary_duration_and_unsupported_scope() -> None:
    with pytest.raises(ValidationError):
        MonitoringDefinitionCreate(name="bad", window_duration="10m")
    with pytest.raises(ValidationError):
        MonitoringDefinitionCreate(
            name="bad", window_duration="5m", trace_scope={"metadata": {"tenant": "x"}}
        )


def test_definition_contract_accepts_supported_trace_scope() -> None:
    definition = MonitoringDefinitionCreate(
        name="production reliability",
        window_duration="15m",
        trace_scope={"trace_name": "checkout", "trace_status": "error"},
    )
    assert definition.window_duration.seconds == int(timedelta(minutes=15).total_seconds())
    assert definition.trace_scope.trace_name == "checkout"
