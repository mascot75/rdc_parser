# -*- coding: utf-8 -*-
"""Shader 名称归一化：去除 Unity shader keywords，匹配归一化名。"""

from __future__ import annotations

import csv
import re
from pathlib import Path

# 全局 shader 名称索引，按长度降序排列
_SHADER_NAME_INDEX: list[tuple[str, str]] = []


def shader_lookup_key(text: str) -> str:
    """生成 shader 查找 key（小写、去空白）。"""
    text = re.sub(r"\s+", " ", text or "").strip().replace("|", "/").lower()
    return re.sub(r"\s+", "", text)


def load_shader_list_csv(path: Path | None) -> list[str]:
    """从 ShaderList.csv 加载 shader 名称列表。"""
    if not path or not path.exists():
        return []
    shaders: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = re.sub(r"\s+", " ", (
                row.get("Shader Name") or row.get("shader") or row.get("name") or ""
            )).strip().replace("|", "/")
            key = shader_lookup_key(name)
            if key and key not in seen:
                shaders.append(name)
                seen.add(key)
    return shaders


def configure_shader_name_index(shader_names: list[str]) -> None:
    """构建全局 shader 名称查找索引（按 key 长度降序，优先匹配长名）。"""
    global _SHADER_NAME_INDEX
    _SHADER_NAME_INDEX = sorted(
        ((shader_lookup_key(name), name) for name in shader_names if shader_lookup_key(name)),
        key=lambda item: len(item[0]),
        reverse=True,
    )


def strip_shader_keywords(shader: str) -> str:
    """去除 Unity shader keywords，返回归一化 shader 名。

    Unity 的 shader 字符串格式为 "KEYWORD1 KEYWORD2 ... ShaderName"，
    关键字（如 _NORMALMAP, _SHADOWS_SOFT）在前，shader 名（含 / 分隔符）在后。
    算法：先查 ShaderList 索引精确匹配，再从末尾提取含 / 的 token。
    """
    shader = re.sub(r"\s+", " ", shader or "").strip().replace("|", "/")
    if not shader or shader in {"(no PS)", "(no VS)"}:
        return ""
    shader = re.sub(r"\(shared shader\)", "", shader, flags=re.IGNORECASE).strip()

    # 纯 ID 引用
    id_match = re.fullmatch(r"(?:PS|VS)\s+(\d+)", shader, flags=re.IGNORECASE)
    if id_match:
        return f"ShaderID:{id_match.group(1)}"

    # 在 ShaderList 索引中查找
    lookup = shader_lookup_key(shader)
    for shader_key, shader_name in _SHADER_NAME_INDEX:
        if shader_key in lookup:
            return shader_name

    # Hidden/ shader
    if "Hidden/" in shader:
        return "Hidden/" + shader.split("Hidden/", 1)[1]

    # 从末尾提取含 / 的 token（通常是 shader 名）
    parts = shader.split()
    for token in reversed(parts):
        token = token.strip(" ,;()")
        if "/" in token:
            return token

    return shader
