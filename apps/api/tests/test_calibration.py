from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from test_trace_ingestion import envelope, sdk_trace_payload

from agentscope_api.database import SessionLocal
from agentscope_api.main import create_app
from agentscope_api.models.calibration import (
    CalibrationStudyRecord,
    HumanAnnotationRecord,
    HumanReferenceSetRecord,
)
from agentscope_api.models.trace import TraceRecord
from agentscope_api.schemas.calibration import (
    CalibrationStudy,
    CalibrationStudyCreate,
    HumanAnnotationWrite,
    ReferenceSubjectsDefine,
)
from agentscope_api.services.calibration import (
    ReferenceSetConflict,
    create_study,
    define_subjects,
    freeze_reference_set,
    write_annotation,
)

pytestmark = pytest.mark.usefixtures("clean_database")


def _traces(client: TestClient, count: int = 2) -> list[dict[str, object]]:
    traces = [sdk_trace_payload(f"calibration trace {index}") for index in range(count)]
    for trace in traces:
        response = client.post("/api/v1/traces", json=envelope(trace))
        assert response.status_code == 202, response.text
    return traces


def _reference_set(client: TestClient, trace_ids: list[str]) -> dict[str, object]:
    created = client.post(
        "/api/v1/human-reference-sets",
        json={"name": "Support quality reference", "description": "Human review batch"},
    )
    assert created.status_code == 201, created.text
    reference_set = created.json()
    defined = client.post(
        f"/api/v1/human-reference-sets/{reference_set['id']}/subjects",
        json={"trace_ids": trace_ids},
    )
    assert defined.status_code == 200, defined.text
    return defined.json()


def _begin(client: TestClient, reference_set_id: str) -> None:
    response = client.post(f"/api/v1/human-reference-sets/{reference_set_id}/begin-labeling")
    assert response.status_code == 200, response.text


def _label(
    client: TestClient,
    reference_set_id: str,
    trace_id: str,
    label: str,
    annotator: str = "lead-reviewer",
) -> None:
    response = client.post(
        f"/api/v1/human-reference-sets/{reference_set_id}/subjects/{trace_id}/reference-label",
        json={"label": label, "annotator_id": annotator, "rationale": "Adjudicated."},
    )
    assert response.status_code == 204, response.text


def test_reference_set_lifecycle_summaries_and_frozen_immutability() -> None:
    with TestClient(create_app()) as client:
        traces = _traces(client)
        trace_ids = [str(trace["trace_id"]) for trace in traces]
        reference_set = _reference_set(client, trace_ids)
        set_id = str(reference_set["id"])

        assert reference_set["subject_count"] == 2
        assert reference_set["reference_count"] == 0
        assert reference_set["unlabeled_count"] == 2
        assert reference_set["failed_count"] == 0
        incomplete = client.post(f"/api/v1/human-reference-sets/{set_id}/freeze")
        assert incomplete.status_code == 409

        _begin(client, set_id)
        first_annotation = client.post(
            f"/api/v1/human-reference-sets/{set_id}/annotations",
            json={
                "trace_id": trace_ids[0],
                "annotator_id": "reviewer-1",
                "label": "passed",
                "rationale": "Meets the rubric.",
            },
        )
        second_annotation = client.post(
            f"/api/v1/human-reference-sets/{set_id}/annotations",
            json={
                "trace_id": trace_ids[0],
                "annotator_id": "reviewer-2",
                "label": "failed",
                "rationale": "Misses a required detail.",
            },
        )
        relabel = client.post(
            f"/api/v1/human-reference-sets/{set_id}/annotations",
            json={
                "trace_id": trace_ids[0],
                "annotator_id": "reviewer-1",
                "label": "failed",
            },
        )
        assert first_annotation.status_code == second_annotation.status_code == 200
        assert relabel.status_code == 200
        assert relabel.json()["id"] == first_annotation.json()["id"]
        annotation_detail = client.get(f"/api/v1/human-annotations/{relabel.json()['id']}")
        assert annotation_detail.status_code == 200
        assert annotation_detail.json()["label"] == "failed"

        subjects = client.get(f"/api/v1/human-reference-sets/{set_id}/subjects").json()
        assert subjects["items"][0]["reference_label"] is None
        assert subjects["items"][0]["annotation_count"] == 2
        assert {
            item["label"]
            for item in client.get(f"/api/v1/human-reference-sets/{set_id}/annotations").json()[
                "items"
            ]
        } == {"failed"}

        _label(client, set_id, trace_ids[0], "passed")
        _label(client, set_id, trace_ids[1], "failed")
        frozen = client.post(f"/api/v1/human-reference-sets/{set_id}/freeze")
        assert frozen.status_code == 200
        assert frozen.json()["status"] == "frozen"
        assert frozen.json()["reference_count"] == 2
        assert frozen.json()["passed_count"] == 1
        assert frozen.json()["failed_count"] == 1
        assert frozen.json()["unlabeled_count"] == 0

        assert (
            client.post(
                f"/api/v1/human-reference-sets/{set_id}/annotations",
                json={"trace_id": trace_ids[0], "annotator_id": "late", "label": "passed"},
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/v1/human-reference-sets/{set_id}/subjects/{trace_ids[0]}/reference-label",
                json={"label": "failed", "annotator_id": "late"},
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/v1/human-reference-sets/{set_id}/subjects",
                json={"trace_ids": list(reversed(trace_ids))},
            ).status_code
            == 409
        )


def test_subject_definition_is_idempotent_but_conflicting_intent_is_rejected() -> None:
    with TestClient(create_app()) as client:
        traces = _traces(client, 3)
        trace_ids = [str(trace["trace_id"]) for trace in traces]
        reference_set = _reference_set(client, trace_ids[:2])
        set_id = str(reference_set["id"])
        repeated = client.post(
            f"/api/v1/human-reference-sets/{set_id}/subjects",
            json={"trace_ids": trace_ids[:2]},
        )
        conflict = client.post(
            f"/api/v1/human-reference-sets/{set_id}/subjects",
            json={"trace_ids": trace_ids},
        )
        missing = client.post(
            "/api/v1/human-reference-sets",
            json={"name": "Missing trace set"},
        ).json()
        missing_trace = client.post(
            f"/api/v1/human-reference-sets/{missing['id']}/subjects",
            json={"trace_ids": ["tr_missing"]},
        )

    assert repeated.status_code == 200
    assert conflict.status_code == 409
    assert missing_trace.status_code == 404


def test_concurrent_identical_subject_definitions_are_idempotent() -> None:
    with TestClient(create_app()) as client:
        trace = _traces(client, 1)[0]
        reference_set = client.post(
            "/api/v1/human-reference-sets", json={"name": "Concurrent subjects"}
        ).json()

    async def define() -> None:
        async with SessionLocal() as session:
            await define_subjects(
                session,
                UUID(str(reference_set["id"])),
                ReferenceSubjectsDefine(trace_ids=[str(trace["trace_id"])]),
            )

    async def exercise() -> None:
        await asyncio.gather(define(), define())

    asyncio.run(exercise())
    with TestClient(create_app()) as client:
        subjects = client.get(f"/api/v1/human-reference-sets/{reference_set['id']}/subjects").json()
    assert len(subjects["items"]) == 1


def test_calibration_study_requires_frozen_set_and_snapshots_ordered_labels() -> None:
    with TestClient(create_app()) as client:
        traces = _traces(client)
        trace_ids = [str(trace["trace_id"]) for trace in traces]
        reference_set = _reference_set(client, trace_ids)
        set_id = str(reference_set["id"])
        rejected = client.post(
            "/api/v1/calibration-studies",
            json={"name": "Too early", "reference_set_id": set_id},
        )
        assert rejected.status_code == 409
        _begin(client, set_id)
        _label(client, set_id, trace_ids[0], "failed")
        _label(client, set_id, trace_ids[1], "passed")
        assert client.post(f"/api/v1/human-reference-sets/{set_id}/freeze").status_code == 200

        created = client.post(
            "/api/v1/calibration-studies",
            json={"name": "Judge baseline", "reference_set_id": set_id},
        )
        repeated = client.post(
            "/api/v1/calibration-studies",
            json={"name": "Judge baseline rerun", "reference_set_id": set_id},
        )
        assert created.status_code == repeated.status_code == 201
        assert created.json()["id"] != repeated.json()["id"]
        study_id = created.json()["id"]
        detail = client.get(f"/api/v1/calibration-studies/{study_id}")
        subjects = client.get(f"/api/v1/calibration-studies/{study_id}/subjects")
        listing = client.get("/api/v1/calibration-studies", params={"page_size": 1})

    assert detail.status_code == 200
    assert detail.json()["status"] == "draft"
    assert detail.json()["subject_count"] == 2
    assert [item["trace_id"] for item in subjects.json()["items"]] == trace_ids
    assert [item["reference_label"] for item in subjects.json()["items"]] == [
        "failed",
        "passed",
    ]
    assert listing.json()["has_more"] is True


def test_concurrent_study_creation_produces_distinct_stable_snapshots() -> None:
    with TestClient(create_app()) as client:
        trace = _traces(client, 1)[0]
        trace_id = str(trace["trace_id"])
        reference_set = _reference_set(client, [trace_id])
        set_id = str(reference_set["id"])
        _begin(client, set_id)
        _label(client, set_id, trace_id, "passed")
        assert client.post(f"/api/v1/human-reference-sets/{set_id}/freeze").status_code == 200

    async def create(name: str) -> CalibrationStudy:
        async with SessionLocal() as session:
            return await create_study(
                session,
                CalibrationStudyCreate(
                    name=name,
                    reference_set_id=UUID(set_id),
                ),
            )

    async def exercise() -> list[CalibrationStudy]:
        return list(await asyncio.gather(create("Concurrent A"), create("Concurrent B")))

    studies = asyncio.run(exercise())
    assert len({str(study.id) for study in studies}) == 2
    assert all(study.subject_count == 1 for study in studies)


def test_concurrent_identical_annotation_is_one_row_and_freeze_serializes_mutations() -> None:
    with TestClient(create_app()) as client:
        trace = _traces(client, 1)[0]
        trace_id = str(trace["trace_id"])
        reference_set = _reference_set(client, [trace_id])
        set_id = str(reference_set["id"])
        _begin(client, set_id)
        _label(client, set_id, trace_id, "passed")

    async def exercise() -> tuple[list[object], int, str, list[object]]:
        payload = HumanAnnotationWrite(trace_id=trace_id, annotator_id="reviewer-1", label="passed")

        async def annotate() -> object:
            async with SessionLocal() as session:
                try:
                    return await write_annotation(session, UUID(set_id), payload)
                except ReferenceSetConflict as error:
                    return error

        first, second = await asyncio.gather(annotate(), annotate())

        async def late_annotation() -> object:
            late = HumanAnnotationWrite(
                trace_id=trace_id, annotator_id="reviewer-2", label="failed"
            )
            async with SessionLocal() as session:
                try:
                    return await write_annotation(session, UUID(set_id), late)
                except ReferenceSetConflict as error:
                    return error

        async def freeze() -> object:
            async with SessionLocal() as session:
                await freeze_reference_set(session, UUID(set_id))
                return "frozen"

        freeze_results = await asyncio.gather(freeze(), late_annotation())
        async with SessionLocal() as session:
            count = await session.scalar(select(func.count()).select_from(HumanAnnotationRecord))
            record = await session.get(HumanReferenceSetRecord, UUID(set_id))
            assert record is not None
            return [first, second], int(count or 0), record.status, freeze_results

    results, count, status, freeze_results = asyncio.run(exercise())
    assert count in {1, 2}
    assert status == "frozen"
    assert not any(isinstance(result, ReferenceSetConflict) for result in results)
    assert freeze_results[0] == "frozen"
    assert isinstance(freeze_results[1], (HumanAnnotationRecord, ReferenceSetConflict))


def test_missing_set_zero_subject_and_invalid_inputs_are_safe() -> None:
    missing_id = "00000000-0000-0000-0000-000000000000"
    with TestClient(create_app()) as client:
        empty = client.post("/api/v1/human-reference-sets", json={"name": "Empty"}).json()
        assert (
            client.post(f"/api/v1/human-reference-sets/{empty['id']}/begin-labeling").status_code
            == 409
        )
        assert client.get(f"/api/v1/human-reference-sets/{missing_id}").status_code == 404
        assert (
            client.post(
                "/api/v1/human-reference-sets",
                json={"name": "x" * 201},
            ).status_code
            == 422
        )
        assert (
            client.get("/api/v1/human-reference-sets", params={"page_size": 101}).status_code == 422
        )
        assert (
            client.post(
                "/api/v1/calibration-studies",
                json={"name": "Missing", "reference_set_id": missing_id},
            ).status_code
            == 404
        )


def test_trace_delete_is_restricted_by_reference_and_study_subjects() -> None:
    with TestClient(create_app()) as client:
        trace = _traces(client, 1)[0]
        trace_id = str(trace["trace_id"])
        reference_set = _reference_set(client, [trace_id])
        set_id = str(reference_set["id"])
        _begin(client, set_id)
        _label(client, set_id, trace_id, "passed")
        client.post(f"/api/v1/human-reference-sets/{set_id}/freeze")
        study = client.post(
            "/api/v1/calibration-studies",
            json={"name": "Deletion policy", "reference_set_id": set_id},
        )
        assert study.status_code == 201

    async def verify() -> None:
        async with SessionLocal() as session:
            study_count = await session.scalar(
                select(func.count()).select_from(CalibrationStudyRecord)
            )
            assert study_count == 1
            trace = await session.get(TraceRecord, trace_id)
            assert trace is not None
            await session.delete(trace)
            with pytest.raises(IntegrityError):
                await session.commit()

    asyncio.run(verify())
