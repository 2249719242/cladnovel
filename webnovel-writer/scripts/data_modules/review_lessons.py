#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审查教训库（review lessons）。

把审查发现的 high/critical 问题（ai_flavor 除外，后者已由 anti_patterns 处理）
提炼为结构化「教训」，持久化到 .story-system/review_lessons.json，并在写前注入
runtime 合同的 anti_patterns 通道，实现「审查 → 记忆 → 下章规避」的自更新闭环。

设计要点：
- 存储为顶层 list[dict]，与 anti_patterns.json 保持一致，复用既有注入路径。
- 同一类别的同一问题会按归一化文本去重；重复出现时累加 occurrences 并更新
  last_chapter，让反复出错的同类问题在注入排序中获得更高权重（自更新）。
- 仅高严重度（high/critical）触发收录，避免噪声淹没写作上下文。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .review_schema import ReviewResult
from .cli_output import print_error, print_success
from .cli_args import normalize_global_project_root

try:
    from security_utils import atomic_write_json
except ImportError:  # pragma: no cover
    from scripts.security_utils import atomic_write_json


# 触发自更新的严重度阈值（与 ai_flavor→anti_patterns 的 medium+ 区分，聚焦高严重度）
LESSON_SEVERITIES = {"high", "critical"}
# ai_flavor 已由 append_ai_flavor_anti_patterns 处理，这里不重复收录
EXCLUDED_CATEGORIES = {"ai_flavor"}
# 注入写作合同时的教训上限，避免淹没上下文
DEFAULT_INJECT_LIMIT = 10

CATEGORY_LABELS = {
    "continuity": "连贯性",
    "setting": "设定",
    "character": "角色",
    "timeline": "时间线",
    "logic": "逻辑",
    "pacing": "节奏",
    "other": "其它",
}

_SEVERITY_RANK = {"critical": 2, "high": 1, "medium": 0, "low": -1}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _lesson_key(category: str, lesson_text: str) -> str:
    norm = _normalize(lesson_text)[:120]
    digest = hashlib.sha256(f"{category}|{norm}".encode("utf-8")).hexdigest()[:8]
    return f"lesson-{category}-{digest}"


def _higher_severity(a: Optional[str], b: Optional[str]) -> str:
    rank_a = _SEVERITY_RANK.get(str(a), -2)
    rank_b = _SEVERITY_RANK.get(str(b), -2)
    return str(a) if rank_a >= rank_b else str(b)


def lessons_path(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".story-system" / "review_lessons.json"


def load_lessons(project_root: str | Path) -> List[Dict[str, Any]]:
    path = lessons_path(project_root)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bad JSON in {path}") from exc
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def append_review_lessons(project_root: str | Path, result: ReviewResult) -> Dict[str, int]:
    """读取一份 ReviewResult，把高严重度问题沉淀/更新为教训。"""
    order = load_lessons(project_root)
    by_id: Dict[str, Dict[str, Any]] = {
        str(row.get("id")): row for row in order if row.get("id")
    }

    added = 0
    updated = 0
    chapter = int(result.chapter)

    for issue in result.issues:
        if issue.severity not in LESSON_SEVERITIES:
            continue
        if issue.category in EXCLUDED_CATEGORIES:
            continue
        lesson_text = (issue.description or issue.evidence or "").strip()
        if not lesson_text:
            continue

        key = _lesson_key(issue.category, lesson_text)
        row = by_id.get(key)
        if row is None:
            row = {
                "id": key,
                "category": issue.category,
                "severity": issue.severity,
                "lesson": lesson_text[:300],
                "fix_hint": (issue.fix_hint or "").strip()[:300],
                "evidence": (issue.evidence or "").strip()[:200],
                "first_chapter": chapter,
                "last_chapter": chapter,
                "occurrences": 1,
                "status": "active",
                "created_at": _now(),
                "updated_at": _now(),
            }
            by_id[key] = row
            order.append(row)
            added += 1
        else:
            row["occurrences"] = int(row.get("occurrences", 1) or 1) + 1
            row["last_chapter"] = max(int(row.get("last_chapter", chapter) or chapter), chapter)
            row["severity"] = _higher_severity(row.get("severity"), issue.severity)
            if issue.fix_hint and not str(row.get("fix_hint") or "").strip():
                row["fix_hint"] = issue.fix_hint.strip()[:300]
            # 重新出现视为仍未解决，重新激活
            row["status"] = "active"
            row["updated_at"] = _now()
            updated += 1

    if added or updated:
        atomic_write_json(lessons_path(project_root), order, backup=True)

    return {"added": added, "updated": updated, "total": len(order)}


def _sort_key(row: Dict[str, Any]):
    return (
        _SEVERITY_RANK.get(str(row.get("severity")), -2),
        int(row.get("occurrences", 1) or 1),
        int(row.get("last_chapter", 0) or 0),
    )


def render_lesson_text(row: Dict[str, Any]) -> str:
    category = str(row.get("category") or "other")
    label = CATEGORY_LABELS.get(category, category)
    parts = [f"【审查教训·{label}】{str(row.get('lesson') or '').strip()}"]
    fix_hint = str(row.get("fix_hint") or "").strip()
    if fix_hint:
        parts.append(f"（修复方向：{fix_hint}）")
    occurrences = int(row.get("occurrences", 1) or 1)
    if occurrences > 1:
        parts.append(f"（已重复 {occurrences} 次，务必规避）")
    return "".join(parts)


def lessons_for_injection(
    project_root: str | Path, limit: int = DEFAULT_INJECT_LIMIT
) -> List[Dict[str, Any]]:
    """返回可直接并入 anti_patterns 列表的教训行（仅 active，按权重排序、截断）。"""
    active = [row for row in load_lessons(project_root) if str(row.get("status", "active")) == "active"]
    active.sort(key=_sort_key, reverse=True)
    selected = active[: max(0, int(limit))] if limit else active
    return [
        {
            "text": render_lesson_text(row),
            "source_table": "review_lessons",
            "source_id": str(row.get("id") or ""),
            "category": str(row.get("category") or "other"),
        }
        for row in selected
    ]


def lesson_stats(project_root: str | Path) -> Dict[str, Any]:
    rows = load_lessons(project_root)
    by_category: Dict[str, int] = {}
    by_severity: Dict[str, int] = {}
    active = 0
    resolved = 0
    for row in rows:
        if str(row.get("status", "active")) == "active":
            active += 1
        else:
            resolved += 1
        by_category[str(row.get("category") or "other")] = by_category.get(str(row.get("category") or "other"), 0) + 1
        by_severity[str(row.get("severity") or "medium")] = by_severity.get(str(row.get("severity") or "medium"), 0) + 1
    return {
        "total": len(rows),
        "active": active,
        "resolved": resolved,
        "by_category": by_category,
        "by_severity": by_severity,
        "path": str(lessons_path(project_root)),
    }


def set_lesson_status(project_root: str | Path, lesson_id: str, status: str) -> bool:
    rows = load_lessons(project_root)
    changed = False
    for row in rows:
        if str(row.get("id")) == lesson_id:
            row["status"] = status
            row["updated_at"] = _now()
            changed = True
    if changed:
        atomic_write_json(lessons_path(project_root), rows, backup=True)
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description="Review Lessons CLI")
    parser.add_argument("--project-root", type=str, required=True, help="项目根目录")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="教训库统计")

    p_list = sub.add_parser("list", help="列出教训")
    p_list.add_argument("--category", type=str, default=None)
    p_list.add_argument("--status", type=str, default="active")
    p_list.add_argument("--limit", type=int, default=0, help="0 表示不限")

    p_resolve = sub.add_parser("resolve", help="标记教训为已解决（停止注入）")
    p_resolve.add_argument("--id", required=True)

    p_reopen = sub.add_parser("reopen", help="重新激活已解决教训")
    p_reopen.add_argument("--id", required=True)

    args = parser.parse_args(normalize_global_project_root(sys.argv[1:]))

    from project_locator import resolve_project_root

    project_root = resolve_project_root(args.project_root)

    if args.command == "stats":
        print_success(lesson_stats(project_root), message="review_lessons_stats")
        return
    if args.command == "list":
        rows = load_lessons(project_root)
        if args.category:
            rows = [r for r in rows if str(r.get("category")) == args.category]
        if args.status:
            rows = [r for r in rows if str(r.get("status", "active")) == args.status]
        rows.sort(key=_sort_key, reverse=True)
        if args.limit:
            rows = rows[: args.limit]
        print_success(rows, message="review_lessons_list")
        return
    if args.command == "resolve":
        ok = set_lesson_status(project_root, args.id, "resolved")
        if ok:
            print_success({"id": args.id, "status": "resolved"}, message="review_lesson_resolved")
        else:
            print_error("NOT_FOUND", f"未找到教训：{args.id}", suggestion="先运行 list 查看可用 id")
        return
    if args.command == "reopen":
        ok = set_lesson_status(project_root, args.id, "active")
        if ok:
            print_success({"id": args.id, "status": "active"}, message="review_lesson_reopened")
        else:
            print_error("NOT_FOUND", f"未找到教训：{args.id}", suggestion="先运行 list 查看可用 id")
        return

    print_error("UNKNOWN_COMMAND", "未知命令", suggestion="请查看 --help")


if __name__ == "__main__":
    from runtime_compat import enable_windows_utf8_stdio

    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
