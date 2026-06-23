---
name: rdc_parser
description: RenderDoc .rdc 文件解析流水线。将 .rdc 转 XML、解析 DrawCall、分类资源、生成报告。支持单文件、批量、对比、GPU 耗时。
metadata:
  short-description: RenderDoc 抓帧解析与资源分类
---

# rdc_parser

RenderDoc `.rdc` 抓帧解析 CLI 工具。从 `.rdc` 导出 XML，解析全部 DrawCall，按 Pass/Shader/资源类型分类，输出 CSV/Markdown/HTML 报告。

## 目录结构

```
rdc_parser/
├── scripts/          ← Python 脚本（cli.py 为主入口）
├── references/       ← 参考数据
│   ├── mesh_export.csv
│   ├── ShaderList.csv
│   └── 资源分类标准.md
└── output/           ← 测试输出
```

## 用法

```bash
python -m scripts.cli <子命令> [参数]
```

需在项目根目录 `rdc_parser/` 下执行。

## 参考数据

内置参考文件位于 `rdc_parser/references/`：

| 文件 | 用途 |
|------|------|
| `mesh_export.csv` | 项目 mesh 路径 → 资源分类映射 |
| `ShaderList.csv` | Shader 名称归一化表 |
| `资源分类标准.md` | 资源类型/分类规则定义 |

CLI 默认从 `rdc_parser/references/` 自动加载，也可用 `--references-dir` 或各 `--mesh-export` / `--resource-standard` / `--shader-list` 单独指定。

## 子命令

### run — 一键全流程（最常用）

```bash
python -m scripts.cli run <input.rdc> -o <output_dir>
```

自动执行：convert → parse → classify → report → timing，输出全部 CSV + Markdown 报告 + 缩略图。

可选参数：
- `--renderdoccmd <path>` — 指定 renderdoccmd.exe
- `--force-convert` — 强制重新转换 XML
- `--no-thumb` — 禁用缩略图导出
- `--no-timing` — 禁用 GPU 耗时导出
- `--timing-runs <N>` — GPU 耗时回放次数（默认 3）
- `--references-dir <dir>` — 参考数据目录
- `--mesh-export <path>` — mesh_export.csv 路径（覆盖 references-dir）
- `--resource-standard <path>` — 资源分类标准.md 路径（覆盖 references-dir）
- `--shader-list <path>` — ShaderList.csv 路径（覆盖 references-dir）

### convert — .rdc 转 .xml

```bash
python -m scripts.cli convert <input.rdc> -o <out_dir>
```

### parse — XML 解析 DrawCall

```bash
python -m scripts.cli parse <input.xml> -o <out_dir>
```

输出 `drawcalls.csv`。

### classify — 资源分类

```bash
python -m scripts.cli classify <drawcalls.csv> -o <out_dir>
```

加载参考数据，输出 `classified.csv`。

可选参考参数：`--references-dir`、`--mesh-export`、`--resource-standard`、`--shader-list`。

### report — 生成汇总报告

```bash
python -m scripts.cli report <classified.csv> -o <out_dir>
```

输出：
- `rdc_report.md` — Markdown 分析报告
- `rdc_summary_by_mesh_type.csv` — 按类型汇总
- `rdc_summary_by_pass_name.csv` — 按 Pass 汇总
- `rdc_summary_by_ps.csv` — 按 Shader 汇总
- `rdc_summary_by_asset_module.csv` — 按资产模块汇总
- `rdc_summary_by_resource_category.csv` — 按资源分类汇总
- `rdc_mesh_detail.csv` — Mesh 详细汇总
- `rdc_shader_detail.csv` — Shader 详细汇总
- `rdc_shader_keywords.csv` — Shader 关键字明细

### html — 交互式 HTML 报告

```bash
python -m scripts.cli html <classified.csv> -o <out_dir> [--platform "D3D11 / SM5.0"]
```

输出 `rdc_report.html`，表格可排序筛选。

### poly — 减面分析

```bash
python -m scripts.cli poly <classified.csv> -o <out_dir>
```

聚合 mesh 级别数据，匹配 `资源分类标准.md` 中的面数标准，输出超标项。

可选参考参数：`--references-dir`、`--mesh-export`、`--resource-standard`、`--shader-list`。

### compare — 两份抓帧对比

```bash
python -m scripts.cli compare <A_classified.csv> <B_classified.csv> -o <out_dir> [--name-a "优化前"] [--name-b "优化后"]
```

输出 `compare_report.md` + 差异 CSV。

### batch — 批量分析

```bash
python -m scripts.cli batch <rdc_directory> -o <out_dir>
```

遍历目录下所有 `.rdc`，每个子目录独立走一遍全流程。

可选参数：`--renderdoccmd`、`--force-convert`、`--references-dir`、`--mesh-export`、`--resource-standard`、`--shader-list`。

### timing — GPU 耗时

```bash
python -m scripts.cli timing <input.rdc> -o <out_dir> [--runs 3] [--classified <classified.csv>] [--json <timing.json>]
```

多次回放导出 per-event GPU 耗时，可合并到 `classified.csv`。`--json` 可跳过导出，直接使用已有的 timing JSON 文件。

## 工作流示例

```bash
# 单文件完整分析
python -m scripts.cli run "D:\captures\scene.rdc" -o "D:\output\scene"

# 对比优化前后
python -m scripts.cli run "D:\captures\before.rdc" -o "D:\output\before"
python -m scripts.cli run "D:\captures\after.rdc" -o "D:\output\after"
python -m scripts.cli compare "D:\output\before\classified.csv" "D:\output\after\classified.csv" -o "D:\output\diff"

# 批量处理
python -m scripts.cli batch "D:\captures\batch" -o "D:\output\batch_out"
```

## 环境要求

- Python 3.10+
- RenderDoc（`renderdoccmd.exe` 用于 .rdc 转 XML 和缩略图导出）
