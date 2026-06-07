#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性反 AI 扫描器测试"""
from data_modules.anti_ai_scanner import (
    load_lexicon,
    scan_lexicon,
    scan_structure,
    scan_text,
)


AI_FLAVORED = (
    "他缓缓开口，淡淡说道。她微微点头，眸中闪过一丝复杂。"
    "他缓缓抬手，轻轻摇头，瞳孔微缩。首先，他要稳住；其次，他要反击。"
    "总而言之，他非常愤怒，内心五味杂陈。"
)

CLEAN = (
    "刀光劈下来时，他人已经在三步之外。\n"
    "“你疯了。”\n"
    "她没理，弯腰捡起那枚还在烫的弹壳，吹了吹。火药味钻进鼻子。\n"
    "门外传来脚步。一个，两个，越来越密。他把保险栓推上去，靠着墙根滑坐下来，"
    "盯着那道越拉越长的影子，等它先动。"
)


def test_lexicon_loads_and_has_categories():
    lex = load_lexicon()
    ids = {c["id"] for c in lex["categories"]}
    assert {"L", "F", "K"}.issubset(ids)


def test_ai_flavored_text_flags_lexicon_categories():
    result = scan_text(AI_FLAVORED)
    flagged = set(result["summary"]["flagged_lexicon_categories"])
    # 万能副词 / 动作套话 / 神态模板 都应被命中并超阈值
    assert "L" in flagged
    assert "F" in flagged
    assert result["summary"]["ai_flavor_risk"] in {"medium", "high"}
    # 命中应附带修复方向
    lexicon_findings = [f for f in result["findings"] if f["type"] == "lexicon"]
    assert lexicon_findings and all(f["hint"] for f in lexicon_findings)


def test_clean_text_low_risk():
    result = scan_text(CLEAN)
    assert result["summary"]["ai_flavor_risk"] == "low"
    assert result["summary"]["flagged_lexicon_categories"] == []


def test_advisory_category_not_hard_flagged():
    lex = load_lexicon()
    # 抽象空泛词（I, advisory）即使命中也不计入硬 flag
    text = "蜕变" * 50
    res = scan_lexicon(text, lex)
    cat_i = next(c for c in res["categories"] if c["id"] == "I")
    assert cat_i["advisory"] is True
    assert cat_i["over_threshold"] is True
    assert "I" not in res["flagged_categories"]


def test_said_tag_overuse_detected():
    text = "\n".join([
        "“走。”他说道。",
        "“不。”她说道。",
        "“为什么？”他问道。",
        "“没空。”她答道。",
        "“再问一次。”他冷冷道。",
        "“滚。”她淡淡道。",
    ])
    s = scan_structure(text, load_lexicon())
    assert s["dialogue_count"] >= 5
    assert s["said_tag_ratio"] > 0.30
    assert "said_tag_overuse" in s["flags"]


def test_repeated_ngram_detected():
    text = "缓缓开口" * 5
    s = scan_structure(text, load_lexicon())
    grams = {g["gram"] for g in s["repeated_ngrams"]}
    assert "缓缓开口" in grams
    assert "repeated_ngram" in s["flags"]


def test_uniform_sentences_flagged():
    # 12 个等长短句，句长方差极低
    text = "。".join(["他走进房间打开了灯"] * 12) + "。"
    s = scan_structure(text, load_lexicon())
    assert "sentence_len_uniform" in s["flags"]


def test_cli_main_with_text(capsys, monkeypatch):
    import json
    from data_modules import anti_ai_scanner

    monkeypatch.setattr(
        anti_ai_scanner.sys, "argv",
        ["anti_ai_scanner", "--text", AI_FLAVORED],
    )
    anti_ai_scanner.main()
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "success"
    assert out["data"]["summary"]["ai_flavor_risk"] in {"medium", "high"}


def test_cli_main_missing_input_errors(capsys, monkeypatch):
    import json
    import pytest
    from data_modules import anti_ai_scanner

    monkeypatch.setattr(anti_ai_scanner.sys, "argv", ["anti_ai_scanner"])
    with pytest.raises(SystemExit):
        anti_ai_scanner.main()
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"


def test_cli_main_with_file(capsys, monkeypatch, tmp_path):
    import json
    from data_modules import anti_ai_scanner

    f = tmp_path / "draft.md"
    f.write_text(AI_FLAVORED, encoding="utf-8")
    monkeypatch.setattr(anti_ai_scanner.sys, "argv", ["anti_ai_scanner", "--file", str(f)])
    anti_ai_scanner.main()
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "success"


def test_structure_detail_all_branches():
    from data_modules.anti_ai_scanner import _structure_detail

    s = {
        "sentence_len_std": 1.0, "short_sentence_ratio": 0.1,
        "single_sentence_paragraph_ratio": 0.05,
        "repeated_ngrams": [{"gram": "缓缓开口", "count": 4}],
        "said_tag_ratio": 0.5, "dialogue_count": 6, "said_tag_count": 3,
    }
    for flag in [
        "sentence_len_uniform", "short_sentence_scarce", "paragraph_rhythm_flat",
        "repeated_ngram", "said_tag_overuse", "unknown_flag",
    ]:
        assert _structure_detail(flag, s)


def test_custom_lexicon_path(tmp_path):
    import json
    lex_path = tmp_path / "lex.json"
    lex_path.write_text(json.dumps({
        "categories": [{"id": "X", "label": "测试", "density_per_1k": 0.1, "words": ["测试词"]}],
        "structural_thresholds": {},
        "said_tag_patterns": [],
    }, ensure_ascii=False), encoding="utf-8")
    result = scan_text("测试词测试词测试词", load_lexicon(lex_path))
    assert "X" in result["summary"]["flagged_lexicon_categories"]
