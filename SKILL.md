---
name: rdc-parser
description: 解析 RenderDoc RDC 抓帧，导出绘制记录、资源分类、CSV/Markdown/HTML 报告和事件 GPU 回放耗时。支持 D3D11、Vulkan 静态 XML 分析及可回放 RDC 的 D3D12 间接绘制展开。
metadata:
  short-description: RenderDoc 抓帧解析与证据验证
---

# RenderDoc 抓帧解析

在技能根目录执行 `python -m scripts <子命令>`。运行环境为 Python 3.10+ 和 RenderDoc；完整回放依赖与抓帧兼容的 GPU、驱动和 API。当前回放适配经 RenderDoc 1.46 验证。

## 一键分析

```powershell
python -m scripts run "D:\captures\scene.rdc" -o "D:\output\scene"
```

默认尝试三轮 GPU 采样并导出回放事件状态，然后转换 XML、分类和生成报告。可回放时以 RenderDoc 事件树的叶子绘制为准，包括 D3D12 ExecuteIndirect 展开的实际绘制。不可回放时尝试 D3D11/Vulkan XML 静态分析，保留部分结果及失败原因。

可选参数：`--renderdoccmd <renderdoccmd.exe>`、`--force-convert`、`--no-thumb`、`--no-timing`、`--timing-runs N`。
`--replay-json <gpu_timing.json>` 可复用已完成的 schema 2 回放导出，校验源路径、文件大小和修改时间；必须说明该轮复用了采样，不能声称重新测量。强制重新测量时不传此参数。
`--no-timing` 仅禁用性能计数器采样；为展开间接绘制，仍尝试提取回放状态。D3D12 完整分析需要成功回放，不能从 XML 静态内容可靠还原 GPU 生成的间接参数。

## 结果验收

- 首先读取 `analysis_status.json`。退出码 0 表示所请求的流程完成；2 表示保留了静态报告但请求的回放/耗时失败；1 表示分析失败。已知面数覆盖率仍需单独检查，流程完成不意味着所有字段都已知。
- 检查 `drawcalls.csv`、`classified.csv`、`rdc_report.md`、`rdc_report.html` 及汇总 CSV 的真实内容。空 DrawCall、空报告或没有匹配的耗时必须报错，不能只看进程退出。
- `gpu_timing.json` schema 2 保存逐次样本、叶子事件、绘制状态、源文件身份和失败原因。`gpu_timing.csv` 仅为匹配到绘制记录的耗时，未匹配项为空而非零。
- CSV 明确记录事件编号类型：`replay_event` 为回放 event ID；`xml_chunk` 为 XML chunk index。二者不能混用。多个间接子事件可以关联一个原始调用，不能当作重复项丢弃或重复累加同一个耗时。
- Draw 面数来自绘制参数或回放动作；未解码的间接参数、未知拓扑、启用 primitive restart 的 strip 保留未知。IB 容量参考不能作为实际绘制面数。提交的三角形数也不代表裁剪、细分后最终光栅化的三角形数。
- GPU 耗时为当前 GPU 上的回放测量。按绘制汇总不包含所有计算、拷贝或未匹配事件；不得换算成游戏 FPS 或原设备帧时间。
- Android 抓帧可能依赖桌面 GPU 不支持的扩展。保留具体 OpenCapture 错误，需要兼容设备远程回放时明确说明；不要把空计数器结果当作零耗时。

## 参考数据

参考数据是可选的项目输入：`mesh_export.csv`、`ShaderList.csv` 和 `资源分类标准.md`。CLI 默认查找工作目录的 `references/`，也可通过 `--references-dir`、`--mesh-export`、`--shader-list`、`--resource-standard` 指定。没有这些数据时，不承诺项目路径匹配、Shader 名称归一化或面数标准判断；报告中的启发式分类和 Buffer 分组不等于经过验证的项目资产。

更新数据的工具脚本位于 `scripts/`，不依赖打包内的 references 目录：

```powershell
python -m scripts update-refs "D:\UnityProject" -o "D:\project-reference-data"
```

该命令扫描 Unity YAML mesh、prefab 和 shader；可用 `--mesh-dir`、`--prefab-dir`、`--workers` 和 `--skip-mesh/--skip-prefab/--skip-shader` 控制范围。资源分类标准需自行提供，不能从缺失的标准推断超标。

## 分步命令

```powershell
python -m scripts convert input.rdc -o output
python -m scripts parse output/input.xml -o output
python -m scripts classify output/drawcalls.csv -o output --references-dir D:\project-reference-data
python -m scripts report output/classified.csv -o output
python -m scripts html output/classified.csv -o output
python -m scripts poly output/classified.csv -o output --resource-standard D:\project-reference-data\资源分类标准.md
python -m scripts timing input.rdc -o timing --runs 3
python -m scripts compare before/classified.csv after/classified.csv -o comparison
python -m scripts batch D:\captures -o D:\reports
```

`parse` 的 XML 静态状态机仅支持 D3D11/Vulkan。对 D3D12 使用 `run input.rdc`。`timing --json` 可读取已有耗时，`--classified` 可合并同一次抓帧的分类 CSV；新旧事件编号格式不一致会拒绝合并。`compare` 可用 `--timing-a/--timing-b` 提供各自对应的耗时。按 `--help` 查询各子命令支持的参数。

## 回归验证

```powershell
python -m unittest scripts.test_regressions -v
```

脚本测试不能替代真实 RDC。交付前从最终 ZIP 解压运行，核对真实事件/参数和报告文件，分别报告通过、失败和环境阻塞，不将单个样本推广为所有 API/设备均已覆盖。
