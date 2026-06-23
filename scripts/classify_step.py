# -*- coding: utf-8 -*-
"""分类步骤：读取 drawcalls.csv，应用 shader 归一化 + 资源分类，输出 classified.csv。"""

from __future__ import annotations

import csv
from dataclasses import fields
from pathlib import Path

from .models import DrawRec, HEADER_ZH
from .shader import strip_shader_keywords
from .resource import (
    ResourceMatcher, ResourceInfo,
    resolve_asset_module, finalize_resource_info,
    parse_keyword_rules_md,
)
from .xml_helpers import clean_name
from .classifier import classify


def classify_records(
    records: list[DrawRec],
    resource_matcher: ResourceMatcher | None = None,
) -> list[DrawRec]:
    """对 DrawRec 列表应用分类（shader 归一化 + 资源匹配）。

    流程：shader 关键字去除 → 资源路径匹配 → 资产模块推断 → mesh_type 重新分类。
    """
    for r in records:
        shader_name = strip_shader_keywords(r.ps if r.ps not in {"(no PS)", ""} else r.vs)
        r.ps = shader_name or r.ps

        # 资源分类匹配
        resource_info = resource_matcher.match(r.vb0, r.ib, r.marker, shader_name, face_estimate=r.ib_capacity_faces_estimate) if resource_matcher else ResourceInfo()

        # 资产模块推断
        asset_module, asset_module_source = resolve_asset_module(
            resource_info, r.mesh_type, r.vb0, r.ib, shader_name, r.marker,
        )

        # 补全资源信息
        resource_info = finalize_resource_info(
            resource_info, asset_module, asset_module_source, r.vb0, r.ib, shader_name, r.marker,
        )

        # 用归一化 shader 重新分类 mesh_type
        mesh_type = classify(r.marker, shader_name, r.rt)

        # 更新字段
        r.mesh_type = mesh_type
        r.asset_module = asset_module
        r.asset_module_source = asset_module_source
        r.resource_type = resource_info.resource_type
        r.resource_category = resource_info.resource_category
        r.resource_path = resource_info.resource_path
        r.matched_mesh_name = resource_info.matched_mesh_name
        r.resource_match_source = resource_info.source
        r.mesh_key = clean_name(f"{mesh_type} | VB:{r.vb0} | IB:{r.ib} | PS:{shader_name or r.ps}")

    return records


def write_classified_csv(path: Path, records: list[DrawRec]) -> None:
    """写入 classified.csv（完整字段）。"""
    field_names = [f.name for f in fields(DrawRec)]
    headers = [HEADER_ZH.get(k, k) for k in field_names]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in records:
            w.writerow([getattr(r, k) for k in field_names])


def load_classified_csv(path: Path) -> list[DrawRec]:
    """从 classified.csv 加载完整 DrawRec 列表。"""
    records: list[DrawRec] = []
    zh_to_en = {v: k for k, v in HEADER_ZH.items()}
    field_names = {f.name for f in fields(DrawRec)}

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # 预构建列名映射：CSV列名 → 英文字段名
        col_map: dict[str, str] = {}
        for col in (reader.fieldnames or []):
            if col in field_names:
                col_map[col] = col
            elif col in zh_to_en:
                col_map[zh_to_en[col]] = col  # CSV 列名是中文 → 映射英文key到中文列

        def get(row: dict, key: str) -> str:
            csv_col = col_map.get(key)
            if csv_col:
                val = row.get(csv_col, "")
                if val:
                    return val
            return ""

        def get_bool(row: dict, key: str) -> bool:
            return get(row, key).lower() in ("true", "1", "是")

        def get_int(row: dict, key: str) -> int:
            try:
                return int(get(row, key) or "0")
            except ValueError:
                return 0

        for row in reader:
            records.append(DrawRec(
                event=get_int(row, "event"),
                draw_type=get(row, "draw_type"),
                mesh_type=get(row, "mesh_type"),
                asset_module=get(row, "asset_module"),
                asset_module_source=get(row, "asset_module_source"),
                resource_type=get(row, "resource_type"),
                resource_category=get(row, "resource_category"),
                resource_path=get(row, "resource_path"),
                matched_mesh_name=get(row, "matched_mesh_name"),
                resource_match_source=get(row, "resource_match_source"),
                mesh_key=get(row, "mesh_key"),
                marker=get(row, "marker"),
                pass_name=get(row, "pass_name"),
                topology=get(row, "topology"),
                face_estimate=get(row, "face_estimate"),
                face_known=get_bool(row, "face_known"),
                face_source=get(row, "face_source"),
                indirect_args_decoded=get_bool(row, "indirect_args_decoded"),
                ib_capacity_faces_estimate=get(row, "ib_capacity_faces_estimate"),
                combined_faces_estimate=get(row, "combined_faces_estimate"),
                index_or_vertex_count=get_int(row, "index_or_vertex_count"),
                instance_count=get_int(row, "instance_count"),
                vb0=get(row, "vb0"),
                ib=get(row, "ib"),
                vs=get(row, "vs"),
                ps=get(row, "ps"),
                raw_ps=get(row, "raw_ps"),
                blend=get_bool(row, "blend"),
                depth_write=get_bool(row, "depth_write"),
                depth_enable=get_bool(row, "depth_enable"),
                rt=get(row, "rt"),
            ))
    return records
