# -*- coding: utf-8 -*-
"""对比两份 classified.csv，输出 delta CSV 和 Markdown 报告。"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .models import DrawRec
from .reporter import summarise_by


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _fmt_delta(delta: int) -> str:
    if delta > 0:
        return f"+{delta:,}"
    elif delta < 0:
        return f"{delta:,}"
    return "0"


def _pct(delta: int, base: int) -> str:
    if base == 0:
        return "N/A" if delta != 0 else "0%"
    pct = delta / base * 100
    sign = "+" if pct > 0 else ""
    return f"{sign}{pct:.1f}%"


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _compare_summaries(
    records_a: list[DrawRec],
    records_b: list[DrawRec],
    key_field: str,
    label: str,
    out_dir: Path,
    name_a: str,
    name_b: str,
) -> list[dict[str, Any]]:
    """对比两个汇总维度，返回 delta 行。"""
    rows_a = summarise_by(records_a, key_field)
    rows_b = summarise_by(records_b, key_field)

    map_a = {r[key_field]: r for r in rows_a}
    map_b = {r[key_field]: r for r in rows_b}
    all_keys = sorted(set(map_a.keys()) | set(map_b.keys()))

    delta_rows = []
    for key in all_keys:
        a = map_a.get(key, {})
        b = map_b.get(key, {})
        dc_a = _safe_int(a.get("drawcalls", 0))
        dc_b = _safe_int(b.get("drawcalls", 0))
        faces_a = _safe_int(a.get("draw_faces", 0))
        faces_b = _safe_int(b.get("draw_faces", 0))
        blend_a = _safe_int(a.get("blended_drawcalls", 0))
        blend_b = _safe_int(b.get("blended_drawcalls", 0))
        nodepth_a = _safe_int(a.get("no_depth_write_drawcalls", 0))
        nodepth_b = _safe_int(b.get("no_depth_write_drawcalls", 0))

        delta_rows.append({
            label: key,
            f"A_DrawCall": dc_a,
            f"B_DrawCall": dc_b,
            f"DrawCall_变化": dc_b - dc_a,
            f"A_面数": faces_a,
            f"B_面数": faces_b,
            f"面数_变化": faces_b - faces_a,
            f"A_混合": blend_a,
            f"B_混合": blend_b,
            f"混合_变化": blend_b - blend_a,
            f"A_不写深度": nodepth_a,
            f"B_不写深度": nodepth_b,
            f"不写深度_变化": nodepth_b - nodepth_a,
        })

    # 按面数变化绝对值排序
    delta_rows.sort(key=lambda r: abs(r["面数_变化"]), reverse=True)

    # 写 CSV
    if delta_rows:
        fieldnames = list(delta_rows[0].keys())
        csv_path = out_dir / f"compare_{key_field}_delta.csv"
        _write_csv(csv_path, delta_rows, fieldnames)

    return delta_rows


def compare_records(
    records_a: list[DrawRec],
    records_b: list[DrawRec],
    out_dir: Path,
    name_a: str = "A",
    name_b: str = "B",
) -> None:
    """对比两份 classified 记录，输出 delta CSV 和 Markdown 报告。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    # 汇总统计
    total_dc_a = len(records_a)
    total_dc_b = len(records_b)
    total_faces_a = sum(_safe_int(r.face_estimate) for r in records_a)
    total_faces_b = sum(_safe_int(r.face_estimate) for r in records_b)
    blend_a = sum(1 for r in records_a if r.blend)
    blend_b = sum(1 for r in records_b if r.blend)
    no_depth_a = sum(1 for r in records_a if not r.depth_enable)
    no_depth_b = sum(1 for r in records_b if not r.depth_enable)

    # 各维度对比
    pass_deltas = _compare_summaries(records_a, records_b, "pass_name", "Pass", out_dir, name_a, name_b)
    shader_deltas = _compare_summaries(records_a, records_b, "ps", "Shader", out_dir, name_a, name_b)
    asset_deltas = _compare_summaries(records_a, records_b, "asset_module", "资产模块", out_dir, name_a, name_b)
    resource_deltas = _compare_summaries(records_a, records_b, "resource_category", "资源分类", out_dir, name_a, name_b)

    # 生成 Markdown 报告
    md_lines = []
    md_lines.append(f"# RDC 对比报告: {name_a} vs {name_b}\n")

    md_lines.append("## 总览\n")
    md_lines.append(f"| 指标 | {name_a} | {name_b} | 变化 | 变化率 |")
    md_lines.append("|------|---------|---------|------|--------|")
    for label, va, vb in [
        ("DrawCall", total_dc_a, total_dc_b),
        ("总面数", total_faces_a, total_faces_b),
        ("混合 DrawCall", blend_a, blend_b),
        ("不写深度", no_depth_a, no_depth_b),
    ]:
        delta = vb - va
        md_lines.append(f"| {label} | {va:,} | {vb:,} | {_fmt_delta(delta)} | {_pct(delta, va)} |")

    md_lines.append("\n## 按 Pass 对比 (Top 20)\n")
    md_lines.append("| Pass | A DrawCall | B DrawCall | 变化 | A 面数 | B 面数 | 面数变化 |")
    md_lines.append("|------|-----------|-----------|------|--------|--------|----------|")
    for r in pass_deltas[:20]:
        md_lines.append(f"| {r['Pass']} | {r['A_DrawCall']:,} | {r['B_DrawCall']:,} | {_fmt_delta(r['DrawCall_变化'])} | {r['A_面数']:,} | {r['B_面数']:,} | {_fmt_delta(r['面数_变化'])} |")

    md_lines.append("\n## 按资源分类对比\n")
    md_lines.append("| 资源分类 | A DrawCall | B DrawCall | 变化 | A 面数 | B 面数 | 面数变化 |")
    md_lines.append("|---------|-----------|-----------|------|--------|--------|----------|")
    for r in resource_deltas[:20]:
        md_lines.append(f"| {r['资源分类']} | {r['A_DrawCall']:,} | {r['B_DrawCall']:,} | {_fmt_delta(r['DrawCall_变化'])} | {r['A_面数']:,} | {r['B_面数']:,} | {_fmt_delta(r['面数_变化'])} |")

    report_path = out_dir / "compare_report.md"
    report_path.write_text("\n".join(md_lines), encoding="utf-8")
