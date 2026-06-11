#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""style_fingerprint 单元测试。"""

import json
import sys
from pathlib import Path

import pytest

from data_modules.style_fingerprint import (
    compare_metrics,
    extract_metrics,
    load_active_profile,
    main,
    profile_md_path,
)


SAMPLE = (
    "---\nchapter: 1\n---\n"
    "# 第一章\n"
    "韩立眯起眼。\n\n"
    "坊市外人流如织，他在巷口站了足足一炷香，才看清那家铺子的门脸。"
    "门口的伙计换了三拨，每一拨都在用余光扫过往的散修！\n\n"
    "“掌柜的在么？”韩立开口。\n\n"
    "“客官说笑了……”伙计缓缓抬头，淡淡道，“我们这儿只收熟客。”\n\n"
    "他转身就走。买卖不成，消息却到手了——这家铺子，果然有问题。\n"
)


def test_extract_metrics_basic():
    m = extract_metrics(SAMPLE)
    assert m["char_count"] > 0
    assert m["sentence_count"] >= 5
    assert m["paragraph_count"] >= 4
    assert m["dialogue_count"] == 3  # 第二行对白含两个引号段
    assert 0 < m["dialogue_char_ratio"] < 1
    assert m["short_sentence_ratio"] > 0
    # frontmatter 与标题不计入正文
    assert "chapter" not in str(m)
    assert m["common_adverb_per_1000"] > 0  # 缓缓/淡淡命中
    assert m["punct_per_1000"]["exclaim"] > 0


def test_extract_metrics_empty_text():
    m = extract_metrics("")
    assert m["char_count"] == 0
    assert m["sentence_len_mean"] == 0.0
    assert m["dialogue_char_ratio"] == 0.0


def test_compare_metrics_identical_is_high_alignment():
    m = extract_metrics(SAMPLE)
    report = compare_metrics(m, m)
    assert report["off_count"] == 0
    assert report["alignment"] == "high"


def test_compare_metrics_detects_divergence():
    sample = extract_metrics(SAMPLE)
    # 构造风格迥异的成稿：超长句、无对白、无短句
    draft_text = (
        "在那一望无际的旷野尽头延伸着绵延不绝的山脉而山脉之上覆盖着常年不化的积雪并且积雪之下埋藏着无数先民留下的遗迹。"
        * 12
    )
    draft = extract_metrics(draft_text)
    report = compare_metrics(sample, draft)
    assert report["off_count"] >= 4
    assert report["alignment"] == "low"
    assert "对白字数占比" in report["off_fields"]


def _run_cli(monkeypatch, tmp_path, argv):
    monkeypatch.setattr(sys, "argv", ["style_fingerprint", "--project-root", str(tmp_path), *argv])
    main()


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return tmp_path


def test_cli_extract_rejects_small_sample(project, tmp_path, monkeypatch, capsys):
    sample_file = tmp_path / "s.txt"
    sample_file.write_text(SAMPLE, encoding="utf-8")
    with pytest.raises(SystemExit):
        _run_cli(monkeypatch, project, ["extract", "--name", "测试作者", "--files", str(sample_file)])
    assert "SAMPLE_TOO_SMALL" in capsys.readouterr().out


def test_cli_extract_compare_list_activate_roundtrip(project, tmp_path, monkeypatch, capsys):
    sample_file = tmp_path / "s.txt"
    sample_file.write_text(SAMPLE * 30, encoding="utf-8")  # >3000 字
    _run_cli(monkeypatch, project, ["extract", "--name", "测试作者", "--files", str(sample_file), "--activate"])
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "success"
    fp = project / ".webnovel" / "style" / "测试作者.fingerprint.json"
    assert fp.is_file()

    # 激活画像可被 load_active_profile 读取（含 profile.md 内容）
    profile_md_path(project, "测试作者").write_text("# 风格画像：测试作者", encoding="utf-8")
    active = load_active_profile(project)
    assert active["name"] == "测试作者"
    assert "风格画像" in active["profile_md"]
    assert active["fingerprint"]["metrics"]["char_count"] > 3000

    # compare：同源文本应高对齐
    draft = tmp_path / "draft.md"
    draft.write_text(SAMPLE * 5, encoding="utf-8")
    _run_cli(monkeypatch, project, ["compare", "--name", "测试作者", "--file", str(draft)])
    report = json.loads(capsys.readouterr().out)
    assert report["data"]["alignment"] in ("high", "medium")

    # list
    _run_cli(monkeypatch, project, ["list"])
    listed = json.loads(capsys.readouterr().out)
    assert listed["data"]["profiles"] == ["测试作者"]
    assert listed["data"]["active"] == "测试作者"

    # 取消激活
    _run_cli(monkeypatch, project, ["activate", "--name", "none"])
    capsys.readouterr()
    assert load_active_profile(project) is None


def test_cli_compare_missing_profile_errors(project, tmp_path, monkeypatch, capsys):
    draft = tmp_path / "draft.md"
    draft.write_text(SAMPLE, encoding="utf-8")
    with pytest.raises(SystemExit):
        _run_cli(monkeypatch, project, ["compare", "--name", "不存在", "--file", str(draft)])
    assert "INPUT_ERROR" in capsys.readouterr().out


def test_cli_rejects_unsafe_profile_name(project, tmp_path, monkeypatch, capsys):
    sample_file = tmp_path / "s.txt"
    sample_file.write_text(SAMPLE * 30, encoding="utf-8")
    with pytest.raises(SystemExit):
        _run_cli(monkeypatch, project, ["extract", "--name", "../escape", "--files", str(sample_file)])
    assert "INPUT_ERROR" in capsys.readouterr().out
