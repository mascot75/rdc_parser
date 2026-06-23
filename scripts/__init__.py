# -*- coding: utf-8 -*-
"""RenderDoc .rdc 文件解析工具包。"""

from .models import DrawRec, BlendInfo, DepthInfo, VkCmdState, MeshStat, DRAW_CALLS, HEADER_ZH, detect_lod, aggregate_mesh_stats
from .converter import find_renderdoccmd, ensure_xml, extract_platform_info
from .analyzer import analyze, write_drawcalls_csv, load_drawcalls_csv
from .classify_step import classify_records, write_classified_csv, load_classified_csv
from .reporter import write_details_csv, summarise_by, write_summary_csv, print_summary, write_classification_excel
from .shader import load_shader_list_csv, configure_shader_name_index, strip_shader_keywords
from .resource import (
    ResourceInfo, ResourceRule, ResourceMatcher,
    load_mesh_export_csv, parse_resource_standard_md,
    resolve_asset_module, finalize_resource_info,
    normalize_asset_path, strip_mesh_suffix, classify_resource_path,
)
from .thumbnail import export_rdc_capture_thumbnail, resolve_rdc_for_thumbnail

__all__ = [
    "DrawRec", "BlendInfo", "DepthInfo", "VkCmdState", "MeshStat",
    "DRAW_CALLS", "HEADER_ZH", "detect_lod", "aggregate_mesh_stats",
    "find_renderdoccmd", "ensure_xml", "extract_platform_info",
    "analyze", "write_drawcalls_csv", "load_drawcalls_csv",
    "classify_records", "write_classified_csv", "load_classified_csv",
    "write_details_csv", "summarise_by", "write_summary_csv", "print_summary",
    "write_classification_excel",
    "load_shader_list_csv", "configure_shader_name_index", "strip_shader_keywords",
    "ResourceInfo", "ResourceRule", "ResourceMatcher",
    "load_mesh_export_csv", "parse_resource_standard_md",
    "resolve_asset_module", "finalize_resource_info",
    "normalize_asset_path", "strip_mesh_suffix", "classify_resource_path",
    "export_rdc_capture_thumbnail", "resolve_rdc_for_thumbnail",
]
