# -*- coding: utf-8 -*-
"""减面分析：将 classified.csv 聚合为 mesh 级别数据，匹配面数标准，输出超标报告。"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import DrawRec, HEADER_ZH, aggregate_mesh_stats
from .resource import (
    ResourceRule, KeywordRule,
    load_mesh_export_csv, parse_resource_standard_md, parse_keyword_rules_md,
    classify_resource_path, normalize_asset_path, strip_mesh_suffix,
)


@dataclass
class PolyStandard:
    resource_type: str
    resource_category: str
    path_pattern: str
    keyword_groups: list[list[str]]
    face_standard: int | None



def parse_poly_standards(md_path: Path | None) -> list[PolyStandard]:
    """解析资源分类标准.md 中的面数标准。"""
    if not md_path or not md_path.exists():
        return []
    text = md_path.read_text(encoding="utf-8-sig", errors="ignore")
    standards: list[PolyStandard] = []
    in_keyword_section = False
    for line in text.splitlines():
        line = line.strip()
        if "关键词规则" in line:
            in_keyword_section = True
            continue
        if not line.startswith("|") or "---" in line:
            continue
        if in_keyword_section:
            break
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 5 or cols[0] == "类型":
            continue
        resource_type = cols[0]
        resource_category = cols[1]
        path_pattern = cols[2].replace("\\", "").lower()
        keyword_text = cols[3].replace("\\", "").strip()
        face_standard_str = cols[4].strip()

        if keyword_text.lower() in ("<br />", "<br>", "<br/>"):
            keyword_text = ""

        keyword_groups = []
        if keyword_text and keyword_text != "不限":
            for group in keyword_text.split("/"):
                options = [x.strip().strip("`").lower() for x in group.split("|") if x.strip()]
                if options:
                    keyword_groups.append(options)

        face_standard = None
        if face_standard_str.isdigit():
            face_standard = int(face_standard_str)

        standards.append(PolyStandard(
            resource_type=resource_type,
            resource_category=resource_category,
            path_pattern=path_pattern,
            keyword_groups=keyword_groups,
            face_standard=face_standard,
        ))
    return standards


def _standard_score(std: PolyStandard, name_lower: str) -> tuple[int, int, int]:
    """评分（用于多条标准匹配时选最优）：
    1. LOD 变体匹配优先（mesh 名含 _low 且标准关键字也含 _low → +2 分）
    2. 关键字组数最少（规则越精确越好）
    3. 关键字总长度最短（避免过度匹配）
    """
    low_pos = name_lower.rfind("_low")
    mid_pos = name_lower.rfind("_mid")
    variant_score = 0
    flat_keywords = [kw for group in std.keyword_groups for kw in group]
    if low_pos >= 0 or mid_pos >= 0:
        last_variant = "_low" if low_pos > mid_pos else "_mid"
        if last_variant == "_low" and any("_low" in kw for kw in flat_keywords):
            variant_score = 2
        elif last_variant == "_mid" and any("_mid" in kw for kw in flat_keywords):
            variant_score = 2
        elif any("_low" in kw or "_mid" in kw for kw in flat_keywords):
            variant_score = 1
    return (
        variant_score,
        len(std.keyword_groups),
        sum(len(kw) for kw in flat_keywords),
    )


def match_poly_standard(
    mesh_name: str, path: str, standards: list[PolyStandard],
) -> PolyStandard | None:
    """匹配面数标准。"""
    name_lower = mesh_name.lower()
    path_lower = path.lower()

    matches: list[PolyStandard] = []
    for std in standards:
        if std.path_pattern and std.path_pattern in path_lower:
            if not std.keyword_groups or all(
                any(opt in name_lower for opt in group) for group in std.keyword_groups
            ):
                matches.append(std)

    if not matches:
        return None

    return max(matches, key=lambda s: _standard_score(s, name_lower))


def write_poly_csv(path: Path, rows: list[dict]) -> None:
    """写入减面分析 CSV。"""
    if not rows:
        return
    headers = list(rows[0].keys())
    zh = {
        "name": "名称", "path": "路径", "lod_level": "LOD等级",
        "single_faces": "单个面数", "instance_count": "实例数",
        "total_faces": "总面数", "drawcall_count": "DrawCall数",
        "resource_type": "类型", "resource_category": "分类",
        "face_standard": "面数标准", "exceed_faces": "超出面数",
        "exceed_ratio": "超出比例%", "priority_score": "优先级分数",
    }
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writerow({h: zh.get(h, h) for h in headers})
        w.writerows(rows)


def run_poly_analysis(records: list[DrawRec], out_dir: Path, md_path: Path) -> None:
    """执行减面分析。"""
    mesh_stats = aggregate_mesh_stats(records)
    standards = parse_poly_standards(md_path)

    print(f"聚合 {len(mesh_stats)} 个 mesh")
    print(f"加载 {len(standards)} 条面数标准")

    results = []
    unmatched = []

    for stat, _recs in mesh_stats:
        std = match_poly_standard(stat.name, stat.path, standards) if stat.path else None

        if std and std.face_standard is not None:
            # 用 single_faces（GPU 实际绘制的单实例面数）与标准对比
            # 注意：single_faces 可能小于 mesh 资产面数（GPU 索引裁剪）
            exceed = stat.single_faces - std.face_standard
            exceed_ratio = (exceed / std.face_standard * 100) if std.face_standard > 0 else 0
            # 优先级 = 超出比例 × 总面数 / 1000，综合考虑单面超标程度和全局影响面
            priority_score = exceed_ratio * stat.total_faces / 1000 if exceed > 0 else 0

            results.append({
                "name": stat.name,
                "path": stat.path,
                "lod_level": stat.lod_level,
                "single_faces": stat.single_faces,
                "instance_count": stat.instance_count,
                "total_faces": stat.total_faces,
                "drawcall_count": stat.drawcall_count,
                "resource_type": std.resource_type,
                "resource_category": std.resource_category,
                "face_standard": std.face_standard,
                "exceed_faces": exceed,
                "exceed_ratio": round(exceed_ratio, 1),
                "priority_score": round(priority_score, 1),
            })
        else:
            unmatched.append({
                "name": stat.name,
                "path": stat.path,
                "lod_level": stat.lod_level,
                "single_faces": stat.single_faces,
                "instance_count": stat.instance_count,
                "total_faces": stat.total_faces,
                "drawcall_count": stat.drawcall_count,
                "resource_type": stat.resource_type,
                "resource_category": stat.resource_category,
            })

    # 输出完整分析
    results.sort(key=lambda r: -r["total_faces"])
    write_poly_csv(out_dir / "poly_analysis_full.csv", results)
    print(f"完整分析: {out_dir / 'poly_analysis_full.csv'}")

    # 输出超标 mesh（按 name 去重）
    exceeded = _dedup_by_name([r for r in results if r["exceed_faces"] > 0])
    if exceeded:
        exceeded.sort(key=lambda r: -r["priority_score"])
        write_poly_csv(out_dir / "poly_exceeded.csv", exceeded)
        print(f"超标 mesh: {out_dir / 'poly_exceeded.csv'} ({len(exceeded)} 个)")

        # 终端输出 TOP 20
        print(f"\n{'='*60}")
        print("减面建议 TOP 20（按优先级排序）")
        print(f"{'='*60}")
        for i, r in enumerate(exceeded[:20], 1):
            print(f"{i}. {r['name']}")
            print(f"   类型: {r['resource_type']} - {r['resource_category']}")
            print(f"   面数: {r['single_faces']:,} (标准: {r['face_standard']:,}, 超出: {r['exceed_ratio']:.1f}%)")
            print(f"   实例: {r['instance_count']} (总面数: {r['total_faces']:,})")

    # 输出未匹配（按 name 去重）
    if unmatched:
        unmatched = _dedup_by_name(unmatched)
        unmatched.sort(key=lambda r: -r["total_faces"])
        write_poly_csv(out_dir / "poly_unmatched.csv", unmatched)
        print(f"未匹配: {out_dir / 'poly_unmatched.csv'} ({len(unmatched)} 个)")

    # 统计摘要
    print(f"\n{'='*60}")
    print("统计摘要")
    print(f"{'='*60}")
    print(f"mesh 总数: {len(mesh_stats)}")
    print(f"匹配标准: {len(results)}")
    print(f"超出标准: {len(exceeded)}")
    print(f"未匹配: {len(unmatched)}")

    # Markdown 报告
    write_poly_report(out_dir / "poly_report.md", results, unmatched)
    print(f"报告: {out_dir / 'poly_report.md'}")


# ── Markdown 报告 ────────────────────────────────────────────────────

def write_poly_report(
    path: Path,
    results: list[dict],
    unmatched: list[dict],
) -> None:
    """生成 Markdown 减面报告。"""
    exceeded = [r for r in results if r["exceed_faces"] > 0]
    exceeded_dedup = _dedup_by_name(exceeded)
    results_dedup = _dedup_by_name(results)

    total_matched_faces = sum(r["total_faces"] for r in results_dedup)
    total_unmatched_faces = sum(r["total_faces"] for r in unmatched)
    total_all_faces = total_matched_faces + total_unmatched_faces
    total_poly_exceeded = sum(r["total_faces"] for r in exceeded_dedup)

    for r in exceeded_dedup:
        r["_reducible"] = max(0, r["total_faces"] - r["face_standard"] * r["instance_count"])
    total_reducible = sum(r["_reducible"] for r in exceeded_dedup)

    md: list[str] = []
    md.append("# Mesh 减面建议报告")
    md.append("")
    md.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md.append("")
    md.append("---")
    md.append("")

    # 一、分析概览
    md.append("## 一、分析概览")
    md.append("")
    md.append(f"- **超出面数标准的mesh数量**: {len(exceeded_dedup)} 个")
    md.append(f"- **未匹配到任何标准的mesh数量**: {len(unmatched)}")
    md.append(f"- **全部mesh总面数**: {total_all_faces:,} 面（匹配 {total_matched_faces:,} + 未匹配 {total_unmatched_faces:,}）")
    md.append(f"- **超标mesh总面数**: {total_poly_exceeded:,} 面")
    if total_reducible > 0:
        md.append(f"- **预计可减少总面数**: {total_reducible:,} 面")
        pct_exceeded = total_reducible / total_poly_exceeded * 100 if total_poly_exceeded else 0
        pct_all = total_reducible / total_all_faces * 100 if total_all_faces else 0
        md.append(f"- **减面潜力占比**: 占超标 {pct_exceeded:.1f}%，占全部 {pct_all:.1f}%")
    md.append("")
    md.append("---")
    md.append("")

    # 二、高优先级减面建议
    md.append("## 二、高优先级减面建议")
    md.append("")

    # 2.1 综合优先级 TOP 30
    md.append("### 2.1 综合优先级 TOP 30（超出比例 × 总面数）")
    md.append("")
    md.append("| 排名 | Mesh名称 | 类型 | 面数 | 标准 | 超出比例 | 实例数 | 总面数 | 预计减少面数 |")
    md.append("|------|----------|------|------|------|----------|--------|--------|------------|")
    top30 = sorted(exceeded_dedup, key=lambda r: -r["priority_score"])[:30]
    for i, r in enumerate(top30, 1):
        md.append(
            f"| {i} | {r['name']} | {r['resource_type']} - {r['resource_category']} "
            f"| {r['single_faces']:,} | {r['face_standard']:,} | {r['exceed_ratio']:.1f}% "
            f"| {r['instance_count']} | {r['total_faces']:,} | {r['_reducible']:,} |"
        )
    md.append("")
    md.append("---")
    md.append("")

    # 2.2 单个体面数最高 TOP 20
    md.append("### 2.2 单个体面数最高 TOP 20")
    md.append("")
    md.append("| 排名 | Mesh名称 | 类型 | 面数 | 标准 | 超出比例 | 实例数 | 总面数 | 预计减少面数 |")
    md.append("|------|----------|------|------|------|----------|--------|--------|------------|")
    for i, r in enumerate(sorted(exceeded_dedup, key=lambda r: -r["single_faces"])[:20], 1):
        md.append(
            f"| {i} | {r['name']} | {r['resource_type']} - {r['resource_category']} "
            f"| {r['single_faces']:,} | {r['face_standard']:,} | {r['exceed_ratio']:.1f}% "
            f"| {r['instance_count']} | {r['total_faces']:,} | {r['_reducible']:,} |"
        )
    md.append("")
    md.append("---")
    md.append("")

    # 2.3 总面数最高 TOP 20
    md.append("### 2.3 总面数最高 TOP 20（仅超标mesh）")
    md.append("")
    md.append("| 排名 | Mesh名称 | 类型 | 面数 | 标准 | 超出比例 | 实例数 | 总面数 | 预计减少面数 |")
    md.append("|------|----------|------|------|------|----------|--------|--------|------------|")
    for i, r in enumerate(sorted(exceeded_dedup, key=lambda r: -r["total_faces"])[:20], 1):
        md.append(
            f"| {i} | {r['name']} | {r['resource_type']} - {r['resource_category']} "
            f"| {r['single_faces']:,} | {r['face_standard']:,} | {r['exceed_ratio']:.1f}% "
            f"| {r['instance_count']} | {r['total_faces']:,} | {r['_reducible']:,} |"
        )
    md.append("")
    md.append("---")
    md.append("")

    # 三、按类型/分类统计
    md.append("## 三、按类型/分类统计")
    md.append("")

    # 3.1 按类型
    md.append("### 3.1 按类型统计")
    md.append("")
    md.append("| 类型 | 超标数量 | 平均面数 | 最高面数 | 总面数 | 平均超出比例 | 预计减少面数 |")
    md.append("|------|----------|----------|----------|--------|--------------|------------|")
    type_groups: dict[str, list[dict]] = defaultdict(list)
    for r in exceeded_dedup:
        type_groups[r["resource_type"]].append(r)
    for type_name, recs in sorted(type_groups.items(), key=lambda x: -sum(r["_reducible"] for r in x[1])):
        faces = [r["single_faces"] for r in recs]
        md.append(
            f"| {type_name} | {len(recs)} | {sum(faces)//len(faces):,} | {max(faces):,} "
            f"| {sum(r['total_faces'] for r in recs):,} "
            f"| {sum(r['exceed_ratio'] for r in recs)/len(recs):.1f}% "
            f"| {sum(r['_reducible'] for r in recs):,} |"
        )
    md.append("")
    md.append("---")
    md.append("")

    # 3.2 按分类
    md.append("### 3.2 按分类统计")
    md.append("")
    md.append("| 分类 | 超标数量 | 平均面数 | 最高面数 | 总面数 | 平均超出比例 | 预计减少面数 |")
    md.append("|------|----------|----------|----------|--------|--------------|------------|")
    cat_groups: dict[str, list[dict]] = defaultdict(list)
    for r in exceeded_dedup:
        cat_groups[r["resource_category"]].append(r)
    for cat_name, recs in sorted(cat_groups.items(), key=lambda x: -sum(r["_reducible"] for r in x[1])):
        faces = [r["single_faces"] for r in recs]
        md.append(
            f"| {cat_name} | {len(recs)} | {sum(faces)//len(faces):,} | {max(faces):,} "
            f"| {sum(r['total_faces'] for r in recs):,} "
            f"| {sum(r['exceed_ratio'] for r in recs)/len(recs):.1f}% "
            f"| {sum(r['_reducible'] for r in recs):,} |"
        )
    md.append("")
    md.append("---")
    md.append("")

    # 四、未匹配 Mesh
    md.append("## 四、未匹配到标准的Mesh")
    md.append("")
    no_path = [r for r in unmatched if not r.get("path")]
    has_path = [r for r in unmatched if r.get("path")]
    md.append(f"- **无路径**: {len(no_path)} 个")
    md.append(f"- **有路径但无规则匹配**: {len(has_path)} 个")
    md.append("")

    if no_path:
        md.append("### 4.1 无路径的Mesh（TOP 20）")
        md.append("")
        md.append("| 排名 | Mesh名称 | 单个面数 | 总面数 | 实例数 |")
        md.append("|------|----------|----------|--------|--------|")
        for i, r in enumerate(sorted(no_path, key=lambda x: -x["total_faces"])[:20], 1):
            md.append(f"| {i} | {r['name']} | {r['single_faces']:,} | {r['total_faces']:,} | {r['instance_count']} |")
        md.append("")

    if has_path:
        md.append("### 4.2 有路径但无规则匹配（TOP 20）")
        md.append("")
        md.append("| 排名 | Mesh名称 | 路径 | 单个面数 | 总面数 | 实例数 |")
        md.append("|------|----------|------|----------|--------|--------|")
        for i, r in enumerate(sorted(has_path, key=lambda x: -x["total_faces"])[:20], 1):
            md.append(f"| {i} | {r['name']} | {r['path']} | {r['single_faces']:,} | {r['total_faces']:,} | {r['instance_count']} |")
        md.append("")

    path.write_text("\n".join(md), encoding="utf-8-sig")


def _dedup_by_name(records: list[dict]) -> list[dict]:
    """按 name 去重，保留第一条。"""
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in records:
        if r["name"] not in seen:
            deduped.append(r)
            seen.add(r["name"])
    return deduped

