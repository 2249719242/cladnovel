#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小卷（故事弧）规划层测试"""
import json

import pytest

from data_modules.arc_planner import (
    arc_context_for_chapter,
    arc_for_chapter,
    arc_plan_path,
    load_arc_plan,
    render_arc_plan_markdown,
    save_arc_plan,
    validate_arc_plan,
)


def _arc(aid, name, start, end, **kw):
    base = {
        "arc_id": aid, "name": name, "chapter_start": start, "chapter_end": end,
        "goal": "弧目标", "arc_climax": "弧末高潮",
        "new_characters": [{"name": "叶良辰", "role": "小反派", "note": "退婚挑衅"}],
        "key_scenes": [{"name": "林家祠堂", "function": "羞辱发生地"}],
        "foreshadow": [{"content": "玉佩发光", "action": "埋", "note": "血脉线索"}],
    }
    base.update(kw)
    return base


def _plan(volume, arcs):
    return {"volume": volume, "arcs": arcs}


def _make_project(tmp_path, volumes_planned=None):
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    state = {"project": {"genre": "xianxia"}}
    if volumes_planned is not None:
        state["progress"] = {"volumes_planned": volumes_planned}
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_save_and_load_roundtrip(tmp_path):
    plan = _plan(1, [_arc("v1-a1", "新手村", 1, 12)])
    path = save_arc_plan(tmp_path, plan)
    assert path == arc_plan_path(tmp_path, 1)
    loaded = load_arc_plan(tmp_path, 1)
    assert loaded["volume"] == 1
    assert loaded["arcs"][0]["arc_id"] == "v1-a1"


def test_load_missing_returns_none(tmp_path):
    assert load_arc_plan(tmp_path, 9) is None


def test_load_bad_json_raises(tmp_path):
    p = arc_plan_path(tmp_path, 1)
    p.parent.mkdir(parents=True)
    p.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError):
        load_arc_plan(tmp_path, 1)


def test_arc_for_chapter_uses_state_volume_mapping(tmp_path):
    _make_project(tmp_path, volumes_planned=[{"volume": 2, "chapters_range": "51-100"}])
    save_arc_plan(tmp_path, _plan(2, [
        _arc("v2-a1", "宗门篇", 51, 62),
        _arc("v2-a2", "秘境篇", 63, 75),
    ]))
    arc = arc_for_chapter(tmp_path, 64)
    assert arc["arc_id"] == "v2-a2"
    assert arc["volume"] == 2


def test_arc_for_chapter_fallback_without_state(tmp_path):
    # 无 volumes_planned 时回退到按 50 章/卷推断 → 第 5 章属于第 1 卷
    save_arc_plan(tmp_path, _plan(1, [_arc("v1-a1", "开篇", 1, 12)]))
    arc = arc_for_chapter(tmp_path, 5)
    assert arc is not None
    assert arc["arc_id"] == "v1-a1"


def test_arc_context_compact_and_empty(tmp_path):
    save_arc_plan(tmp_path, _plan(1, [_arc("v1-a1", "开篇", 1, 12)]))
    ctx = arc_context_for_chapter(tmp_path, 3)
    assert ctx["arc_id"] == "v1-a1"
    assert ctx["new_characters"] and ctx["key_scenes"]
    assert set(ctx) >= {"goal", "arc_climax", "chapter_start", "chapter_end"}
    # 区间外的章节无小卷
    assert arc_context_for_chapter(tmp_path, 99) == {}


def test_validate_good_plan(tmp_path):
    plan = _plan(1, [_arc("v1-a1", "A", 1, 12), _arc("v1-a2", "B", 13, 24)])
    res = validate_arc_plan(plan, vol_start=1, vol_end=24)
    assert res["ok"] is True
    assert res["errors"] == []


def test_validate_overlap_is_error(tmp_path):
    plan = _plan(1, [_arc("v1-a1", "A", 1, 12), _arc("v1-a2", "B", 10, 20)])
    res = validate_arc_plan(plan)
    assert res["ok"] is False
    assert any("重叠" in e for e in res["errors"])


def test_validate_gap_and_span_warnings(tmp_path):
    # a1 跨度 3 章（过短，warning）；a1→a2 之间有空档（warning）
    plan = _plan(1, [_arc("v1-a1", "A", 1, 3), _arc("v1-a2", "B", 10, 22)])
    res = validate_arc_plan(plan)
    assert res["ok"] is True
    assert any("空档" in w for w in res["warnings"])
    assert any("跨度" in w for w in res["warnings"])


def test_validate_empty_is_error(tmp_path):
    res = validate_arc_plan(_plan(1, []))
    assert res["ok"] is False


def test_validate_missing_manifests_warn(tmp_path):
    bare = {"arc_id": "v1-a1", "name": "A", "chapter_start": 1, "chapter_end": 12,
            "goal": "g", "arc_climax": "c", "new_characters": [], "key_scenes": []}
    res = validate_arc_plan(_plan(1, [bare]))
    assert any("新登场人物" in w for w in res["warnings"])
    assert any("关键场景" in w for w in res["warnings"])


def test_render_markdown_contains_cast_and_scenes(tmp_path):
    md = render_arc_plan_markdown(_plan(1, [_arc("v1-a1", "新手村", 1, 12)]))
    assert "新手村" in md
    assert "叶良辰" in md
    assert "林家祠堂" in md
    assert "第1-12章" in md


# ---- CLI ----

def test_cli_list_current_validate_render(capsys, monkeypatch, tmp_path):
    from data_modules import arc_planner

    _make_project(tmp_path, volumes_planned=[{"volume": 1, "chapters_range": "1-24"}])
    save_arc_plan(tmp_path, _plan(1, [_arc("v1-a1", "A", 1, 12), _arc("v1-a2", "B", 13, 24)]))

    def run(*argv):
        monkeypatch.setattr(arc_planner.sys, "argv",
                            ["arc_planner", "--project-root", str(tmp_path), *argv])
        arc_planner.main()
        return json.loads(capsys.readouterr().out)

    out = run("list", "--volume", "1")
    assert out["data"]["arcs"][0]["arc_id"] == "v1-a1"

    out = run("current", "--chapter", "14")
    assert out["data"]["arc_id"] == "v1-a2"

    out = run("validate", "--volume", "1")
    assert out["data"]["ok"] is True

    out = run("render-md", "--volume", "1")
    assert out["status"] == "success"
    assert arc_plan_path(tmp_path, 1).with_suffix(".md").is_file()


def test_validate_illegal_range_and_dup_id(tmp_path):
    plan = _plan(1, [
        {"arc_id": "v1-a1", "name": "A", "chapter_start": 5, "chapter_end": 1},  # 非法区间
        {"arc_id": "v1-a1", "name": "B", "chapter_start": 13, "chapter_end": 24},  # 重复 id
        {"name": "C", "chapter_start": 25, "chapter_end": 36},  # 缺 arc_id
    ])
    res = validate_arc_plan(plan)
    assert res["ok"] is False
    assert any("非法" in e for e in res["errors"])
    assert any("重复" in e for e in res["errors"])
    assert any("缺少 arc_id" in e for e in res["errors"])


def test_render_markdown_string_forms_and_minimal(tmp_path):
    arc = {
        "arc_id": "v1-a1", "name": "极简", "chapter_start": 1, "chapter_end": 8,
        "new_characters": ["路人甲"], "key_scenes": ["广场"], "foreshadow": ["神秘信物"],
    }
    md = render_arc_plan_markdown(_plan(1, [arc]))
    assert "路人甲" in md and "广场" in md and "神秘信物" in md


def test_arc_for_chapter_no_plan_returns_none(tmp_path):
    _make_project(tmp_path, volumes_planned=[{"volume": 1, "chapters_range": "1-50"}])
    assert arc_for_chapter(tmp_path, 5) is None


def test_cli_not_found_branches(capsys, monkeypatch, tmp_path):
    from data_modules import arc_planner

    _make_project(tmp_path)

    def run(*argv):
        monkeypatch.setattr(arc_planner.sys, "argv",
                            ["arc_planner", "--project-root", str(tmp_path), *argv])
        arc_planner.main()
        return json.loads(capsys.readouterr().out)

    assert run("list", "--volume", "7")["status"] == "error"
    assert run("validate", "--volume", "7")["status"] == "error"
    assert run("render-md", "--volume", "7")["status"] == "error"


def test_cli_validate_with_explicit_range(capsys, monkeypatch, tmp_path):
    from data_modules import arc_planner

    _make_project(tmp_path)
    save_arc_plan(tmp_path, _plan(3, [_arc("v3-a1", "A", 101, 112)]))
    monkeypatch.setattr(arc_planner.sys, "argv",
                        ["arc_planner", "--project-root", str(tmp_path),
                         "validate", "--volume", "3", "--range", "101-150"])
    arc_planner.main()
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["ok"] is True
    assert any("卷终点" in w for w in out["data"]["warnings"])


def test_cli_current_not_found(capsys, monkeypatch, tmp_path):
    from data_modules import arc_planner

    _make_project(tmp_path)
    monkeypatch.setattr(arc_planner.sys, "argv",
                        ["arc_planner", "--project-root", str(tmp_path), "current", "--chapter", "5"])
    arc_planner.main()
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
