#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
确定性反 AI 扫描器。

把原先散落在 polish-guide / anti-ai-guide / style-adapter 等 md 里、靠模型
自查的「200+ 高频词库 + 词频提醒」下沉为代码扫描：
- 词库唯一真源：data/anti_ai_lexicon.json
- 词法层：按类别统计命中数 / 千字密度，超阈值的类别给出「命中 + 修复方向」
- 结构层：句长分布、单句成段比、重复 4-gram、said-tag 占比（抓"换词不换病"）

设计意图（见 docs 优化说明）：
- 模型不再通读整本词典，只收到「自己这章的命中」，省 token、避免背违禁清单。
- 阈值与词库都在 JSON 里，可按题材/作者校准，不写死在代码。
- 纯函数 scan_text 便于测试；CLI 仅负责解析 --file / --chapter 并定位真源词库。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cli_output import print_error, print_success
from .cli_args import normalize_global_project_root


_LEXICON_PATH = Path(__file__).resolve().parent / "data" / "anti_ai_lexicon.json"
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?…]+")
_PUNCT_RE = re.compile(r"[\s，。！？、；：“”‘’\"'（）()《》〈〉…—\-—~·,.!?;:]+")
# 中文引号包裹的对白
_DIALOGUE_RE = re.compile(r"[“\"]([^”\"]{1,200})[”\"]")


def load_lexicon(path: Optional[Path] = None) -> Dict[str, Any]:
    target = Path(path) if path else _LEXICON_PATH
    return json.loads(target.read_text(encoding="utf-8"))


def _split_sentences(text: str) -> List[str]:
    parts = [seg.strip() for seg in _SENTENCE_SPLIT_RE.split(text)]
    return [seg for seg in parts if seg]


def _strip_punct(text: str) -> str:
    return _PUNCT_RE.sub("", text)


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return var ** 0.5


def scan_lexicon(text: str, lexicon: Dict[str, Any]) -> Dict[str, Any]:
    """按类别统计词库命中与密度，标记超阈值类别。"""
    char_count = len(_strip_punct(text))
    per_1k = (char_count / 1000.0) or 1e-9
    categories_out: List[Dict[str, Any]] = []
    flagged: List[str] = []
    total_hits = 0

    for cat in lexicon.get("categories", []):
        samples: List[Dict[str, Any]] = []
        cat_hits = 0
        for word in cat.get("words", []):
            count = text.count(word)
            if count:
                cat_hits += count
                samples.append({"word": word, "count": count})
        total_hits += cat_hits
        density = round(cat_hits / per_1k, 2)
        threshold = float(cat.get("density_per_1k", 1.0))
        advisory = bool(cat.get("advisory", False))
        over = density > threshold
        if over and not advisory:
            flagged.append(cat["id"])
        samples.sort(key=lambda s: s["count"], reverse=True)
        categories_out.append({
            "id": cat["id"],
            "label": cat["label"],
            "hits": cat_hits,
            "density_per_1k": density,
            "threshold_per_1k": threshold,
            "advisory": advisory,
            "over_threshold": over,
            "hint": cat.get("hint", ""),
            "samples": samples[:8],
        })

    return {
        "char_count": char_count,
        "total_hits": total_hits,
        "categories": categories_out,
        "flagged_categories": flagged,
    }


def scan_structure(text: str, lexicon: Dict[str, Any]) -> Dict[str, Any]:
    """结构层指标：抓'换词不换病'的句式/节奏问题。"""
    th = lexicon.get("structural_thresholds", {})
    short_max = int(th.get("short_sentence_max_chars", 8))
    ngram_n = int(th.get("ngram_n", 4))
    ngram_min = int(th.get("ngram_repeat_min", 4))

    sentences = _split_sentences(text)
    lengths = [len(_strip_punct(s)) for s in sentences]
    lengths = [n for n in lengths if n > 0]
    sentence_count = len(lengths)
    mean_len = round(sum(lengths) / sentence_count, 2) if sentence_count else 0.0
    std_len = round(_std([float(n) for n in lengths]), 2)
    short_ratio = round(sum(1 for n in lengths if n <= short_max) / sentence_count, 3) if sentence_count else 0.0

    paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
    single_sentence_paras = sum(1 for p in paragraphs if len(_split_sentences(p)) <= 1)
    single_ratio = round(single_sentence_paras / len(paragraphs), 3) if paragraphs else 0.0

    # 重复 n-gram（去标点后滑窗）
    flat = _strip_punct(text)
    grams = Counter(flat[i:i + ngram_n] for i in range(len(flat) - ngram_n + 1))
    repeated = [
        {"gram": g, "count": c}
        for g, c in grams.most_common(20)
        if c >= ngram_min
    ][:10]

    # said-tag 占比（近似）：对白行数 vs said-tag 出现数
    dialogue_count = len(_DIALOGUE_RE.findall(text))
    said_patterns = lexicon.get("said_tag_patterns", [])
    said_count = sum(text.count(p) for p in said_patterns)
    said_ratio = round(said_count / dialogue_count, 3) if dialogue_count else 0.0

    flags: List[str] = []
    if sentence_count >= 8 and std_len < float(th.get("sentence_len_std_min", 6.0)):
        flags.append("sentence_len_uniform")
    if sentence_count >= 8 and short_ratio < float(th.get("short_sentence_ratio_min", 0.20)):
        flags.append("short_sentence_scarce")
    if len(paragraphs) >= 6 and single_ratio < float(th.get("single_sentence_paragraph_ratio_min", 0.15)):
        flags.append("paragraph_rhythm_flat")
    if repeated:
        flags.append("repeated_ngram")
    if dialogue_count >= 5 and said_ratio > float(th.get("said_tag_ratio_max", 0.30)):
        flags.append("said_tag_overuse")

    return {
        "sentence_count": sentence_count,
        "mean_sentence_len": mean_len,
        "sentence_len_std": std_len,
        "short_sentence_ratio": short_ratio,
        "single_sentence_paragraph_ratio": single_ratio,
        "repeated_ngrams": repeated,
        "dialogue_count": dialogue_count,
        "said_tag_count": said_count,
        "said_tag_ratio": said_ratio,
        "flags": flags,
    }


_STRUCTURE_HINTS = {
    "sentence_len_uniform": "句长过于整齐，插入短句打断或长句铺陈，制造句长方差。",
    "short_sentence_scarce": "短句太少，紧张/爆点处用短句、单句成段提速。",
    "paragraph_rhythm_flat": "段落节奏平均，让部分段落只留一句话，制造疏密对比。",
    "repeated_ngram": "出现高频重复短语（疑似套话/口癖），改写或替换。",
    "said_tag_overuse": "“说道/淡淡道”等 said-tag 过多，改用前置动作替代（他把杯子一搁——“不去。”）。",
}


def scan_text(text: str, lexicon: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    lex = lexicon or load_lexicon()
    lexicon_result = scan_lexicon(text, lex)
    structure_result = scan_structure(text, lex)

    findings: List[Dict[str, Any]] = []
    for cat in lexicon_result["categories"]:
        if cat["over_threshold"] and not cat["advisory"]:
            top = "、".join(f"{s['word']}×{s['count']}" for s in cat["samples"][:5])
            findings.append({
                "type": "lexicon",
                "category": cat["id"],
                "label": cat["label"],
                "detail": f"{cat['label']}密度 {cat['density_per_1k']}/千字（阈值 {cat['threshold_per_1k']}）：{top}",
                "hint": cat["hint"],
            })
    for flag in structure_result["flags"]:
        findings.append({
            "type": "structure",
            "category": flag,
            "detail": _structure_detail(flag, structure_result),
            "hint": _STRUCTURE_HINTS.get(flag, ""),
        })

    hard_flagged = len(lexicon_result["flagged_categories"]) + sum(
        1 for f in structure_result["flags"] if f != "repeated_ngram"
    )
    if hard_flagged >= 4:
        risk = "high"
    elif hard_flagged >= 1:
        risk = "medium"
    else:
        risk = "low"

    return {
        "char_count": lexicon_result["char_count"],
        "lexicon": lexicon_result,
        "structure": structure_result,
        "findings": findings,
        "summary": {
            "flagged_lexicon_categories": lexicon_result["flagged_categories"],
            "structure_flags": structure_result["flags"],
            "finding_count": len(findings),
            "ai_flavor_risk": risk,
        },
    }


def _structure_detail(flag: str, s: Dict[str, Any]) -> str:
    if flag == "sentence_len_uniform":
        return f"句长标准差 {s['sentence_len_std']}（偏低，句子长度过于接近）"
    if flag == "short_sentence_scarce":
        return f"短句占比 {s['short_sentence_ratio']}（偏低）"
    if flag == "paragraph_rhythm_flat":
        return f"单句成段比 {s['single_sentence_paragraph_ratio']}（偏低，段落节奏平均）"
    if flag == "repeated_ngram":
        grams = "、".join(f"{g['gram']}×{g['count']}" for g in s["repeated_ngrams"][:5])
        return f"重复短语：{grams}"
    if flag == "said_tag_overuse":
        return f"said-tag 占比 {s['said_tag_ratio']}（对白 {s['dialogue_count']} 处，标签 {s['said_tag_count']} 个）"
    return flag


def _resolve_text(args: argparse.Namespace) -> str:
    if args.file:
        path = Path(args.file)
        if not path.is_file():
            raise FileNotFoundError(f"未找到正文文件：{path}")
        return path.read_text(encoding="utf-8")
    if args.text is not None:
        return args.text
    if args.chapter is not None:
        from project_locator import resolve_project_root
        from chapter_paths import find_chapter_file

        project_root = resolve_project_root(args.project_root)
        chapter_file = find_chapter_file(project_root, int(args.chapter))
        if not chapter_file or not chapter_file.is_file():
            raise FileNotFoundError(f"未找到第{args.chapter}章正文文件")
        return chapter_file.read_text(encoding="utf-8")
    raise ValueError("必须提供 --file / --chapter / --text 之一")


def main() -> None:
    parser = argparse.ArgumentParser(description="Anti-AI Scanner CLI")
    parser.add_argument("--project-root", type=str, default=None, help="项目根目录（配合 --chapter）")
    parser.add_argument("--file", type=str, default=None, help="正文文件路径")
    parser.add_argument("--chapter", type=int, default=None, help="章节号（从项目解析正文）")
    parser.add_argument("--text", type=str, default=None, help="直接传入文本（主要用于测试）")
    parser.add_argument("--lexicon", type=str, default=None, help="自定义词库路径（默认用内置真源）")

    args = parser.parse_args(normalize_global_project_root(sys.argv[1:]))

    try:
        text = _resolve_text(args)
    except (FileNotFoundError, ValueError) as exc:
        print_error("INPUT_ERROR", str(exc), suggestion="提供 --file 或 --chapter（含 --project-root）")
        sys.exit(1)

    lexicon = load_lexicon(Path(args.lexicon)) if args.lexicon else load_lexicon()
    result = scan_text(text, lexicon)
    print_success(result, message="anti_ai_scan")


if __name__ == "__main__":
    from runtime_compat import enable_windows_utf8_stdio

    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
