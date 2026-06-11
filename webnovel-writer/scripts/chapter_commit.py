#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio

from data_modules.chapter_commit_service import ChapterCommitService


def _read_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Chapter commit CLI")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="读取已持久化的 commit 文件，只补跑 failed/pending 的 projection",
    )
    parser.add_argument("--review-result", default="")
    parser.add_argument("--fulfillment-result", default="")
    parser.add_argument("--disambiguation-result", default="")
    parser.add_argument("--extraction-result", default="")
    args = parser.parse_args()

    service = ChapterCommitService(Path(args.project_root))

    if args.resume:
        payload = service.resume_projections(args.chapter)
        print(json.dumps(payload, ensure_ascii=False))
        return

    missing = [
        flag
        for flag, value in (
            ("--review-result", args.review_result),
            ("--fulfillment-result", args.fulfillment_result),
            ("--disambiguation-result", args.disambiguation_result),
            ("--extraction-result", args.extraction_result),
        )
        if not value
    ]
    if missing:
        parser.error(f"非 --resume 模式下必须提供: {', '.join(missing)}")

    payload = service.build_commit(
        chapter=args.chapter,
        review_result=_read_json(args.review_result),
        fulfillment_result=_read_json(args.fulfillment_result),
        disambiguation_result=_read_json(args.disambiguation_result),
        extraction_result=_read_json(args.extraction_result),
    )
    service.persist_commit(payload)
    if payload["meta"]["status"] == "accepted":
        payload = service.apply_projections(payload)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
