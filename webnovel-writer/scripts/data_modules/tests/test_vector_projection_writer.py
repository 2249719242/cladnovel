#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VectorProjectionWriter 单元测试。"""
from data_modules.vector_projection_writer import VectorProjectionWriter


def test_event_to_text_formats_power_breakthrough():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    event = {
        "event_type": "power_breakthrough",
        "chapter": 47,
        "subject": "韩立",
        "payload": {"field": "realm", "new": "筑基初期"},
    }
    text = writer._event_to_text(event)
    assert "第47章" in text
    assert "韩立" in text
    assert "筑基初期" in text


def test_delta_to_text_formats_relationship():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    delta = {
        "from_entity": "韩立",
        "to_entity": "陈巧倩",
        "relationship_type": "合作",
        "chapter": 47,
    }
    text = writer._delta_to_text(delta)
    assert "第47章" in text
    assert "韩立" in text
    assert "陈巧倩" in text
    assert "合作" in text


def test_collect_chunks_from_commit():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    payload = {
        "meta": {"chapter": 47, "status": "accepted"},
        "accepted_events": [
            {
                "event_type": "power_breakthrough",
                "chapter": 47,
                "subject": "韩立",
                "payload": {"field": "realm", "new": "筑基初期"},
            },
        ],
        "entity_deltas": [
            {
                "from_entity": "韩立",
                "to_entity": "陈巧倩",
                "relationship_type": "合作",
                "chapter": 47,
            },
        ],
    }
    chunks = writer._collect_chunks(payload)
    assert len(chunks) == 2
    assert chunks[0]["chunk_type"] == "event"
    assert chunks[1]["chunk_type"] == "entity_delta"


def test_rejected_commit_returns_not_applied():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    writer.project_root = None
    result = writer.apply({"meta": {"status": "rejected", "chapter": 1}})
    assert result["applied"] is False


def test_collect_chunks_includes_summary_and_scene_chunks():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    payload = {
        "meta": {"chapter": 47, "status": "accepted"},
        "summary_text": "韩立进入坊市试探消息真伪。",
        "scene_chunks": [
            {"index": 1, "content": "韩立在坊市外围观察人流。"},
            {"index": 2, "content": "接头时对方袖口露出内门令牌。"},
        ],
        "accepted_events": [],
        "entity_deltas": [],
    }
    chunks = writer._collect_chunks(payload)
    assert [c["chunk_type"] for c in chunks] == ["summary", "scene", "scene"]
    assert chunks[0]["chunk_id"] == "ch0047_summary"
    assert chunks[1]["chunk_id"] == "ch0047_s1"
    assert chunks[1]["parent_chunk_id"] == "ch0047_summary"
    assert chunks[2]["chunk_id"] == "ch0047_s2"


def test_collect_chunks_assigns_unique_ids_to_events_and_deltas():
    writer = VectorProjectionWriter.__new__(VectorProjectionWriter)
    payload = {
        "meta": {"chapter": 47, "status": "accepted"},
        "accepted_events": [
            {
                "event_type": "power_breakthrough",
                "chapter": 47,
                "subject": "韩立",
                "payload": {"field": "realm", "new": "筑基初期"},
            },
            {
                "event_type": "artifact_obtained",
                "chapter": 47,
                "subject": "han_li",
                "payload": {"name": "玄铁令", "owner": "韩立"},
            },
        ],
        "entity_deltas": [
            {
                "from_entity": "韩立",
                "to_entity": "陈巧倩",
                "relationship_type": "合作",
                "chapter": 47,
            },
        ],
    }
    chunks = writer._collect_chunks(payload)
    ids = [c["chunk_id"] for c in chunks]
    assert len(ids) == len(set(ids)) == 3
    assert ids == ["ch0047_evt0", "ch0047_evt1", "ch0047_ed0"]
