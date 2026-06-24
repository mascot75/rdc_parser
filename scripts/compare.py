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


def _pct(delta: int | float, base: int | float) -> str:
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
    timing_a: dict[int, float] | None = None,
    timing_b: dict[int, float] | None = None,
) -> list[dict[str, Any]]:
    """对比两个汇总维度，返回 delta 行。"""
    rows_a = summarise_by(records_a, key_field, timing_a)
    rows_b = summarise_by(records_b, key_field, timing_b)

    map_a = {r[key_field]: r for r in rows_a}
    map_b = {r[key_field]: r for r in rows_b}
    all_keys = sorted(set(map_a.keys()) | set(map_b.keys()))

    has_timing = timing_a is not None or timing_b is not None

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

        row = {
            label: key,
            "A_DrawCall": dc_a,
            "B_DrawCall": dc_b,
            "DrawCall_变化": dc_b - dc_a,
            "A_面数": faces_a,
            "B_面数": faces_b,
            "面数_变化": faces_b - faces_a,
            "A_混合": blend_a,
            "B_混合": blend_b,
            "混合_变化": blend_b - blend_a,
            "A_不写深度": nodepth_a,
            "B_不写深度": nodepth_b,
            "不写深度_变化": nodepth_b - nodepth_a,
        }

        if has_timing:
            time_a = float(a.get("time_ms", 0))
            time_b = float(b.get("time_ms", 0))
            row["A_耗时(ms)"] = round(time_a, 3)
            row["B_耗时(ms)"] = round(time_b, 3)
            row["耗时变化(ms)"] = round(time_b - time_a, 3)

        delta_rows.append(row)

    if has_timing:
        delta_rows.sort(key=lambda r: abs(r["耗时变化(ms)"]), reverse=True)
    else:
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
    timing_a: dict[int, float] | None = None,
    timing_b: dict[int, float] | None = None,
) -> None:
    """对比两份 classified 记录，输出 delta CSV 和 Markdown 报告。"""
    out_dir.mkdir(parents=True, exist_ok=True)

    has_timing = timing_a is not None or timing_b is not None

    # 汇总统计
    total_dc_a = len(records_a)
    total_dc_b = len(records_b)
    total_faces_a = sum(_safe_int(r.face_estimate) for r in records_a)
    total_faces_b = sum(_safe_int(r.face_estimate) for r in records_b)
    blend_a = sum(1 for r in records_a if r.blend)
    blend_b = sum(1 for r in records_b if r.blend)
    no_depth_a = sum(1 for r in records_a if not r.depth_enable)
    no_depth_b = sum(1 for r in records_b if not r.depth_enable)
    total_time_a = sum(timing_a.values()) if timing_a else 0.0
    total_time_b = sum(timing_b.values()) if timing_b else 0.0

    # 各维度对比
    pass_deltas = _compare_summaries(records_a, records_b, "pass_name", "Pass", out_dir, name_a, name_b, timing_a, timing_b)
    shader_deltas = _compare_summaries(records_a, records_b, "ps", "Shader", out_dir, name_a, name_b, timing_a, timing_b)
    asset_deltas = _compare_summaries(records_a, records_b, "asset_module", "资产模块", out_dir, name_a, name_b, timing_a, timing_b)
    resource_deltas = _compare_summaries(records_a, records_b, "resource_category", "资源分类", out_dir, name_a, name_b, timing_a, timing_b)

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
    if has_timing:
        time_delta = total_time_b - total_time_a
        md_lines.append(f"| GPU 总耗时 (ms) | {total_time_a:.2f} | {total_time_b:.2f} | {time_delta:+.2f} | {_pct(time_delta, total_time_a)} |")

    def _pass_table(deltas: list[dict], title: str) -> None:
        md_lines.append(f"\n## {title}\n")
        if has_timing:
            md_lines.append(f"| Pass | {name_a} DC | {name_b} DC | DC变化 | {name_a} 耗时 | {name_b} 耗时 | 耗时变化 |")
            md_lines.append("|------|----------|----------|--------|-----------|-----------|----------|")
            for r in deltas[:20]:
                md_lines.append(f"| {r['Pass']} | {r['A_DrawCall']:,} | {r['B_DrawCall']:,} | {_fmt_delta(r['DrawCall_变化'])} | {r['A_耗时(ms)']:.3f} | {r['B_耗时(ms)']:.3f} | {r['耗时变化(ms)']:+.3f} |")
        else:
            md_lines.append(f"| Pass | {name_a} DC | {name_b} DC | DC变化 | {name_a} 面数 | {name_b} 面数 | 面数变化 |")
            md_lines.append("|------|----------|----------|--------|-----------|-----------|----------|")
            for r in deltas[:20]:
                md_lines.append(f"| {r['Pass']} | {r['A_DrawCall']:,} | {r['B_DrawCall']:,} | {_fmt_delta(r['DrawCall_变化'])} | {r['A_面数']:,} | {r['B_面数']:,} | {_fmt_delta(r['面数_变化'])} |")

    _pass_table(pass_deltas, "按 Pass 对比 (Top 20)")

    md_lines.append("\n## 按资源分类对比\n")
    if has_timing:
        md_lines.append(f"| 资源分类 | {name_a} DC | {name_b} DC | DC变化 | {name_a} 耗时 | {name_b} 耗时 | 耗时变化 |")
        md_lines.append("|---------|----------|----------|--------|-----------|-----------|----------|")
        for r in resource_deltas[:20]:
            md_lines.append(f"| {r['资源分类']} | {r['A_DrawCall']:,} | {r['B_DrawCall']:,} | {_fmt_delta(r['DrawCall_变化'])} | {r['A_耗时(ms)']:.3f} | {r['B_耗时(ms)']:.3f} | {r['耗时变化(ms)']:+.3f} |")
    else:
        md_lines.append(f"| 资源分类 | {name_a} DC | {name_b} DC | DC变化 | {name_a} 面数 | {name_b} 面数 | 面数变化 |")
        md_lines.append("|---------|----------|----------|--------|-----------|-----------|----------|")
        for r in resource_deltas[:20]:
            md_lines.append(f"| {r['资源分类']} | {r['A_DrawCall']:,} | {r['B_DrawCall']:,} | {_fmt_delta(r['DrawCall_变化'])} | {r['A_面数']:,} | {r['B_面数']:,} | {_fmt_delta(r['面数_变化'])} |")

    report_path = out_dir / "compare_report.md"
    report_path.write_text("\n".join(md_lines), encoding="utf-8")
