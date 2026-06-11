#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
作者风格指纹（确定性）。

服务于 /webnovel-style 风格画像系统的可量化部分：
- extract：从作者样本文本提取量化指纹（句长分布、段落节奏、对白配比、
  标点习惯、高频副词密度等），存入 .webnovel/style/{name}.fingerprint.json
- compare：把成稿与指纹逐项对比，输出偏差报告（不靠人眼感觉）
- list / activate：管理画像与激活指针

设计原则：
- 纯函数 extract_metrics 便于测试；CLI 只做 IO
- 指纹只承载「可量化」特征；质性特征（叙事视角、口癖、描写习惯）
  由 LLM 写入同目录 {name}.profile.md，两者配套使用
- compare 只报偏差不判死刑：风格对齐是润色参考，阈值宽松
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .cli_args import normalize_global_project_root
from .cli_output import print_error, print_success

_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?…]+")
_PARA_SPLIT_RE = re.compile(r"\n\s*\n|\n")
_DIALOGUE_RE = re.compile(r"[“\"]([^”\"]{1,300})[”\"]")
_MD_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_MD_HEADING_RE = re.compile(r"^#+ .*$", re.MULTILINE)

# 常见万能副词/神态模板词（量化口癖密度用；画像层的个性口癖由 LLM 提取）
_COMMON_ADVERBS = [
    "缓缓", "淡淡", "微微", "轻轻", "默默", "渐渐", "悄悄", "慢慢",
    "顿时", "瞬间", "忽然", "突然", "竟然", "居然", "几乎", "似乎",
]

_PUNCT_KEYS = {
    "exclaim": "！",
    "question": "？",
    "ellipsis": "…",
    "dash": "—",
    "comma": "，",
}

# compare 的相对偏差容差（比例指标用绝对差）
_RATIO_TOLERANCE = 0.12
_RELATIVE_TOLERANCE = 0.30


def _clean_text(text: str) -> str:
    """去掉 markdown frontmatter / 标题行，只留正文。"""
    text = _MD_FRONTMATTER_RE.sub("", text)
    text = _MD_HEADING_RE.sub("", text)
    return text.strip()


def _split_sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _split_paragraphs(text: str) -> List[str]:
    return [p.strip() for p in _PARA_SPLIT_RE.split(text) if p.strip()]


def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return (sum((v - m) ** 2 for v in values) / len(values)) ** 0.5


def extract_metrics(text: str) -> Dict[str, Any]:
    """从正文文本提取量化风格指标。纯函数。"""
    text = _clean_text(text)
    char_count = len(re.sub(r"\s", "", text))
    sentences = _split_sentences(text)
    paragraphs = _split_paragraphs(text)
    sent_lens = [float(len(s)) for s in sentences]
    para_lens = [float(len(p)) for p in paragraphs]

    dialogues = _DIALOGUE_RE.findall(text)
    dialogue_chars = sum(len(d) for d in dialogues)

    para_sentence_counts = [len(_split_sentences(p)) or 1 for p in paragraphs]

    punct: Dict[str, float] = {}
    for key, ch in _PUNCT_KEYS.items():
        punct[key] = round(text.count(ch) / char_count * 1000, 2) if char_count else 0.0

    adverb_hits = sum(text.count(w) for w in _COMMON_ADVERBS)

    return {
        "char_count": char_count,
        "sentence_count": len(sentences),
        "sentence_len_mean": round(_mean(sent_lens), 2),
        "sentence_len_std": round(_std(sent_lens), 2),
        "short_sentence_ratio": round(
            sum(1 for v in sent_lens if v <= 10) / len(sent_lens), 3
        ) if sent_lens else 0.0,
        "long_sentence_ratio": round(
            sum(1 for v in sent_lens if v > 30) / len(sent_lens), 3
        ) if sent_lens else 0.0,
        "paragraph_count": len(paragraphs),
        "paragraph_len_mean": round(_mean(para_lens), 2),
        "single_sentence_paragraph_ratio": round(
            sum(1 for c in para_sentence_counts if c == 1) / len(para_sentence_counts), 3
        ) if para_sentence_counts else 0.0,
        "dialogue_count": len(dialogues),
        "dialogue_char_ratio": round(dialogue_chars / char_count, 3) if char_count else 0.0,
        "dialogue_len_mean": round(_mean([float(len(d)) for d in dialogues]), 2),
        "punct_per_1000": punct,
        "common_adverb_per_1000": round(adverb_hits / char_count * 1000, 2) if char_count else 0.0,
    }


# 指纹中参与 compare 的指标及展示名
_COMPARE_FIELDS = {
    "sentence_len_mean": ("平均句长", "relative"),
    "sentence_len_std": ("句长波动", "relative"),
    "short_sentence_ratio": ("短句占比", "ratio"),
    "long_sentence_ratio": ("长句占比", "ratio"),
    "paragraph_len_mean": ("平均段长", "relative"),
    "single_sentence_paragraph_ratio": ("单句成段比", "ratio"),
    "dialogue_char_ratio": ("对白字数占比", "ratio"),
    "dialogue_len_mean": ("对白平均长度", "relative"),
    "common_adverb_per_1000": ("万能副词密度", "relative"),
}


def compare_metrics(sample: Dict[str, Any], draft: Dict[str, Any]) -> Dict[str, Any]:
    """对比样本指纹与成稿指标，输出逐项偏差。纯函数。"""
    items: List[Dict[str, Any]] = []
    for field, (label, kind) in _COMPARE_FIELDS.items():
        s_val = float(sample.get(field) or 0.0)
        d_val = float(draft.get(field) or 0.0)
        if kind == "ratio":
            deviation = abs(d_val - s_val)
            off = deviation > _RATIO_TOLERANCE
        else:
            base = max(abs(s_val), 1e-6)
            deviation = abs(d_val - s_val) / base
            off = deviation > _RELATIVE_TOLERANCE
        items.append({
            "field": field,
            "label": label,
            "sample": s_val,
            "draft": d_val,
            "deviation": round(deviation, 3),
            "off": off,
            "direction": "high" if d_val > s_val else ("low" if d_val < s_val else "equal"),
        })
    off_items = [i for i in items if i["off"]]
    if len(off_items) >= 4:
        alignment = "low"
    elif len(off_items) >= 2:
        alignment = "medium"
    else:
        alignment = "high"
    return {
        "items": items,
        "off_count": len(off_items),
        "off_fields": [i["label"] for i in off_items],
        "alignment": alignment,
    }


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------

def _style_dir(project_root: Path) -> Path:
    return Path(project_root) / ".webnovel" / "style"


def _fingerprint_path(project_root: Path, name: str) -> Path:
    return _style_dir(project_root) / f"{name}.fingerprint.json"


def profile_md_path(project_root: Path, name: str) -> Path:
    return _style_dir(project_root) / f"{name}.profile.md"


def _active_path(project_root: Path) -> Path:
    return _style_dir(project_root) / "active.json"


def load_active_profile(project_root: Path) -> Optional[Dict[str, Any]]:
    """返回激活画像 {name, profile_md, fingerprint}；未激活返回 None。供 context 装配使用。"""
    active_file = _active_path(project_root)
    if not active_file.is_file():
        return None
    try:
        name = json.loads(active_file.read_text(encoding="utf-8")).get("active") or ""
    except json.JSONDecodeError:
        return None
    if not name:
        return None
    result: Dict[str, Any] = {"name": name}
    md = profile_md_path(project_root, name)
    fp = _fingerprint_path(project_root, name)
    result["profile_md"] = md.read_text(encoding="utf-8") if md.is_file() else ""
    result["fingerprint"] = (
        json.loads(fp.read_text(encoding="utf-8")) if fp.is_file() else {}
    )
    return result


_SAFE_NAME_RE = re.compile(r"^[\w一-鿿-]{1,40}$")


def _validate_name(name: str) -> str:
    name = str(name or "").strip()
    if not _SAFE_NAME_RE.match(name):
        raise ValueError("画像名只允许中英文、数字、下划线、连字符，长度 1-40")
    return name


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_extract(args: argparse.Namespace, project_root: Path) -> None:
    name = _validate_name(args.name)
    texts: List[str] = []
    sources: List[str] = []
    for raw in args.files:
        path = Path(raw)
        if not path.is_file():
            raise FileNotFoundError(f"样本文件不存在：{path}")
        texts.append(path.read_text(encoding="utf-8"))
        sources.append(str(path))
    combined = "\n\n".join(texts)
    metrics = extract_metrics(combined)
    if metrics["char_count"] < 3000:
        print_error(
            "SAMPLE_TOO_SMALL",
            f"样本合计 {metrics['char_count']} 字，少于 3000 字，指纹不可靠",
            suggestion="提供至少 3000 字（建议 2-5 个完整章节）的作者样本",
        )
        sys.exit(1)
    payload = {
        "name": name,
        "sources": sources,
        "sample_count": len(texts),
        "metrics": metrics,
    }
    target = _fingerprint_path(project_root, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.activate:
        _active_path(project_root).write_text(
            json.dumps({"active": name}, ensure_ascii=False), encoding="utf-8"
        )
    print_success(
        {**payload, "fingerprint_file": str(target), "activated": bool(args.activate),
         "profile_md_expected": str(profile_md_path(project_root, name))},
        message="fingerprint_extracted",
    )


def _cmd_compare(args: argparse.Namespace, project_root: Path) -> None:
    name = _validate_name(args.name)
    fp_file = _fingerprint_path(project_root, name)
    if not fp_file.is_file():
        raise FileNotFoundError(f"画像指纹不存在：{fp_file}（先运行 extract）")
    sample_metrics = json.loads(fp_file.read_text(encoding="utf-8"))["metrics"]
    draft_path = Path(args.file)
    if not draft_path.is_file():
        raise FileNotFoundError(f"成稿文件不存在：{draft_path}")
    draft_metrics = extract_metrics(draft_path.read_text(encoding="utf-8"))
    report = compare_metrics(sample_metrics, draft_metrics)
    print_success(
        {"name": name, "draft_file": str(draft_path), **report},
        message="style_compared",
    )


def _cmd_list(project_root: Path) -> None:
    style_dir = _style_dir(project_root)
    profiles = sorted(p.name[: -len(".fingerprint.json")] for p in style_dir.glob("*.fingerprint.json")) if style_dir.is_dir() else []
    active = (load_active_profile(project_root) or {}).get("name", "")
    print_success({"profiles": profiles, "active": active}, message="style_profiles")


def _cmd_activate(args: argparse.Namespace, project_root: Path) -> None:
    name = _validate_name(args.name)
    if name != "none" and not _fingerprint_path(project_root, name).is_file():
        raise FileNotFoundError(f"画像不存在：{name}")
    _active_path(project_root).parent.mkdir(parents=True, exist_ok=True)
    _active_path(project_root).write_text(
        json.dumps({"active": "" if name == "none" else name}, ensure_ascii=False),
        encoding="utf-8",
    )
    print_success({"active": "" if name == "none" else name}, message="style_activated")


def main() -> None:
    parser = argparse.ArgumentParser(description="Style fingerprint CLI")
    parser.add_argument("--project-root", type=str, default=None)
    sub = parser.add_subparsers(dest="command")

    p_extract = sub.add_parser("extract", help="从样本文件提取风格指纹")
    p_extract.add_argument("--name", required=True, help="画像名（如作者名）")
    p_extract.add_argument("--files", nargs="+", required=True, help="样本文本文件路径（1 个或多个）")
    p_extract.add_argument("--activate", action="store_true", help="提取后直接设为激活画像")

    p_compare = sub.add_parser("compare", help="成稿与指纹对比")
    p_compare.add_argument("--name", required=True)
    p_compare.add_argument("--file", required=True, help="成稿文件路径")

    sub.add_parser("list", help="列出画像与激活状态")

    p_activate = sub.add_parser("activate", help="激活画像（--name none 取消激活）")
    p_activate.add_argument("--name", required=True)

    args = parser.parse_args(normalize_global_project_root(sys.argv[1:]))

    from project_locator import resolve_project_root
    project_root = resolve_project_root(args.project_root)

    try:
        if args.command == "extract":
            _cmd_extract(args, project_root)
        elif args.command == "compare":
            _cmd_compare(args, project_root)
        elif args.command == "list":
            _cmd_list(project_root)
        elif args.command == "activate":
            _cmd_activate(args, project_root)
        else:
            parser.print_help()
            sys.exit(2)
    except (FileNotFoundError, ValueError) as exc:
        print_error("INPUT_ERROR", str(exc))
        sys.exit(1)


if __name__ == "__main__":
    from runtime_compat import enable_windows_utf8_stdio

    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
