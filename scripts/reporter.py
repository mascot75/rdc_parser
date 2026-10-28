# -*- coding: utf-8 -*-
"""输出：CSV 写入、汇总统计、终端摘要打印。"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from dataclasses import fields
from pathlib import Path

from .models import DrawRec, HEADER_ZH, MeshStat, detect_lod, aggregate_mesh_stats
from .classifier import short_marker


def write_details_csv(path: Path, records: list[DrawRec]) -> None:
    """写入每个 DrawCall 的详细 CSV。"""
    field_names = [f.name for f in fields(DrawRec)]
    headers = [HEADER_ZH.get(k, k) for k in field_names]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in records:
            w.writerow([getattr(r, k) for k in field_names])


def summarise_by(records: list[DrawRec], key_field: str, timing: dict[int, float] | None = None) -> list[dict]:
    """按指定字段分组汇总，返回 DC 数、混合 DC、不写深度 DC、面数等统计。

    当 timing 不为 None 时，额外返回 time_ms（耗时）和 time_pct（耗时占比）。
    """
    groups: dict[str, list[DrawRec]] = defaultdict(list)
    for r in records:
        groups[getattr(r, key_field)].append(r)
    total_time = sum(timing.get(r.event, 0.0) for r in records) if timing else 0.0
    rows = []
    for key, recs in sorted(groups.items(), key=lambda x: -len(x[1])):
        # ib_faces: IB 容量面数之和（buffer 资产属性）
        ib_faces = sum(int(float(r.ib_capacity_faces_estimate)) for r in recs if r.ib_capacity_faces_estimate)
        # draw_faces: GPU 实际渲染面数之和（性能开销）
        draw_faces = sum(int(r.face_estimate) for r in recs if r.face_known and r.face_estimate)
        row = {
            key_field: key,
            "drawcalls": len(recs),
            "blended_drawcalls": sum(1 for r in recs if r.blend),
            "no_depth_write_drawcalls": sum(1 for r in recs if not r.depth_write),
            "draw_faces": draw_faces,
            "ib_faces": ib_faces,
            "top_shaders": ", ".join(k for k, _ in Counter(r.ps for r in recs).most_common(3)),
        }
        if timing is not None:
            t = sum(timing.get(r.event, 0.0) for r in recs)
            row["time_ms"] = round(t, 2)
            row["time_pct"] = round(t / total_time * 100, 1) if total_time > 0 else 0.0
        rows.append(row)
    return rows


def write_summary_csv(path: Path, rows: list[dict]) -> None:
    """写入汇总 CSV。"""
    if not rows:
        return
    headers = list(rows[0].keys())
    zh = {
        "mesh_type": "Mesh渲染类型", "pass_name": "Pass简称",
        "asset_module": "资产模块", "resource_category": "资源分类",
        "resource_type": "资源类型", "ps": "PixelShader",
        "drawcalls": "DrawCall数量",
        "blended_drawcalls": "混合DC数", "no_depth_write_drawcalls": "不写深度DC数",
        "ib_faces": "IndexBuffer面数",
        "draw_faces": "Draw参数面数",
        "top_shaders": "主要Shader",
        "time_ms": "耗时(ms)", "time_pct": "耗时占比(%)",
    }
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writerow({h: zh.get(h, h) for h in headers})
        w.writerows(rows)


def print_summary(records: list[DrawRec], platform_info: str) -> None:
    """打印终端摘要。"""
    total = len(records)
    if total == 0:
        print("\n未解析到任何 DrawCall。")
        return

    total_faces = sum(int(float(r.ib_capacity_faces_estimate)) for r in records if r.ib_capacity_faces_estimate)
    total_draw_faces = sum(int(r.face_estimate) for r in records if r.face_known and r.face_estimate)
    blended = sum(1 for r in records if r.blend)
    no_depth = sum(1 for r in records if not r.depth_write)

    print(f"\n{'='*60}")
    if platform_info:
        print(f"平台: {platform_info}")
    print(f"DrawCall 总数: {total}")
    print(f"Draw参数面数 总计: {total_draw_faces:,}")
    print(f"IB面数 总计: {total_faces:,}")
    print(f"混合 DC: {blended}")
    print(f"不写深度 DC: {no_depth}")

    print(f"\n--- 按 Pass ---")
    for row in summarise_by(records, "pass_name")[:15]:
        print(f"  {row['pass_name']:40s}  DC={row['drawcalls']:5d}  Draw={row['draw_faces']:>10,}  IB={row['ib_faces']:>10,}")

    print(f"\n--- Top Shader ---")
    for shader, cnt in Counter(r.ps for r in records).most_common(10):
        print(f"  {shader:60s}  {cnt:5d}")

    print(f"\n--- Top Marker ---")
    for marker, cnt in Counter(short_marker(r.marker) for r in records).most_common(10):
        print(f"  {marker:60s}  {cnt:5d}")

    print(f"\n--- 按资产模块 ---")
    for row in summarise_by(records, "asset_module"):
        label = row['asset_module'] or "(未匹配)"
        print(f"  {label:40s}  DC={row['drawcalls']:5d}  Draw={row['draw_faces']:>10,}  IB={row['ib_faces']:>10,}")

    print(f"\n--- 按资源分类 ---")
    for row in summarise_by(records, "resource_category")[:15]:
        label = row['resource_category'] or "(未分类)"
        print(f"  {label:40s}  DC={row['drawcalls']:5d}  Draw={row['draw_faces']:>10,}  IB={row['ib_faces']:>10,}")


# ── Mesh 详细汇总 ───────────────────────────────────────────────────


def write_mesh_detail_csv(path: Path, records: list[DrawRec]) -> None:
    """按 VB+IB 聚合，输出每个 mesh 的合并数据。"""
    mesh_stats = aggregate_mesh_stats(records)

    rows: list[dict] = []
    for s, recs in mesh_stats:
        shader = recs[0].ps if recs else ""
        passes = ", ".join(dict.fromkeys(r.pass_name for r in recs if r.pass_name))

        rows.append({
            "mesh_name": s.name,
            "resource_path": s.path,
            "resource_category": s.resource_category,
            "asset_module": s.asset_module,
            "ib_faces": s.ib_faces,
            "draw_params_faces": s.draw_params_faces,
            "draw_faces": s.combined_faces,
            "single_faces": s.single_faces,
            "instance_count": s.instance_count,
            "dc_count": s.drawcall_count,
            "shader": shader,
            "pass": passes,
        })

    zh = {
        "mesh_name": "Mesh名称", "resource_path": "资源路径",
        "resource_category": "资源分类", "asset_module": "资产模块",
        "ib_faces": "IndexBuffer面数",
        "draw_params_faces": "Draw参数面数",
        "draw_faces": "采用面数",
        "single_faces": "单实例面数",
        "instance_count": "实例数",
        "dc_count": "DrawCall数", "shader": "Shader",
        "pass": "Pass",
    }
    headers = list(zh.keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writerow(zh)
        w.writerows(rows)


# ── 按资源类型分类 Excel ────────────────────────────────────────────

def write_classification_excel(path: Path, records: list[DrawRec]) -> None:
    """按资源类型分组输出 Excel，每个类型一个工作表。

    按 VB+IB 聚合 mesh，按 resource_type 分组，按 mesh 名去重。
    只输出已分类（非"未分类"）的 mesh。
    """
    from openpyxl import Workbook

    mesh_stats = aggregate_mesh_stats(records)

    # 按 resource_type 分组，按 mesh 名去重
    type_groups: dict[str, list[MeshStat]] = defaultdict(list)
    seen_names: dict[str, set[str]] = defaultdict(set)

    for s, _recs in mesh_stats:
        if not s.resource_type:
            continue
        if s.name in seen_names[s.resource_type]:
            continue
        seen_names[s.resource_type].add(s.name)
        type_groups[s.resource_type].append(s)

    wb = Workbook()
    wb.remove(wb.active)

    headers = ["名称", "路径", "分类", "IndexBuffer面数", "Draw参数面数", "单实例面数", "采用面数", "实例数", "面数来源"]

    for type_name in sorted(type_groups):
        recs = sorted(type_groups[type_name], key=lambda s: -s.combined_faces)
        total_faces = sum(s.combined_faces for s in recs)
        # Excel sheet 名不允许 : \ / ? * [ ]，替换为 -
        safe_name = type_name.replace("/", "-").replace("\\", "-").replace(":", "-")
        sheet_name = f"{safe_name}-{total_faces:,}"
        if len(sheet_name) > 31:
            sheet_name = sheet_name[:31]

        ws = wb.create_sheet(title=sheet_name)
        ws.append(headers)
        for s in recs:
            ws.append([s.name, s.path, s.resource_category,
                        s.ib_faces, s.draw_params_faces, s.single_faces,
                        s.combined_faces, s.instance_count, s.face_source])

    wb.save(path)


# ── Shader 详细汇总 ─────────────────────────────────────────────────

def _extract_keywords(raw_ps: str) -> list[str]:
    """从原始 shader 字符串中提取关键字（shader 名之前的 token）。

    格式为 "KEYWORD1 KEYWORD2 ... Shader/Name"，
    遇到含 / 的 token（即 shader 名）时停止。
    """
    if not raw_ps:
        return []
    parts = raw_ps.strip().split()
    keywords: list[str] = []
    for token in parts:
        if "/" in token:
            break
        keywords.append(token)
    return keywords


def write_shader_detail_csv(path: Path, records: list[DrawRec]) -> None:
    """按归一化 shader 聚合，输出每个 shader 的合并数据。"""
    groups: dict[str, list[DrawRec]] = defaultdict(list)
    for r in records:
        groups[r.ps].append(r)

    rows: list[dict] = []
    for shader, recs in groups.items():
        ib_faces = sum(int(float(r.ib_capacity_faces_estimate)) for r in recs if r.ib_capacity_faces_estimate)
        draw_faces = sum(int(r.face_estimate) for r in recs if r.face_known and r.face_estimate)
        mesh_keys = {f"{r.vb0}|{r.ib}" for r in recs}
        top_passes = ", ".join(k for k, _ in Counter(r.pass_name for r in recs if r.pass_name).most_common(3))
        top_cats = ", ".join(k for k, _ in Counter(r.resource_category for r in recs if r.resource_category and r.resource_category != "未分类").most_common(3))
        all_keywords: set[str] = set()
        for r in recs:
            all_keywords.update(_extract_keywords(r.raw_ps))

        rows.append({
            "shader": shader,
            "dc_count": len(recs),
            "ib_faces": ib_faces,
            "draw_faces": draw_faces,
            "mesh_count": len(mesh_keys),
            "keywords_count": len(all_keywords),
            "top_passes": top_passes,
            "top_resource_categories": top_cats,
        })

    rows.sort(key=lambda r: -r["dc_count"])

    zh = {
        "shader": "Shader", "dc_count": "DrawCall数",
        "ib_faces": "IndexBuffer面数", "draw_faces": "Draw参数面数",
        "mesh_count": "关联Mesh数", "keywords_count": "关键字数量",
        "top_passes": "主要Pass",
        "top_resource_categories": "主要资源分类",
    }
    headers = list(zh.keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writerow(zh)
        w.writerows(rows)


# ── Shader 关键字明细 ───────────────────────────────────────────────

def write_shader_keywords_csv(path: Path, records: list[DrawRec]) -> None:
    """输出每个 shader 的关键字明细，每行一个 shader-keyword 组合。"""
    groups: dict[str, list[DrawRec]] = defaultdict(list)
    for r in records:
        groups[r.ps].append(r)

    rows: list[dict] = []
    for shader, recs in groups.items():
        kw_counter: Counter[str] = Counter()
        for r in recs:
            for kw in _extract_keywords(r.raw_ps):
                kw_counter[kw] += 1
        for kw, cnt in kw_counter.most_common():
            rows.append({
                "shader": shader,
                "keyword": kw,
                "dc_count": cnt,
            })

    zh = {"shader": "Shader", "keyword": "关键字", "dc_count": "DrawCall数"}
    headers = list(zh.keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writerow(zh)
        w.writerows(rows)


# ── 综合报告 ───────────────────────────────────────────────────────

def write_report_md(path: Path, records: list[DrawRec], platform_info: str = "", timing: dict[int, float] | None = None, resource_standard: Path | None = None) -> None:
    """生成综合 Markdown 分析报告，包含：概览、按类型/模块/分类/Pass 汇总、Shader 分析、Mesh 分析。"""
    from datetime import datetime

    total = len(records)
    if total == 0:
        raise ValueError("没有绘制记录，拒绝生成空 Markdown 报告")

    from .gpu_timing import validate_timing_alignment
    validate_timing_alignment(records, timing)

    total_ib = sum(int(float(r.ib_capacity_faces_estimate)) for r in records if r.ib_capacity_faces_estimate)
    total_draw = sum(int(r.face_estimate) for r in records if r.face_known and r.face_estimate)
    blended = sum(1 for r in records if r.blend)
    no_depth = sum(1 for r in records if not r.depth_write)

    # 计算总耗时
    total_gpu_time = 0.0
    if timing:
        total_gpu_time = sum(timing.get(r.event, 0.0) for r in records)

    mesh_stats = aggregate_mesh_stats(records)
    mesh_keys = {f"{r.vb0}|{r.ib}" for r in records}
    shader_set = {r.ps for r in records}

    md: list[str] = []
    md.append('# RenderDoc 抓帧分析报告')
    md.append('')
    md.append(f'> 生成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    if platform_info:
        md.append(f'> 平台: {platform_info}')
    md.append('')
    md.append('---')
    md.append('')

    # 一、概览
    md.append('## 一、分析概览')
    md.append('')
    md.append('| 指标 | 数值 |')
    md.append('|------|------|')
    md.append(f'| DrawCall 总数 | {total:,} |')
    md.append(f'| 面数可确定的 DC | {sum(r.face_known for r in records):,} |')
    md.append(f'| 面数未知的 DC | {sum(not r.face_known for r in records):,} |')
    md.append(f'| 已知 IB 容量参考之和 | {total_ib:,} |')
    md.append(f'| 有 IB 容量参考的 DC | {sum(bool(r.ib_capacity_faces_estimate) for r in records):,} |')
    md.append(f'| Draw参数面数 总计 | {total_draw:,} |')
    md.append(f'| Mesh 数量 | {len(mesh_keys):,} |')
    md.append(f'| Shader 数量 | {len(shader_set)} |')
    md.append(f'| 混合 DC | {blended:,} |')
    md.append(f'| 不写深度 DC | {no_depth:,} |')
    if timing:
        md.append(f'| 已匹配绘制事件回放耗时之和 | {total_gpu_time:.2f} ms |')
        timed_count = sum(1 for r in records if timing.get(r.event, 0.0) > 0)
        md.append(f'| 有耗时 DC | {timed_count:,} |')
    md.append('')
    md.append('> 面数总计仅覆盖已知记录，未知项不是零；IB 参考仅汇总已知容量，不参与采用面数，无参考记录不代表容量为零。Mesh 按 Buffer 组合近似分组，不能等同于项目资产数。GPU 值为本机回放事件耗时，不是游戏帧时间；占比分母为已匹配绘制事件之和，不含其他计算、拷贝事件。')
    md.append('')
    md.append('---')
    md.append('')

    # 二、按 Pass（渲染管线结构）
    md.append('## 二、按 Pass')
    md.append('')
    if timing:
        md.append('| Pass | DC | 混合DC | 不写深度DC | IB面数 | Draw面数 | 耗时(ms) | 耗时占比 |')
        md.append('|------|-----|--------|-----------|--------|----------|---------|---------|')
        for row in summarise_by(records, "pass_name", timing)[:15]:
            md.append(f"| {row['pass_name']} | {row['drawcalls']:,} | {row['blended_drawcalls']:,} | {row['no_depth_write_drawcalls']:,} | {row['ib_faces']:,} | {row['draw_faces']:,} | {row['time_ms']:.2f} | {row['time_pct']:.1f}% |")
    else:
        md.append('| Pass | DC | 混合DC | 不写深度DC | IB面数 | Draw面数 |')
        md.append('|------|-----|--------|-----------|--------|----------|')
        for row in summarise_by(records, "pass_name")[:15]:
            md.append(f"| {row['pass_name']} | {row['drawcalls']:,} | {row['blended_drawcalls']:,} | {row['no_depth_write_drawcalls']:,} | {row['ib_faces']:,} | {row['draw_faces']:,} |")
    md.append('')
    md.append('---')
    md.append('')

    # 三、Shader 分析
    md.append('## 三、Shader 分析')
    md.append('')
    md.append('### 3.1 Shader 总览 TOP 20')
    md.append('')

    shader_groups: dict[str, list[DrawRec]] = defaultdict(list)
    for r in records:
        shader_groups[r.ps].append(r)

    if timing:
        md.append('| Shader | DC | IB面数 | Draw面数 | 耗时(ms) | 耗时占比 | 主要Pass |')
        md.append('|--------|-----|--------|----------|---------|---------|---------|')

        shader_rows: list[tuple[str, int, int, int, float, str]] = []
        for shader, recs in shader_groups.items():
            s_ib = sum(int(float(r.ib_capacity_faces_estimate)) for r in recs if r.ib_capacity_faces_estimate)
            s_draw = sum(int(r.face_estimate) for r in recs if r.face_known and r.face_estimate)
            s_time = sum(timing.get(r.event, 0.0) for r in recs)
            top_pass = ", ".join(k for k, _ in Counter(r.pass_name for r in recs if r.pass_name).most_common(2))
            shader_rows.append((shader, len(recs), s_ib, s_draw, s_time, top_pass))

        shader_rows.sort(key=lambda x: -x[4])
        for shader, cnt, s_ib, s_draw, s_time, top_pass in shader_rows[:20]:
            pct = (s_time / total_gpu_time * 100) if total_gpu_time > 0 else 0
            passes = top_pass[:40] + ('...' if len(top_pass) > 40 else '')
            md.append(f"| {shader} | {cnt} | {s_ib:,} | {s_draw:,} | {s_time:.2f} | {pct:.1f}% | {passes} |")
    else:
        md.append('| Shader | DC | IB面数 | Draw面数 | Mesh数 | 关键字数 | 主要Pass |')
        md.append('|--------|-----|--------|----------|--------|---------|---------|')

        shader_rows: list[tuple[str, int, int, int, int, int, str]] = []
        for shader, recs in shader_groups.items():
            s_ib = sum(int(float(r.ib_capacity_faces_estimate)) for r in recs if r.ib_capacity_faces_estimate)
            s_draw = sum(int(r.face_estimate) for r in recs if r.face_known and r.face_estimate)
            s_mesh = len({f"{r.vb0}|{r.ib}" for r in recs})
            s_kw = len({kw for r in recs for kw in _extract_keywords(r.raw_ps)})
            top_pass = ", ".join(k for k, _ in Counter(r.pass_name for r in recs if r.pass_name).most_common(2))
            shader_rows.append((shader, len(recs), s_ib, s_draw, s_kw, s_mesh, top_pass))

        shader_rows.sort(key=lambda x: -x[1])
        for shader, cnt, s_ib, s_draw, s_kw, s_mesh, top_pass in shader_rows[:20]:
            passes = top_pass[:40] + ('...' if len(top_pass) > 40 else '')
            md.append(f"| {shader} | {cnt} | {s_ib:,} | {s_draw:,} | {s_mesh} | {s_kw} | {passes} |")
    md.append('')
    md.append('---')
    md.append('')

    # 四、Mesh 分析
    md.append('## 四、Mesh 分析')
    md.append('')
    md.append('### 4.1 采用面数最高 TOP 20')
    md.append('')

    if timing:
        md.append('| Mesh | 资源分类 | 资产模块 | 采用面数 | 实例数 | DC | 耗时(ms) | 耗时占比 |')
        md.append('|------|---------|---------|---------|--------|-----|---------|---------|')
        for s, recs in mesh_stats[:20]:
            name = s.name[:30]
            mt = sum(timing.get(r.event, 0.0) for r in recs)
            pct = (mt / total_gpu_time * 100) if total_gpu_time > 0 else 0
            md.append(f"| {name} | {s.resource_category} | {s.asset_module} | {s.combined_faces:,} | {s.instance_count} | {s.drawcall_count} | {mt:.2f} | {pct:.1f}% |")
    else:
        md.append('| Mesh | 资源分类 | 资产模块 | IB面数 | Draw参数面数 | 单实例面数 | 采用面数 | 实例数 | DC |')
        md.append('|------|---------|---------|--------|------------|-----------|---------|--------|-----|')
        for s, _recs in mesh_stats[:20]:
            name = s.name[:30]
            md.append(f"| {name} | {s.resource_category} | {s.asset_module} | {s.ib_faces:,} | {s.draw_params_faces:,} | {s.single_faces:,} | {s.combined_faces:,} | {s.instance_count} | {s.drawcall_count} |")
    md.append('')
    md.append('---')
    md.append('')

    # 五、按资产模块
    md.append('## 五、按资产模块')
    md.append('')
    if timing:
        md.append('| 模块 | DC | 混合DC | 不写深度DC | IB面数 | Draw面数 | 耗时(ms) | 耗时占比 |')
        md.append('|------|-----|--------|-----------|--------|----------|---------|---------|')
        for row in summarise_by(records, "asset_module", timing):
            label = row['asset_module'] or '(未匹配)'
            md.append(f"| {label} | {row['drawcalls']:,} | {row['blended_drawcalls']:,} | {row['no_depth_write_drawcalls']:,} | {row['ib_faces']:,} | {row['draw_faces']:,} | {row['time_ms']:.2f} | {row['time_pct']:.1f}% |")
    else:
        md.append('| 模块 | DC | 混合DC | 不写深度DC | IB面数 | Draw面数 |')
        md.append('|------|-----|--------|-----------|--------|----------|')
        for row in summarise_by(records, "asset_module"):
            label = row['asset_module'] or '(未匹配)'
            md.append(f"| {label} | {row['drawcalls']:,} | {row['blended_drawcalls']:,} | {row['no_depth_write_drawcalls']:,} | {row['ib_faces']:,} | {row['draw_faces']:,} |")
    md.append('')
    md.append('---')
    md.append('')

    # 六、按资源分类
    md.append('## 六、按资源分类')
    md.append('')
    if timing:
        md.append('| 分类 | DC | 混合DC | 不写深度DC | IB面数 | Draw面数 | 耗时(ms) | 耗时占比 |')
        md.append('|------|-----|--------|-----------|--------|----------|---------|---------|')
        for row in summarise_by(records, "resource_category", timing)[:15]:
            label = row['resource_category'] or '(未分类)'
            md.append(f"| {label} | {row['drawcalls']:,} | {row['blended_drawcalls']:,} | {row['no_depth_write_drawcalls']:,} | {row['ib_faces']:,} | {row['draw_faces']:,} | {row['time_ms']:.2f} | {row['time_pct']:.1f}% |")
    else:
        md.append('| 分类 | DC | 混合DC | 不写深度DC | IB面数 | Draw面数 |')
        md.append('|------|-----|--------|-----------|--------|----------|')
        for row in summarise_by(records, "resource_category")[:15]:
            label = row['resource_category'] or '(未分类)'
            md.append(f"| {label} | {row['drawcalls']:,} | {row['blended_drawcalls']:,} | {row['no_depth_write_drawcalls']:,} | {row['ib_faces']:,} | {row['draw_faces']:,} |")
    md.append('')
    md.append('---')
    md.append('')

    # 七、减面分析概要
    md.append('## 七、减面分析')
    md.append('')

    if resource_standard and resource_standard.exists():
        from .poly_analyzer import parse_poly_standards, match_poly_standard, _dedup_by_name

        standards = parse_poly_standards(resource_standard)
        if standards:
            # 聚合 mesh 级别数据
            mesh_stats_for_poly = aggregate_mesh_stats(records)
            results = []
            for s, recs in mesh_stats_for_poly:
                std = match_poly_standard(s.name, s.path, standards)
                if std and std.face_standard:
                    exceed = s.single_faces - std.face_standard if s.single_faces > std.face_standard else 0
                    exceed_ratio = (exceed / std.face_standard * 100) if std.face_standard > 0 and exceed > 0 else 0
                    priority_score = exceed_ratio * s.combined_faces / 100
                    results.append({
                        "name": s.name,
                        "resource_type": s.resource_type,
                        "resource_category": s.resource_category,
                        "single_faces": s.single_faces,
                        "face_standard": std.face_standard,
                        "exceed_faces": exceed,
                        "exceed_ratio": exceed_ratio,
                        "instance_count": s.instance_count,
                        "total_faces": s.combined_faces,
                        "priority_score": priority_score,
                    })

            exceeded = [r for r in results if r["exceed_faces"] > 0]
            exceeded_dedup = _dedup_by_name(exceeded)

            if exceeded_dedup:
                for r in exceeded_dedup:
                    r["_reducible"] = max(0, r["total_faces"] - r["face_standard"] * r["instance_count"])
                total_reducible = sum(r["_reducible"] for r in exceeded_dedup)

                md.append(f'超出面数标准的 mesh: **{len(exceeded_dedup)}** 个')
                if total_reducible > 0:
                    md.append(f'预计可减少总面数: **{total_reducible:,}** 面')
                md.append('')

                # 综合优先级 TOP 10
                md.append('### 7.1 高优先级减面建议 TOP 10')
                md.append('')
                md.append('| Mesh名称 | 类型 | 面数 | 标准 | 超出比例 | 实例数 | 总面数 | 预计减少 |')
                md.append('|----------|------|------|------|----------|--------|--------|----------|')
                top10 = sorted(exceeded_dedup, key=lambda r: -r["priority_score"])[:10]
                for r in top10:
                    md.append(
                        f"| {r['name'][:30]} | {r['resource_type']} | {r['single_faces']:,} "
                        f"| {r['face_standard']:,} | {r['exceed_ratio']:.1f}% "
                        f"| {r['instance_count']} | {r['total_faces']:,} | {r['_reducible']:,} |"
                    )
                md.append('')
            else:
                md.append('所有 mesh 均在面数标准内。')
                md.append('')
        else:
            md.append('未找到面数标准定义。')
            md.append('')
    else:
        md.append('未提供资源分类/面数标准，无法判定项目资源是否超标。')
        md.append('')

    path.write_text("\n".join(md), encoding="utf-8")
