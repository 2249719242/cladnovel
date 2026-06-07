#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小卷（故事弧 / arc）规划层。

在"卷（~50 章）"和"章"之间引入中间层：把一卷拆成若干小卷，每个小卷是一个
完整子冲突（起→升级→兑现），并就近规划该弧的**新登场人物**与**关键场景**。

设计要点：
- 真源是结构化 JSON：大纲/第{V}卷-小卷规划.json（程序读取，供章→弧映射与写前注入）。
- 同时可渲染人类可读的 大纲/第{V}卷-小卷规划.md。
- 章→弧映射复用卷映射（state.volumes_planned），再在卷内按章节区间定位小卷。
- 写作时由 extract_chapter_context 注入"当前小卷"上下文（目标/班底/舞台/弧末高潮）。
- 向后兼容：没有小卷规划文件的项目，arc_context 返回 {}，主流程照常。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .cli_output import print_error, print_success
from .cli_args import normalize_global_project_root

try:
    from chapter_outline_loader import volume_num_for_chapter_from_state
    from chapter_paths import volume_num_for_chapter
except ImportError:  # pragma: no cover
    from scripts.chapter_outline_loader import volume_num_for_chapter_from_state
    from scripts.chapter_paths import volume_num_for_chapter

try:
    from security_utils import atomic_write_json
except ImportError:  # pragma: no cover
    from scripts.security_utils import atomic_write_json


# 小卷建议章数（弹性，仅作校验提醒，非硬门槛）
ARC_MIN_CHAPTERS = 6
ARC_MAX_CHAPTERS = 18
_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def arc_plan_path(project_root: str | Path, volume: int) -> Path:
    return Path(project_root).expanduser().resolve() / "大纲" / f"第{int(volume)}卷-小卷规划.json"


def load_arc_plan(project_root: str | Path, volume: int) -> Optional[Dict[str, Any]]:
    path = arc_plan_path(project_root, volume)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bad JSON in {path}") from exc
    if not isinstance(data, dict):
        return None
    data.setdefault("volume", int(volume))
    arcs = data.get("arcs")
    data["arcs"] = [a for a in arcs if isinstance(a, dict)] if isinstance(arcs, list) else []
    return data


def save_arc_plan(project_root: str | Path, plan: Dict[str, Any]) -> Path:
    volume = int(plan.get("volume") or 0)
    path = arc_plan_path(project_root, volume)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, plan, backup=True)
    return path


def _resolve_volume(project_root: str | Path, chapter: int) -> Optional[int]:
    root = Path(project_root)
    return volume_num_for_chapter_from_state(root, int(chapter)) or volume_num_for_chapter(int(chapter))


def arc_for_chapter(project_root: str | Path, chapter: int) -> Optional[Dict[str, Any]]:
    volume = _resolve_volume(project_root, chapter)
    if not volume:
        return None
    plan = load_arc_plan(project_root, volume)
    if not plan:
        return None
    for arc in plan.get("arcs", []):
        start = int(arc.get("chapter_start") or 0)
        end = int(arc.get("chapter_end") or 0)
        if start <= int(chapter) <= end:
            return {**arc, "volume": volume}
    return None


def arc_context_for_chapter(project_root: str | Path, chapter: int) -> Dict[str, Any]:
    """写前注入用的紧凑'当前小卷'上下文；无小卷规划时返回 {}。"""
    arc = arc_for_chapter(project_root, chapter)
    if not arc:
        return {}
    return {
        "arc_id": str(arc.get("arc_id") or ""),
        "name": str(arc.get("name") or ""),
        "volume": int(arc.get("volume") or 0),
        "chapter_start": int(arc.get("chapter_start") or 0),
        "chapter_end": int(arc.get("chapter_end") or 0),
        "goal": str(arc.get("goal") or ""),
        "core_conflict": str(arc.get("core_conflict") or ""),
        "arc_climax": str(arc.get("arc_climax") or ""),
        "new_characters": list(arc.get("new_characters") or []),
        "key_scenes": list(arc.get("key_scenes") or []),
        "foreshadow": list(arc.get("foreshadow") or []),
    }


def _volume_range_from_state(project_root: str | Path, volume: int) -> Optional[Tuple[int, int]]:
    state_path = Path(project_root) / ".webnovel" / "state.json"
    if not state_path.is_file():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    planned = (state.get("progress") or {}).get("volumes_planned")
    if not isinstance(planned, list):
        return None
    for item in planned:
        if isinstance(item, dict) and item.get("volume") == int(volume):
            m = _RANGE_RE.match(str(item.get("chapters_range") or ""))
            if m:
                return int(m.group(1)), int(m.group(2))
    return None


def validate_arc_plan(
    plan: Dict[str, Any],
    vol_start: Optional[int] = None,
    vol_end: Optional[int] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    arcs = list(plan.get("arcs") or [])

    if not arcs:
        errors.append("小卷列表为空")
        return {"ok": False, "errors": errors, "warnings": warnings}

    seen_ids: set = set()
    parsed: List[Tuple[int, int, Dict[str, Any]]] = []
    for idx, arc in enumerate(arcs, start=1):
        aid = str(arc.get("arc_id") or "").strip()
        name = str(arc.get("name") or "").strip()
        start = arc.get("chapter_start")
        end = arc.get("chapter_end")
        if not aid:
            errors.append(f"第{idx}个小卷缺少 arc_id")
        elif aid in seen_ids:
            errors.append(f"arc_id 重复：{aid}")
        else:
            seen_ids.add(aid)
        if not name:
            warnings.append(f"小卷 {aid or idx} 缺少 name")
        if not isinstance(start, int) or not isinstance(end, int) or start <= 0 or end < start:
            errors.append(f"小卷 {aid or idx} 章节区间非法：{start}-{end}")
            continue
        if not str(arc.get("goal") or "").strip():
            warnings.append(f"小卷 {aid or idx} 缺少 goal")
        if not str(arc.get("arc_climax") or "").strip():
            warnings.append(f"小卷 {aid or idx} 缺少 arc_climax")
        if not list(arc.get("new_characters") or []):
            warnings.append(f"小卷 {aid or idx} 没有新登场人物清单")
        if not list(arc.get("key_scenes") or []):
            warnings.append(f"小卷 {aid or idx} 没有关键场景清单")
        span = end - start + 1
        if span < ARC_MIN_CHAPTERS or span > ARC_MAX_CHAPTERS:
            warnings.append(
                f"小卷 {aid or idx} 跨度 {span} 章，超出建议区间 {ARC_MIN_CHAPTERS}-{ARC_MAX_CHAPTERS}"
            )
        parsed.append((start, end, arc))

    parsed.sort(key=lambda t: t[0])
    for i in range(1, len(parsed)):
        prev_end = parsed[i - 1][1]
        cur_start = parsed[i][0]
        if cur_start <= prev_end:
            errors.append(f"小卷区间重叠：…{prev_end} 与 {cur_start}…")
        elif cur_start != prev_end + 1:
            warnings.append(f"小卷之间存在空档：{prev_end} → {cur_start}")

    if vol_start is not None and parsed and parsed[0][0] != int(vol_start):
        warnings.append(f"首个小卷起点 {parsed[0][0]} ≠ 卷起点 {vol_start}")
    if vol_end is not None and parsed and parsed[-1][1] != int(vol_end):
        warnings.append(f"末个小卷终点 {parsed[-1][1]} ≠ 卷终点 {vol_end}")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def render_arc_plan_markdown(plan: Dict[str, Any]) -> str:
    volume = int(plan.get("volume") or 0)
    lines: List[str] = [f"# 第{volume}卷 · 小卷（故事弧）规划", ""]
    for arc in plan.get("arcs", []):
        aid = str(arc.get("arc_id") or "")
        name = str(arc.get("name") or "")
        start = arc.get("chapter_start")
        end = arc.get("chapter_end")
        lines.append(f"## {name}（{aid}） 第{start}-{end}章")
        lines.append("")
        if arc.get("goal"):
            lines.append(f"- 弧目标：{arc['goal']}")
        if arc.get("core_conflict"):
            lines.append(f"- 核心冲突：{arc['core_conflict']}")
        if arc.get("arc_climax"):
            lines.append(f"- 弧末高潮：{arc['arc_climax']}")
        chars = list(arc.get("new_characters") or [])
        if chars:
            lines.append("- 新登场人物：")
            for c in chars:
                if isinstance(c, dict):
                    name_c = str(c.get("name") or "")
                    role = str(c.get("role") or "")
                    note = str(c.get("note") or "")
                    tail = "；".join(x for x in [role, note] if x)
                    lines.append(f"  - {name_c}（{tail}）" if tail else f"  - {name_c}")
                else:
                    lines.append(f"  - {c}")
        scenes = list(arc.get("key_scenes") or [])
        if scenes:
            lines.append("- 关键场景：")
            for s in scenes:
                if isinstance(s, dict):
                    name_s = str(s.get("name") or "")
                    func = str(s.get("function") or "")
                    lines.append(f"  - {name_s}（{func}）" if func else f"  - {name_s}")
                else:
                    lines.append(f"  - {s}")
        fores = list(arc.get("foreshadow") or [])
        if fores:
            lines.append("- 伏笔（埋/收）：")
            for f in fores:
                if isinstance(f, dict):
                    content = str(f.get("content") or "")
                    action = str(f.get("action") or "")
                    note = str(f.get("note") or "")
                    tail = "；".join(x for x in [action, note] if x)
                    lines.append(f"  - {content}（{tail}）" if tail else f"  - {content}")
                else:
                    lines.append(f"  - {f}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Arc (小卷) Planner CLI")
    parser.add_argument("--project-root", type=str, required=True, help="项目根目录")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出某卷的小卷")
    p_list.add_argument("--volume", type=int, required=True)

    p_current = sub.add_parser("current", help="查询某章所属小卷上下文")
    p_current.add_argument("--chapter", type=int, required=True)

    p_validate = sub.add_parser("validate", help="校验某卷的小卷规划")
    p_validate.add_argument("--volume", type=int, required=True)
    p_validate.add_argument("--range", type=str, default="", help="卷章节区间 start-end（缺省时从 state 读取）")

    p_render = sub.add_parser("render-md", help="由 JSON 渲染人类可读 MD")
    p_render.add_argument("--volume", type=int, required=True)

    args = parser.parse_args(normalize_global_project_root(sys.argv[1:]))

    from project_locator import resolve_project_root

    project_root = resolve_project_root(args.project_root)

    if args.command == "list":
        plan = load_arc_plan(project_root, args.volume)
        if not plan:
            print_error("NOT_FOUND", f"未找到第{args.volume}卷小卷规划",
                        suggestion=f"先生成 {arc_plan_path(project_root, args.volume)}")
            return
        print_success(plan, message="arc_list")
        return

    if args.command == "current":
        ctx = arc_context_for_chapter(project_root, args.chapter)
        if not ctx:
            print_error("NOT_FOUND", f"第{args.chapter}章无所属小卷",
                        suggestion="确认该卷已生成小卷规划且区间覆盖该章")
            return
        print_success(ctx, message="arc_current")
        return

    if args.command == "validate":
        plan = load_arc_plan(project_root, args.volume)
        if not plan:
            print_error("NOT_FOUND", f"未找到第{args.volume}卷小卷规划")
            return
        vol_start = vol_end = None
        if args.range:
            m = _RANGE_RE.match(args.range)
            if m:
                vol_start, vol_end = int(m.group(1)), int(m.group(2))
        else:
            rng = _volume_range_from_state(project_root, args.volume)
            if rng:
                vol_start, vol_end = rng
        result = validate_arc_plan(plan, vol_start, vol_end)
        print_success(result, message="arc_validate")
        return

    if args.command == "render-md":
        plan = load_arc_plan(project_root, args.volume)
        if not plan:
            print_error("NOT_FOUND", f"未找到第{args.volume}卷小卷规划")
            return
        md_path = arc_plan_path(project_root, args.volume).with_suffix(".md")
        md_path.write_text(render_arc_plan_markdown(plan), encoding="utf-8")
        print_success({"path": str(md_path)}, message="arc_render_md")
        return

    print_error("UNKNOWN_COMMAND", "未知命令", suggestion="请查看 --help")


if __name__ == "__main__":
    from runtime_compat import enable_windows_utf8_stdio

    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
