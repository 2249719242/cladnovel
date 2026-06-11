#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""resolve_shared_reference tests"""

from pathlib import Path

from data_modules.genre_profile_builder import (
    _PLUGIN_REFERENCES_DIR,
    resolve_shared_reference,
)


def test_resolve_falls_back_to_plugin_references(tmp_path):
    # 插件安装模式：书项目下没有 .claude/references/，必须回退到插件目录
    resolved = resolve_shared_reference(tmp_path, "genre-profiles.md")
    assert resolved is not None
    assert resolved == _PLUGIN_REFERENCES_DIR / "genre-profiles.md"
    assert resolved.is_file()


def test_resolve_prefers_project_override(tmp_path):
    override_dir = tmp_path / ".claude" / "references"
    override_dir.mkdir(parents=True)
    override = override_dir / "genre-profiles.md"
    override.write_text("# 自定义题材画像", encoding="utf-8")

    resolved = resolve_shared_reference(tmp_path, "genre-profiles.md")
    assert resolved == override


def test_resolve_missing_file_returns_none(tmp_path):
    assert resolve_shared_reference(tmp_path, "no-such-reference.md") is None
