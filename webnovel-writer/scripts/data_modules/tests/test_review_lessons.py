#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审查教训库测试"""
import json

from data_modules.review_schema import ReviewIssue, ReviewResult
from data_modules.review_lessons import (
    append_review_lessons,
    lesson_stats,
    lessons_for_injection,
    lessons_path,
    load_lessons,
    set_lesson_status,
)


def _result(chapter, issues):
    return ReviewResult(chapter=chapter, issues=issues, summary="")


def test_high_and_critical_issues_become_lessons(tmp_path):
    result = _result(
        5,
        [
            ReviewIssue(severity="critical", category="continuity",
                        description="主角用了第3章已失去的能力", fix_hint="复查能力状态"),
            ReviewIssue(severity="high", category="setting", description="宗门等级与设定集冲突"),
            ReviewIssue(severity="medium", category="logic", description="轻微逻辑跳跃"),
            ReviewIssue(severity="low", category="pacing", description="节奏稍慢"),
        ],
    )

    stats = append_review_lessons(tmp_path, result)

    assert stats["added"] == 2  # 仅 high/critical
    rows = load_lessons(tmp_path)
    categories = {r["category"] for r in rows}
    assert categories == {"continuity", "setting"}
    cont = next(r for r in rows if r["category"] == "continuity")
    assert cont["occurrences"] == 1
    assert cont["severity"] == "critical"
    assert cont["fix_hint"] == "复查能力状态"
    assert cont["first_chapter"] == 5 and cont["last_chapter"] == 5


def test_ai_flavor_is_excluded(tmp_path):
    result = _result(
        2,
        [ReviewIssue(severity="high", category="ai_flavor", description="AI味句式重复")],
    )
    stats = append_review_lessons(tmp_path, result)
    assert stats["added"] == 0
    assert load_lessons(tmp_path) == []


def test_recurring_issue_accumulates_occurrences(tmp_path):
    issue = ReviewIssue(severity="high", category="character", description="配角性格前后不一致")
    append_review_lessons(tmp_path, _result(4, [issue]))
    # 同类同文本在第 7 章再次出现，且升级为 critical
    issue2 = ReviewIssue(severity="critical", category="character", description="配角性格前后不一致")
    stats = append_review_lessons(tmp_path, _result(7, [issue2]))

    assert stats["added"] == 0
    assert stats["updated"] == 1
    rows = load_lessons(tmp_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["occurrences"] == 2
    assert row["severity"] == "critical"  # 取更高严重度
    assert row["first_chapter"] == 4 and row["last_chapter"] == 7


def test_injection_rows_are_ranked_and_tagged(tmp_path):
    append_review_lessons(tmp_path, _result(1, [
        ReviewIssue(severity="high", category="setting", description="设定矛盾A"),
    ]))
    # critical 出现两次 → 应排在前
    append_review_lessons(tmp_path, _result(2, [
        ReviewIssue(severity="critical", category="timeline", description="时间线矛盾B"),
    ]))
    append_review_lessons(tmp_path, _result(3, [
        ReviewIssue(severity="critical", category="timeline", description="时间线矛盾B"),
    ]))

    rows = lessons_for_injection(tmp_path, limit=10)
    assert rows[0]["category"] == "timeline"
    assert rows[0]["source_table"] == "review_lessons"
    assert "【审查教训·时间线】" in rows[0]["text"]
    assert "已重复 2 次" in rows[0]["text"]


def test_resolved_lessons_are_not_injected(tmp_path):
    append_review_lessons(tmp_path, _result(1, [
        ReviewIssue(severity="critical", category="logic", description="逻辑漏洞X"),
    ]))
    row_id = load_lessons(tmp_path)[0]["id"]

    assert len(lessons_for_injection(tmp_path)) == 1
    assert set_lesson_status(tmp_path, row_id, "resolved") is True
    assert lessons_for_injection(tmp_path) == []

    stats = lesson_stats(tmp_path)
    assert stats["resolved"] == 1
    assert stats["active"] == 0


def test_set_status_unknown_id_returns_false(tmp_path):
    append_review_lessons(tmp_path, _result(1, [
        ReviewIssue(severity="high", category="setting", description="某问题"),
    ]))
    assert set_lesson_status(tmp_path, "lesson-does-not-exist", "resolved") is False


def test_no_qualifying_issue_writes_no_file(tmp_path):
    stats = append_review_lessons(tmp_path, _result(1, [
        ReviewIssue(severity="low", category="pacing", description="小问题"),
    ]))
    assert stats == {"added": 0, "updated": 0, "total": 0}
    assert not lessons_path(tmp_path).exists()


def _make_project(tmp_path):
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"project": {"genre": "xianxia"}}, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def test_cli_main_stats_and_resolve(capsys, monkeypatch, tmp_path):
    from data_modules import review_lessons

    _make_project(tmp_path)
    append_review_lessons(tmp_path, _result(1, [
        ReviewIssue(severity="critical", category="logic", description="逻辑漏洞Y"),
    ]))
    row_id = load_lessons(tmp_path)[0]["id"]

    monkeypatch.setattr(review_lessons.sys, "argv",
                        ["review_lessons", "--project-root", str(tmp_path), "stats"])
    review_lessons.main()
    out = json.loads(capsys.readouterr().out)
    assert out["data"]["active"] == 1

    monkeypatch.setattr(review_lessons.sys, "argv",
                        ["review_lessons", "--project-root", str(tmp_path), "resolve", "--id", row_id])
    review_lessons.main()
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "success"
    assert load_lessons(tmp_path)[0]["status"] == "resolved"


def test_load_lessons_bad_json_raises(tmp_path):
    import pytest
    p = lessons_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_lessons(tmp_path)


def test_cli_list_and_reopen(capsys, monkeypatch, tmp_path):
    from data_modules import review_lessons

    _make_project(tmp_path)
    append_review_lessons(tmp_path, _result(1, [
        ReviewIssue(severity="high", category="setting", description="设定问题Z"),
    ]))
    rid = load_lessons(tmp_path)[0]["id"]
    set_lesson_status(tmp_path, rid, "resolved")

    monkeypatch.setattr(review_lessons.sys, "argv", [
        "review_lessons", "--project-root", str(tmp_path),
        "list", "--status", "resolved", "--category", "setting", "--limit", "5",
    ])
    review_lessons.main()
    out = json.loads(capsys.readouterr().out)
    assert len(out["data"]) == 1

    monkeypatch.setattr(review_lessons.sys, "argv", [
        "review_lessons", "--project-root", str(tmp_path), "reopen", "--id", rid,
    ])
    review_lessons.main()
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "success"
    assert load_lessons(tmp_path)[0]["status"] == "active"


def test_cli_resolve_unknown_id(capsys, monkeypatch, tmp_path):
    from data_modules import review_lessons

    _make_project(tmp_path)
    monkeypatch.setattr(review_lessons.sys, "argv", [
        "review_lessons", "--project-root", str(tmp_path), "resolve", "--id", "nope",
    ])
    review_lessons.main()
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"


def test_pipeline_emits_review_lessons_stats(tmp_path):
    from review_pipeline import build_review_artifacts

    review_results = tmp_path / "review_results.json"
    review_results.write_text(
        json.dumps({
            "issues": [
                {"severity": "critical", "category": "continuity", "description": "前后矛盾"},
                {"severity": "high", "category": "ai_flavor", "evidence": "AI味"},
            ],
            "summary": "测试",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    payload = build_review_artifacts(tmp_path, chapter=8, review_results_path=review_results)

    assert payload["review_lessons"]["added"] == 1  # ai_flavor 不计入教训
    assert payload["anti_patterns_added"] == 1       # ai_flavor 进 anti_patterns
