# -*- coding: utf-8 -*-
"""CLI 子命令架构：convert / parse / classify / report / poly / run / html / compare / batch / timing。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .converter import ensure_xml, extract_platform_info
from .analyzer import analyze, write_drawcalls_csv, load_drawcalls_csv
from .classify_step import classify_records, write_classified_csv, load_classified_csv
from .reporter import summarise_by, write_summary_csv, print_summary, write_mesh_detail_csv, write_shader_detail_csv, write_shader_keywords_csv, write_report_md, write_classification_excel
from .shader import load_shader_list_csv, configure_shader_name_index
from .resource import load_mesh_export_csv, parse_resource_standard_md, parse_keyword_rules_md, ResourceMatcher
from .thumbnail import export_rdc_capture_thumbnail, resolve_rdc_for_thumbnail
from .poly_analyzer import run_poly_analysis
from .html_report import write_html_report
from .compare import compare_records
from .batch import run_batch
from .gpu_timing import export_gpu_timing, load_timing_json, merge_timing, write_timing_csv


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out-dir", "-o", help="输出目录 (默认: ./<输入文件名>/)")


def _out_dir(args, input_path: Path | None = None) -> Path:
    if args.out_dir:
        return Path(args.out_dir).resolve()
    if input_path:
        return _default_out_dir(input_path)
    return Path(".").resolve()


def _default_out_dir(input_path: Path) -> Path:
    """默认输出目录：当前工作目录下以输入文件名命名的子目录。"""
    return Path.cwd() / input_path.stem


# ── 参考文件自动发现 ────────────────────────────────────────────────

_DEFAULT_REFS_DIR = Path("rdc_parser/references")


def _add_references_args(p: argparse.ArgumentParser) -> None:
    """添加参考文件相关参数：--references-dir + 三个独立覆盖参数。"""
    p.add_argument("--references-dir", help=f"参考文件目录 (默认 {_DEFAULT_REFS_DIR})")
    p.add_argument("--mesh-export", help="mesh_export.csv 路径（覆盖 references-dir 中的）")
    p.add_argument("--resource-standard", help="资源分类标准.md 路径（覆盖 references-dir 中的）")
    p.add_argument("--shader-list", help="ShaderList.csv 路径（覆盖 references-dir 中的）")


def _resolve_references(args) -> tuple[Path | None, Path | None, Path | None]:
    """解析参考文件路径：独立参数 > references-dir 自动发现 > None。"""
    refs_dir = Path(args.references_dir) if hasattr(args, "references_dir") and args.references_dir else _DEFAULT_REFS_DIR

    mesh_export = Path(args.mesh_export) if hasattr(args, "mesh_export") and args.mesh_export else None
    resource_standard = Path(args.resource_standard) if hasattr(args, "resource_standard") and args.resource_standard else None
    shader_list = Path(args.shader_list) if hasattr(args, "shader_list") and args.shader_list else None

    if refs_dir.is_dir():
        if mesh_export is None:
            candidate = refs_dir / "mesh_export.csv"
            if candidate.exists():
                mesh_export = candidate
        if resource_standard is None:
            for name in ["资源分类标准.md", "resource_standard.md"]:
                candidate = refs_dir / name
                if candidate.exists():
                    resource_standard = candidate
                    break
        if shader_list is None:
            candidate = refs_dir / "ShaderList.csv"
            if candidate.exists():
                shader_list = candidate

    return mesh_export, resource_standard, shader_list


# ── convert ─────────────────────────────────────────────────────────

def cmd_convert(args: argparse.Namespace) -> None:
    """将 .rdc 转换为 .xml。"""
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"错误: 文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _default_out_dir(input_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"输入: {input_path}")
    xml_path = ensure_xml(input_path, out_dir, args.force_convert, args.renderdoccmd)
    print(f"输出: {xml_path}")

    platform_info = extract_platform_info(xml_path)
    if platform_info:
        print(f"平台: {platform_info}")


# ── parse ───────────────────────────────────────────────────────────

def cmd_parse(args: argparse.Namespace) -> None:
    """解析 .xml 输出 drawcalls.csv。"""
    xml_path = Path(args.input).resolve()
    if not xml_path.exists():
        print(f"错误: 文件不存在: {xml_path}", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _default_out_dir(xml_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"XML: {xml_path}")
    platform_info = extract_platform_info(xml_path)
    if platform_info:
        print(f"平台: {platform_info}")

    print("解析中...", flush=True)
    records = analyze(xml_path)
    print(f"共 {len(records)} 个 DrawCall")

    csv_path = out_dir / "drawcalls.csv"
    write_drawcalls_csv(csv_path, records)
    print(f"输出: {csv_path}")


# ── classify ────────────────────────────────────────────────────────

def cmd_classify(args: argparse.Namespace) -> None:
    """读取 drawcalls.csv，应用分类，输出 classified.csv。"""
    csv_path = Path(args.input).resolve()
    if not csv_path.exists():
        print(f"错误: 文件不存在: {csv_path}", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _default_out_dir(csv_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"输入: {csv_path}")
    records = load_drawcalls_csv(csv_path)
    print(f"加载 {len(records)} 条记录")

    # 加载参考数据
    mesh_export, resource_standard, shader_list = _resolve_references(args)
    resource_matcher = None
    if mesh_export and mesh_export.exists():
        mesh_paths = load_mesh_export_csv(mesh_export)
        rules = parse_resource_standard_md(resource_standard) if resource_standard and resource_standard.exists() else []
        keyword_rules = parse_keyword_rules_md(resource_standard) if resource_standard and resource_standard.exists() else []
        resource_matcher = ResourceMatcher(mesh_paths, rules, keyword_rules)
        total_paths = sum(len(entries) for entries in mesh_paths.values())
        print(f"已加载 {len(mesh_paths)} 个 mesh ({total_paths} 条路径), {len(rules)} 条路径规则, {len(keyword_rules)} 条关键词规则")

    if shader_list and shader_list.exists():
        shader_names = load_shader_list_csv(shader_list)
        configure_shader_name_index(shader_names)
        print(f"已加载 {len(shader_names)} 个 shader 名称")

    records = classify_records(records, resource_matcher)

    classified_path = out_dir / "classified.csv"
    write_classified_csv(classified_path, records)
    print(f"输出: {classified_path}")


# ── report ──────────────────────────────────────────────────────────

def cmd_report(args: argparse.Namespace) -> None:
    """从 classified.csv 生成汇总报告。"""
    csv_path = Path(args.input).resolve()
    if not csv_path.exists():
        print(f"错误: 文件不存在: {csv_path}", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _default_out_dir(csv_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    records = load_classified_csv(csv_path)
    print(f"加载 {len(records)} 条记录")

    for field, label in [
        ("mesh_type", "按类型"), ("asset_module", "按资产模块"),
        ("resource_category", "按资源分类"), ("pass_name", "按Pass"),
        ("ps", "按Shader"),
    ]:
        path = out_dir / f"rdc_summary_by_{field}.csv"
        write_summary_csv(path, summarise_by(records, field))
        print(f"{label}汇总: {path}")

    mesh_path = out_dir / "rdc_mesh_detail.csv"
    write_mesh_detail_csv(mesh_path, records)
    print(f"Mesh详细汇总: {mesh_path}")

    shader_path = out_dir / "rdc_shader_detail.csv"
    write_shader_detail_csv(shader_path, records)
    print(f"Shader详细汇总: {shader_path}")

    keywords_path = out_dir / "rdc_shader_keywords.csv"
    write_shader_keywords_csv(keywords_path, records)
    print(f"Shader关键字明细: {keywords_path}")

    report_path = out_dir / "rdc_report.md"
    platform_info = ""
    if hasattr(args, "platform") and args.platform:
        platform_info = args.platform

    # 获取资源标准路径
    _, resource_standard, _ = _resolve_references(args)
    write_report_md(report_path, records, platform_info, resource_standard=resource_standard)
    print(f"分析报告: {report_path}")

    print_summary(records, platform_info)


# ── poly ────────────────────────────────────────────────────────────

def cmd_poly(args: argparse.Namespace) -> None:
    """减面分析：从 classified.csv 聚合 mesh 级别数据，匹配面数标准。"""
    csv_path = Path(args.input).resolve()
    if not csv_path.exists():
        print(f"错误: 文件不存在: {csv_path}", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _default_out_dir(csv_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    _, resource_standard, _ = _resolve_references(args)
    if not resource_standard or not resource_standard.exists():
        print(f"错误: 资源分类标准文件不存在: {resource_standard}", file=sys.stderr)
        sys.exit(1)

    records = load_classified_csv(csv_path)
    print(f"加载 {len(records)} 条记录")

    run_poly_analysis(records, out_dir, resource_standard)


# ── run ─────────────────────────────────────────────────────────────

def cmd_run(args: argparse.Namespace) -> None:
    """一键全流程：convert → parse → classify → report → timing。"""
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"错误: 文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _default_out_dir(input_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"输入: {input_path}")
    records, platform_info, mesh_export, resource_standard = _run_pipeline_core(input_path, out_dir, args)

    # 分类 Excel
    if mesh_export:
        cls_xlsx_path = out_dir / "classification.xlsx"
        write_classification_excel(cls_xlsx_path, records)
        print(f"分类Excel: {cls_xlsx_path}")

    # poly 减面分析
    if resource_standard and resource_standard.exists():
        run_poly_analysis(records, out_dir, resource_standard)

    # 缩略图
    if not args.no_thumb:
        rdc_for_thumb = resolve_rdc_for_thumbnail(input_path)
        if rdc_for_thumb:
            thumb_path = out_dir / "capture_thumbnail.png"
            if export_rdc_capture_thumbnail(rdc_for_thumb, thumb_path, args.renderdoccmd):
                print(f"缩略图: {thumb_path}")

    # GPU 耗时（默认开启）
    timing_data = None
    if not args.no_timing and input_path.suffix.lower() == ".rdc":
        timing_runs = getattr(args, "timing_runs", 3)
        renderdoccmd = getattr(args, "renderdoccmd", None)
        json_path = out_dir / "gpu_timing.json"

        print(f"\n导出 GPU 耗时 ({timing_runs} 次回放)...")
        if export_gpu_timing(input_path, json_path, renderdoccmd, timing_runs):
            timing_data = load_timing_json(json_path)
            if timing_data:
                print(f"解析到 {len(timing_data)} 个事件耗时")
                classified_path = out_dir / "classified.csv"
                if classified_path.exists():
                    records = load_classified_csv(classified_path)
                    rows = merge_timing(records, timing_data)
                    csv_path = out_dir / "gpu_timing.csv"
                    write_timing_csv(csv_path, rows)
                    print(f"耗时 CSV: {csv_path}")

                    # 重新生成报告（包含耗时数据和减面分析）
                    write_report_md(out_dir / "rdc_report.md", records, platform_info, timing_data, resource_standard)
                    print(f"更新报告（含耗时）: {out_dir / 'rdc_report.md'}")
            else:
                print("未获取到耗时数据")
        else:
            print("GPU 耗时导出失败（可忽略，用 --no-timing 跳过）")

    print_summary(records, platform_info)


# ── html ─────────────────────────────────────────────────────────

def cmd_html(args: argparse.Namespace) -> None:
    """从 classified.csv 生成交互式 HTML 报告。"""
    csv_path = Path(args.input).resolve()
    if not csv_path.exists():
        print(f"错误: 文件不存在: {csv_path}", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _default_out_dir(csv_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    records = load_classified_csv(csv_path)
    print(f"加载 {len(records)} 条记录")

    platform_info = args.platform if hasattr(args, "platform") and args.platform else ""
    html_path = out_dir / "rdc_report.html"
    write_html_report(html_path, records, platform_info)
    print(f"HTML 报告: {html_path}")


# ── compare ─────────────────────────────────────────────────────

def cmd_compare(args: argparse.Namespace) -> None:
    """对比两份 classified.csv，输出 delta CSV 和报告。"""
    path_a = Path(args.input_a).resolve()
    path_b = Path(args.input_b).resolve()
    for p, label in [(path_a, "A"), (path_b, "B")]:
        if not p.exists():
            print(f"错误: {label} 文件不存在: {p}", file=sys.stderr)
            sys.exit(1)
    out_dir = _out_dir(args, path_a)
    out_dir.mkdir(parents=True, exist_ok=True)

    records_a = load_classified_csv(path_a)
    records_b = load_classified_csv(path_b)
    print(f"A: {path_a.name} ({len(records_a)} 条)")
    print(f"B: {path_b.name} ({len(records_b)} 条)")

    name_a = Path(args.name_a).stem if hasattr(args, "name_a") and args.name_a else path_a.stem
    name_b = Path(args.name_b).stem if hasattr(args, "name_b") and args.name_b else path_b.stem
    compare_records(records_a, records_b, out_dir, name_a, name_b)
    print(f"对比报告: {out_dir / 'compare_report.md'}")


# ── batch ────────────────────────────────────────────────────────

def cmd_batch(args: argparse.Namespace) -> None:
    """批量分析目录下所有 .rdc 文件。"""
    input_dir = Path(args.input).resolve()
    if not input_dir.is_dir():
        print(f"错误: 目录不存在: {input_dir}", file=sys.stderr)
        sys.exit(1)
    out_dir = _out_dir(args, input_dir)

    def _pipeline(rdc_path: Path, sub_out_dir: Path) -> list:
        records, _platform_info, _me, _rs = _run_pipeline_core(rdc_path, sub_out_dir, args)
        return records

    run_batch(input_dir, out_dir, _pipeline)


# ── timing ──────────────────────────────────────────────────────

def cmd_timing(args: argparse.Namespace) -> None:
    """导出 GPU 耗时并合并到 classified.csv。"""
    rdc_path = Path(args.input).resolve()
    if not rdc_path.exists():
        print(f"错误: 文件不存在: {rdc_path}", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(args.out_dir).resolve() if args.out_dir else _default_out_dir(rdc_path)
    out_dir.mkdir(parents=True, exist_ok=True)

    runs = getattr(args, "runs", 3)
    renderdoccmd = getattr(args, "renderdoccmd", None)

    # 如果已有 JSON 直接用，否则从 RDC 导出
    if args.json and Path(args.json).exists():
        json_path = Path(args.json).resolve()
        print(f"使用已有耗时 JSON: {json_path}")
    else:
        json_path = out_dir / "gpu_timing.json"
        print(f"导出 GPU 耗时 ({runs} 次回放)...")
        if not export_gpu_timing(rdc_path, json_path, renderdoccmd, runs):
            sys.exit(1)
        print(f"耗时 JSON: {json_path}")

    timing = load_timing_json(json_path)
    print(f"解析到 {len(timing)} 个事件耗时")

    if args.classified:
        classified_path = Path(args.classified).resolve()
        if not classified_path.exists():
            print(f"错误: classified.csv 不存在: {classified_path}", file=sys.stderr)
            sys.exit(1)
        records = load_classified_csv(classified_path)
        rows = merge_timing(records, timing)
    else:
        rows = [{"event": eid, "gpu_time_ms": round(dur, 4)} for eid, dur in sorted(timing.items())]

    csv_path = out_dir / "gpu_timing.csv"
    write_timing_csv(csv_path, rows)
    print(f"耗时 CSV: {csv_path}")


# ── 核心流水线（run / batch 共用）──────────────────────────────────────

def _run_pipeline_core(
    rdc_path: Path,
    out_dir: Path,
    args: argparse.Namespace,
) -> tuple[list, str, Path | None, Path | None]:
    """单个 RDC 的核心流水线：convert → parse → classify → report。

    返回 (records, platform_info, mesh_export, resource_standard)。
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. convert
    xml_path = ensure_xml(rdc_path, out_dir, args.force_convert, args.renderdoccmd)
    platform_info = extract_platform_info(xml_path) or ""

    # 2. parse
    records = analyze(xml_path)
    write_drawcalls_csv(out_dir / "drawcalls.csv", records)

    # 3. classify
    mesh_export, resource_standard, shader_list = _resolve_references(args)
    resource_matcher = None
    if mesh_export and mesh_export.exists():
        mesh_paths = load_mesh_export_csv(mesh_export)
        rules = parse_resource_standard_md(resource_standard) if resource_standard and resource_standard.exists() else []
        keyword_rules = parse_keyword_rules_md(resource_standard) if resource_standard and resource_standard.exists() else []
        resource_matcher = ResourceMatcher(mesh_paths, rules, keyword_rules)

    if shader_list and shader_list.exists():
        shader_names = load_shader_list_csv(shader_list)
        configure_shader_name_index(shader_names)

    records = classify_records(records, resource_matcher)
    write_classified_csv(out_dir / "classified.csv", records)

    # 4. report
    for field in ("pass_name", "ps", "asset_module", "resource_category"):
        write_summary_csv(out_dir / f"rdc_summary_by_{field}.csv", summarise_by(records, field))
    write_mesh_detail_csv(out_dir / "rdc_mesh_detail.csv", records)
    write_shader_detail_csv(out_dir / "rdc_shader_detail.csv", records)
    write_shader_keywords_csv(out_dir / "rdc_shader_keywords.csv", records)
    write_report_md(out_dir / "rdc_report.md", records, platform_info, resource_standard=resource_standard)

    return records, platform_info, mesh_export, resource_standard


# ── 主入口 ──────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="parse_rdc",
        description="RenderDoc .rdc 文件解析流水线",
    )
    sub = parser.add_subparsers(dest="command")

    # convert
    p = sub.add_parser("convert", help="将 .rdc 转换为 .xml")
    p.add_argument("input", help=".rdc 文件路径")
    _add_common_args(p)
    p.add_argument("--renderdoccmd", help="renderdoccmd.exe 路径")
    p.add_argument("--force-convert", action="store_true", help="强制重新转换")

    # parse
    p = sub.add_parser("parse", help="解析 .xml 输出 drawcalls.csv")
    p.add_argument("input", help=".xml 文件路径")
    _add_common_args(p)

    # classify
    p = sub.add_parser("classify", help="读取 drawcalls.csv 应用分类，输出 classified.csv")
    p.add_argument("input", help="drawcalls.csv 文件路径")
    _add_common_args(p)
    _add_references_args(p)

    # report
    p = sub.add_parser("report", help="从 classified.csv 生成汇总报告")
    p.add_argument("input", help="classified.csv 文件路径")
    _add_common_args(p)

    # poly
    p = sub.add_parser("poly", help="减面分析：聚合 mesh 级别数据，匹配面数标准")
    p.add_argument("input", help="classified.csv 文件路径")
    _add_common_args(p)
    _add_references_args(p)

    # run
    p = sub.add_parser("run", help="一键全流程")
    p.add_argument("input", help=".rdc 或 .xml 文件路径")
    _add_common_args(p)
    p.add_argument("--renderdoccmd", help="renderdoccmd.exe 路径")
    p.add_argument("--force-convert", action="store_true", help="强制重新转换")
    _add_references_args(p)
    p.add_argument("--no-thumb", action="store_true", help="禁用缩略图导出")
    p.add_argument("--no-timing", action="store_true", help="禁用 GPU 耗时导出")
    p.add_argument("--timing-runs", type=int, default=3, help="GPU 耗时回放次数 (默认 3)")

    # html
    p = sub.add_parser("html", help="从 classified.csv 生成交互式 HTML 报告")
    p.add_argument("input", help="classified.csv 文件路径")
    _add_common_args(p)
    p.add_argument("--platform", help="平台信息（显示在报告中）")

    # compare
    p = sub.add_parser("compare", help="对比两份 classified.csv，输出 delta 报告")
    p.add_argument("input_a", help="A 份 classified.csv")
    p.add_argument("input_b", help="B 份 classified.csv")
    _add_common_args(p)
    p.add_argument("--name-a", help="A 份名称（默认取文件名）")
    p.add_argument("--name-b", help="B 份名称（默认取文件名）")

    # batch
    p = sub.add_parser("batch", help="批量分析目录下所有 .rdc 文件")
    p.add_argument("input", help="包含 .rdc 文件的目录")
    _add_common_args(p)
    p.add_argument("--renderdoccmd", help="renderdoccmd.exe 路径")
    p.add_argument("--force-convert", action="store_true", help="强制重新转换")
    _add_references_args(p)

    # timing
    p = sub.add_parser("timing", help="导出 GPU 耗时并合并到 classified.csv")
    p.add_argument("input", help=".rdc 文件路径")
    _add_common_args(p)
    p.add_argument("--json", help="已有耗时 JSON 路径（跳过导出）")
    p.add_argument("--classified", help="classified.csv 路径（合并耗时用）")
    p.add_argument("--renderdoccmd", help="renderdoccmd.exe 路径")
    p.add_argument("--runs", type=int, default=3, help="回放次数 (默认 3)")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmd_map = {
        "convert": cmd_convert,
        "parse": cmd_parse,
        "classify": cmd_classify,
        "report": cmd_report,
        "poly": cmd_poly,
        "run": cmd_run,
        "html": cmd_html,
        "compare": cmd_compare,
        "batch": cmd_batch,
        "timing": cmd_timing,
    }
    cmd_map[args.command](args)
