# -*- coding: utf-8 -*-
"""从 classified.csv 生成交互式 HTML 报告。"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from .models import DrawRec


def _escape(text: str) -> str:
    return html.escape(str(text))


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def _format_number(n: int) -> str:
    return f"{n:,}"


def write_html_report(path: Path, records: list[DrawRec], platform_info: str = "") -> None:
    """生成交互式 HTML 报告。"""
    if not records:
        raise ValueError("没有绘制记录，拒绝生成空 HTML 报告")
    # 统计汇总
    total_dc = len(records)
    total_faces = sum(_safe_int(r.face_estimate) for r in records)
    blend_count = sum(1 for r in records if r.blend)
    no_depth_count = sum(1 for r in records if not r.depth_enable)

    # 按 Pass 汇总
    pass_stats: dict[str, dict[str, int]] = {}
    for r in records:
        key = r.pass_name or "Unknown"
        if key not in pass_stats:
            pass_stats[key] = {"dc": 0, "faces": 0, "blend": 0, "no_depth": 0}
        pass_stats[key]["dc"] += 1
        pass_stats[key]["faces"] += _safe_int(r.face_estimate)
        if r.blend:
            pass_stats[key]["blend"] += 1
        if not r.depth_enable:
            pass_stats[key]["no_depth"] += 1

    # 按资源分类汇总
    resource_stats: dict[str, dict[str, int]] = {}
    for r in records:
        key = r.resource_category or "未分类"
        if key not in resource_stats:
            resource_stats[key] = {"dc": 0, "faces": 0}
        resource_stats[key]["dc"] += 1
        resource_stats[key]["faces"] += _safe_int(r.face_estimate)

    # 生成 HTML
    html_parts = []
    html_parts.append("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RDC 分析报告</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 20px; background: #f5f5f5; }
.container { max-width: 1400px; margin: 0 auto; }
h1 { color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }
h2 { color: #555; margin-top: 30px; }
.summary { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin: 20px 0; }
.card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
.card .label { color: #666; font-size: 14px; }
.card .value { font-size: 28px; font-weight: bold; color: #333; }
table { width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin: 15px 0; }
th { background: #4CAF50; color: white; padding: 12px 15px; text-align: left; position: sticky; top: 0; }
td { padding: 10px 15px; border-bottom: 1px solid #eee; }
tr:hover { background: #f9f9f9; }
.num { text-align: right; font-family: monospace; }
.delta-pos { color: #f44336; }
.delta-neg { color: #4CAF50; }
.filters { margin: 15px 0; }
.filters input { padding: 8px 12px; border: 1px solid #ddd; border-radius: 4px; width: 300px; }
.platform { color: #888; font-size: 14px; margin-bottom: 20px; }
</style>
</head>
<body>
<div class="container">
""")

    if platform_info:
        html_parts.append(f'<p class="platform">平台: {_escape(platform_info)}</p>')

    html_parts.append(f"""
<h1>RDC DrawCall 分析报告</h1>
<p>面数仅统计已知绘制参数；未知项不是零。资源名称和分类可能缺少项目参考数据。IB 容量不作为实际绘制面数。</p>
<p>面数已知 {sum(r.face_known for r in records)} / {total_dc} 条。事件编号类型：{_escape(records[0].event_space)}。</p>
<div class="summary">
<div class="card"><div class="label">DrawCall 总数</div><div class="value">{_format_number(total_dc)}</div></div>
<div class="card"><div class="label">总面数</div><div class="value">{_format_number(total_faces)}</div></div>
<div class="card"><div class="label">混合 DrawCall</div><div class="value">{_format_number(blend_count)}</div></div>
<div class="card"><div class="label">不写深度</div><div class="value">{_format_number(no_depth_count)}</div></div>
</div>
""")

    # Pass 汇总表
    html_parts.append("<h2>按 Pass 汇总</h2>")
    html_parts.append('<div class="filters"><input type="text" id="passFilter" placeholder="筛选 Pass..." oninput="filterTable(\'passTable\', this.value)"></div>')
    html_parts.append('<table id="passTable"><thead><tr><th>Pass</th><th class="num">DrawCall</th><th class="num">面数</th><th class="num">混合</th><th class="num">不写深度</th></tr></thead><tbody>')
    for pass_name, stats in sorted(pass_stats.items(), key=lambda x: x[1]["dc"], reverse=True):
        html_parts.append(f'<tr><td>{_escape(pass_name)}</td><td class="num">{_format_number(stats["dc"])}</td><td class="num">{_format_number(stats["faces"])}</td><td class="num">{_format_number(stats["blend"])}</td><td class="num">{_format_number(stats["no_depth"])}</td></tr>')
    html_parts.append("</tbody></table>")

    # 资源分类汇总表
    html_parts.append("<h2>按资源分类汇总</h2>")
    html_parts.append('<div class="filters"><input type="text" id="resFilter" placeholder="筛选资源..." oninput="filterTable(\'resTable\', this.value)"></div>')
    html_parts.append('<table id="resTable"><thead><tr><th>资源分类</th><th class="num">DrawCall</th><th class="num">面数</th></tr></thead><tbody>')
    for res_name, stats in sorted(resource_stats.items(), key=lambda x: x[1]["dc"], reverse=True):
        html_parts.append(f'<tr><td>{_escape(res_name)}</td><td class="num">{_format_number(stats["dc"])}</td><td class="num">{_format_number(stats["faces"])}</td></tr>')
    html_parts.append("</tbody></table>")

    # 明细表
    html_parts.append("<h2>DrawCall 明细</h2>")
    html_parts.append('<div class="filters"><input type="text" id="detailFilter" placeholder="筛选..." oninput="filterTable(\'detailTable\', this.value)"></div>')
    html_parts.append('<table id="detailTable"><thead><tr><th>事件序号</th><th>Pass</th><th>Shader</th><th>资源分类</th><th class="num">面数</th><th>混合</th><th>写深度</th></tr></thead><tbody>')
    for r in records:
        blend_txt = "Y" if r.blend else ""
        depth_txt = "Y" if r.depth_enable else ""
        html_parts.append(f'<tr><td class="num">{r.event}</td><td>{_escape(r.pass_name)}</td><td>{_escape(r.ps)}</td><td>{_escape(r.resource_category)}</td><td class="num">{_format_number(_safe_int(r.face_estimate))}</td><td>{blend_txt}</td><td>{depth_txt}</td></tr>')
    html_parts.append("</tbody></table>")

    # JavaScript
    html_parts.append("""
</div>
<script>
function filterTable(tableId, filterText) {
    var table = document.getElementById(tableId);
    var rows = table.getElementsByTagName("tr");
    var filter = filterText.toLowerCase();
    for (var i = 1; i < rows.length; i++) {
        var cells = rows[i].getElementsByTagName("td");
        var found = false;
        for (var j = 0; j < cells.length; j++) {
            if (cells[j].textContent.toLowerCase().indexOf(filter) > -1) {
                found = true;
                break;
            }
        }
        rows[i].style.display = found ? "" : "none";
    }
}
</script>
</body>
</html>
""")

    path.write_text("".join(html_parts), encoding="utf-8")
