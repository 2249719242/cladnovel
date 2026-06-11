#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.config import DataModulesConfig
from data_modules.index_manager import IndexManager


def test_commit_service_rejects_when_missed_nodes_exist(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "missed_nodes": ["发现陷阱"]},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "rejected"


def test_commit_service_accepts_when_all_checks_pass(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "accepted"
    assert payload["contract_refs"]["master"] == "MASTER_SETTING.json"
    assert payload["contract_refs"]["volume"] == "volume_001.json"
    assert payload["contract_refs"]["chapter"] == "chapter_003.json"
    assert payload["outline_snapshot"]["covered_nodes"] == ["发现陷阱"]


def test_commit_service_includes_volume_ref_and_write_fact_provenance(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    assert payload["contract_refs"]["volume"] == "volume_001.json"
    assert payload["provenance"]["write_fact_role"] == "chapter_commit"
    assert payload["provenance"]["projection_role"] == "derived_read_models"


def test_chapter_commit_cli_builds_and_persists_commit(tmp_path, monkeypatch):
    review_path = tmp_path / "review.json"
    fulfillment_path = tmp_path / "fulfillment.json"
    disambiguation_path = tmp_path / "disambiguation.json"
    extraction_path = tmp_path / "extraction.json"
    review_path.write_text('{"blocking_count": 0}', encoding="utf-8")
    fulfillment_path.write_text(
        '{"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []}',
        encoding="utf-8",
    )
    disambiguation_path.write_text('{"pending": []}', encoding="utf-8")
    extraction_path.write_text('{"state_deltas": [], "entity_deltas": [], "accepted_events": []}', encoding="utf-8")

    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from chapter_commit import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "chapter_commit",
            "--project-root",
            str(tmp_path),
            "--chapter",
            "3",
            "--review-result",
            str(review_path),
            "--fulfillment-result",
            str(fulfillment_path),
            "--disambiguation-result",
            str(disambiguation_path),
            "--extraction-result",
            str(extraction_path),
        ],
    )
    main()

    assert (tmp_path / ".story-system" / "commits" / "chapter_003.commit.json").is_file()


def test_apply_projections_writes_events_and_amend_proposals(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": ["发现陷阱"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "",
            "accepted_events": [
                {
                    "event_id": "evt-001",
                    "chapter": 3,
                    "event_type": "world_rule_broken",
                    "subject": "金手指",
                    "payload": {
                        "field": "world_rule",
                        "base_value": "每日一次",
                        "proposed_value": "短时失控突破",
                    },
                }
            ],
        },
    )

    service.apply_projections(payload)

    assert (tmp_path / ".story-system" / "events" / "chapter_003.events.json").is_file()
    manager = IndexManager(DataModulesConfig.from_project_root(tmp_path))
    with manager._get_conn() as conn:
        row = conn.execute(
            """
            SELECT record_type, field, override_value, status
            FROM override_contracts
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row["record_type"] == "amend_proposal"
    assert row["field"] == "world_rule"
    assert row["override_value"] == "短时失控突破"
    assert row["status"] == "pending"


def test_build_commit_carries_scene_chunks(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"missed_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "accepted_events": [],
            "summary_text": "",
            "scene_chunks": [{"index": 1, "content": "场景一"}],
        },
    )
    assert payload["scene_chunks"] == [{"index": 1, "content": "场景一"}]


def test_resume_projections_returns_error_when_commit_missing(tmp_path):
    service = ChapterCommitService(tmp_path)
    result = service.resume_projections(99)
    assert result["error"] == "commit_not_found"


def test_resume_projections_only_reruns_failed_and_pending(tmp_path, monkeypatch):
    import data_modules.state_projection_writer as spw
    import data_modules.index_projection_writer as ipw
    import data_modules.summary_projection_writer as sumw
    import data_modules.memory_projection_writer as memw
    import data_modules.vector_projection_writer as vpw

    calls = []

    def make_writer(name, applied=True):
        class _Stub:
            def __init__(self, project_root):
                pass

            def apply(self, payload):
                calls.append(name)
                return {"applied": applied, "writer": name}

        return _Stub

    monkeypatch.setattr(spw, "StateProjectionWriter", make_writer("state"))
    monkeypatch.setattr(ipw, "IndexProjectionWriter", make_writer("index"))
    monkeypatch.setattr(sumw, "SummaryProjectionWriter", make_writer("summary"))
    monkeypatch.setattr(memw, "MemoryProjectionWriter", make_writer("memory"))
    monkeypatch.setattr(vpw, "VectorProjectionWriter", make_writer("vector"))

    service = ChapterCommitService(tmp_path)
    payload = {
        "meta": {"schema_version": "story-system/v1", "chapter": 7, "status": "accepted"},
        "accepted_events": [],
        "state_deltas": [],
        "entity_deltas": [],
        "summary_text": "",
        "scene_chunks": [],
        "projection_status": {
            "state": "failed:boom",
            "index": "done",
            "summary": "done",
            "memory": "skipped",
            "vector": "pending",
        },
    }
    service.persist_commit(payload)

    result = service.resume_projections(7)

    assert calls == ["state"]
    assert result["projection_status"]["state"] == "done"
    assert result["projection_status"]["vector"] == "skipped"
    assert result["projection_status"]["index"] == "done"
    assert result["projection_status"]["summary"] == "done"
    assert result["projection_status"]["memory"] == "skipped"

    persisted = service.load_commit(7)
    assert persisted["projection_status"]["state"] == "done"


def test_resume_projections_noop_when_all_done(tmp_path):
    service = ChapterCommitService(tmp_path)
    payload = {
        "meta": {"schema_version": "story-system/v1", "chapter": 8, "status": "accepted"},
        "projection_status": {
            "state": "done",
            "index": "skipped",
            "summary": "done",
            "memory": "done",
            "vector": "skipped",
        },
    }
    service.persist_commit(payload)
    result = service.resume_projections(8)
    assert result["projection_status"]["state"] == "done"
