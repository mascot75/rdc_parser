# -*- coding: utf-8 -*-
"""数据结构定义、常量和通用聚合逻辑。"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass


# ── Draw call 名称集合 ──────────────────────────────────────────────

DRAW_CALLS = {
    # D3D11
    "ID3D11DeviceContext::Draw",
    "ID3D11DeviceContext::DrawAuto",
    "ID3D11DeviceContext::DrawIndexed",
    "ID3D11DeviceContext::DrawInstanced",
    "ID3D11DeviceContext::DrawIndexedInstanced",
    "ID3D11DeviceContext::DrawIndexedInstancedIndirect",
    "ID3D11DeviceContext::DrawInstancedIndirect",
    # Vulkan
    "vkCmdDraw",
    "vkCmdDrawIndexed",
    "vkCmdDrawIndirect",
    "vkCmdDrawIndexedIndirect",
}


def normalize_draw_type(cname: str) -> str:
    """将 D3D11/Vulkan draw call 名称归一化为短类型名。

    如 "ID3D11DeviceContext::DrawIndexed" → "DrawIndexed",
    "vkCmdDrawIndexed" → "DrawIndexed"。
    """
    vulkan_map = {
        "vkCmdDraw": "Draw",
        "vkCmdDrawIndexed": "DrawIndexed",
        "vkCmdDrawIndirect": "DrawIndirect",
        "vkCmdDrawIndexedIndirect": "DrawIndexedIndirect",
    }
    if cname in vulkan_map:
        return vulkan_map[cname]
    return cname.replace("ID3D11DeviceContext::", "")


# ── 数据结构 ────────────────────────────────────────────────────────

@dataclass
class BlendInfo:
    enabled: bool = False
    alpha_to_coverage: bool = False


@dataclass
class DepthInfo:
    depth_enable: bool = False
    write_mask: str = ""
    func: str = ""


@dataclass
class VkCmdState:
    topology: str = "D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST(assumed)"
    vb_ids: list[str] | None = None
    ib_id: str = "0"
    ib_format: str = "DXGI_FORMAT_R16_UINT"
    vs_id: str = "0"
    ps_id: str = "0"
    blend_state: str = "0"
    depth_state: str = "0"
    current_rt: str = "0"

    def __post_init__(self) -> None:
        if self.vb_ids is None:
            self.vb_ids = ["0"]


@dataclass
class DrawRec:
    """单个 Draw Call 的完整渲染状态快照。

    由 analyzer.py 在遍历 XML 时生成，经 classify_step.py 补全资源分类信息。
    """
    event: int
    draw_type: str
    mesh_type: str
    asset_module: str
    asset_module_source: str
    resource_type: str
    resource_category: str
    resource_path: str
    matched_mesh_name: str
    resource_match_source: str
    mesh_key: str
    marker: str
    pass_name: str
    topology: str
    # ── 面数相关字段（单条 Draw Call 级别）──────────────────────────
    #
    # 面数有三种值，含义不同：
    #   face_estimate          — GPU 实际绘制的面数 = IndexCount × InstanceCount / 3
    #                            直接 Draw 从参数读取，Indirect Draw 从 GPU buffer 解码
    #   ib_capacity_faces      — IndexBuffer 容量上限 = Buffer字节数 / 索引格式大小 / 3
    #                            这是 buffer 能容纳的最大面数，实际绘制可能只用了部分索引
    #   combined_faces         — 采用面数：优先用 face_estimate，不可用时回退到 ib_capacity
    #
    # face_source 标记 face_estimate 的数据来源：
    #   "draw_params"    — 从 Draw Call 参数直接读取（最可靠）
    #   "indirect_args"  — 从 GPU buffer 解码（Indirect Draw，可能有解码误差）
    #   "unknown"        — 无法获取（Draw 类型不支持或参数缺失）
    #
    # 注意：同一个 mesh 可能有多个 Draw Call（不同 Pass、不同 LOD 区域），
    #       每个 Draw Call 的 face_estimate 可能远小于 IB 容量，
    #       因为 GPU 可能只渲染了 mesh 的一部分索引范围。
    face_estimate: str
    face_known: bool
    face_source: str
    indirect_args_decoded: bool
    ib_capacity_faces_estimate: str
    combined_faces_estimate: str
    index_or_vertex_count: int
    instance_count: int
    vb0: str
    ib: str
    vs: str
    ps: str
    raw_ps: str
    blend: bool
    depth_write: bool
    depth_enable: bool
    rt: str


HEADER_ZH = {
    "event": "事件序号",
    "draw_type": "Draw类型",
    "mesh_type": "Mesh渲染类型",
    "asset_module": "资产模块",
    "asset_module_source": "资产模块来源",
    "resource_type": "资源类型",
    "resource_category": "资源分类",
    "resource_path": "资源路径",
    "matched_mesh_name": "匹配Mesh名",
    "resource_match_source": "资源分类来源",
    "mesh_key": "近似MeshKey",
    "marker": "Marker路径",
    "pass_name": "Pass简称",
    "topology": "拓扑类型",
    "face_estimate": "Draw参数面数",
    "face_known": "Draw参数面数可用",
    "face_source": "面数来源",
    "indirect_args_decoded": "Indirect参数是否解码",
    "ib_capacity_faces_estimate": "IndexBuffer面数",
    "combined_faces_estimate": "采用面数",
    "index_or_vertex_count": "索引/顶点数量",
    "instance_count": "实例数",
    "vb0": "顶点Buffer0",
    "ib": "索引Buffer",
    "vs": "VertexShader",
    "ps": "PixelShader",
    "raw_ps": "原始Shader",
    "blend": "是否混合",
    "depth_write": "是否写深度",
    "depth_enable": "是否启用深度",
    "rt": "RenderTarget",
}


# ── Mesh 聚合统计 ──────────────────────────────────────────────────

@dataclass
class MeshStat:
    """按 VB+IB 聚合后的 mesh 级别统计。

    面数字段说明（mesh 级别，聚合了该 mesh 所有 Draw Call）：
      ib_faces          — IndexBuffer 容量面数（不含实例数）
                          这是 buffer 能容纳的最大面数，共享 buffer 可能远大于实际 mesh
      draw_params_faces — Draw参数面数之和（face_estimate，含实例数）
                          仅统计有 Draw 参数的 DrawCall，不含回退到 IB 的部分
      combined_faces    — 采用面数之和（combined_faces_estimate，含实例数）
                          优先用 draw_params_faces，不可用时回退到 ib_capacity
      single_faces      — 单实例面数 = combined_faces / instance_count
                          用于面数标准对比
      face_source        — 面数来源：draw_params（直接参数）/ indirect_args（GPU 间接参数）
                          / mixed（两种都有）/ unknown
    """
    name: str
    path: str
    lod_level: int
    draw_params_faces: int  # Draw参数面数之和（face_estimate，含实例数，不含回退）
    combined_faces: int     # 采用面数之和（combined_faces_estimate，含实例数，含回退）
    single_faces: int       # 单实例面数（combined_faces / instance_count）
    ib_faces: int           # IndexBuffer 容量面数（不含实例数）
    instance_count: int
    drawcall_count: int
    face_source: str        # 面数来源：draw_params / indirect_args / mixed / unknown
    resource_type: str
    resource_category: str
    asset_module: str

    @property
    def total_faces(self) -> int:
        """兼容旧代码：等同 combined_faces。"""
        return self.combined_faces


def detect_lod(mesh_name: str) -> int:
    """从 mesh 名检测 LOD 等级（匹配 _LOD0, @LOD1 等模式）。"""
    match = re.search(r'[_@]LOD(\d+)', mesh_name, re.IGNORECASE)
    return int(match.group(1)) if match else 0


def aggregate_mesh_stats(records: list[DrawRec]) -> list[tuple[MeshStat, list[DrawRec]]]:
    """将 DrawRec 列表按 VB+IB 聚合为 mesh 级别统计。

    同一 mesh 可能因不同 shader/pass 被拆成多条 DrawRec，
    用 vb0+ib 作为唯一标识聚合。

    面数计算逻辑：
      ib_faces          — 取第一条 DrawRec 的 ib_capacity_faces_estimate（IB 容量，不含实例数）
      draw_params_faces — 所有 DrawRec 的 face_estimate 之和（仅已知面数的，不含回退）
      combined_faces    — 所有 DrawRec 的 combined_faces_estimate 之和（含回退到 IB 的部分）
      single_faces      — combined_faces / instance_count
      face_source        — 所有 DrawRec 中出现最多的 face_source，有多种则为 "mixed"

    返回按 combined_faces 降序排列的 (MeshStat, 该mesh的DrawRec列表) 元组。
    """
    groups: dict[str, list[DrawRec]] = defaultdict(list)
    for r in records:
        groups[f"{r.vb0}|{r.ib}"].append(r)

    results: list[tuple[MeshStat, list[DrawRec]]] = []
    for key, recs in groups.items():
        first = recs[0]
        name = first.matched_mesh_name or first.vb0
        instance_count = sum(r.instance_count for r in recs)

        # ib_faces: IB 容量面数 = Buffer字节数 / 索引格式大小 / 3
        # 这是 buffer 能容纳的最大面数，不含实例数
        # 注意：共享 buffer 的容量可能远大于实际 mesh 面数
        ib_faces = 0
        for r in recs:
            if r.ib_capacity_faces_estimate:
                try:
                    ib_faces = int(float(r.ib_capacity_faces_estimate))
                except (ValueError, TypeError):
                    pass
                break

        # draw_params_faces: Draw参数面数之和（仅 face_estimate，不含回退到 IB 的部分）
        draw_params_faces = sum(int(float(r.face_estimate)) for r in recs if r.face_known and r.face_estimate)

        # combined_faces: 采用面数之和（combined_faces_estimate，优先用 face_estimate，回退到 ib_capacity）
        combined_faces = sum(int(float(r.combined_faces_estimate)) for r in recs if r.combined_faces_estimate)

        # single_faces: 单实例面数 = 采用面数 / 实例数
        if combined_faces and instance_count > 0:
            single_faces = combined_faces // instance_count
        else:
            single_faces = ib_faces  # 无 Draw 参数时回退到 IB 容量

        # face_source: 取所有 DrawCall 中出现最多的面数来源
        # 有多种来源时标记为 "mixed"（如同一 mesh 既有 direct draw 又有 indirect draw）
        source_counter: dict[str, int] = {}
        for r in recs:
            src = r.face_source or "unknown"
            source_counter[src] = source_counter.get(src, 0) + 1
        face_source = max(source_counter, key=source_counter.get) if source_counter else "unknown"
        if len(source_counter) > 1:
            face_source = "mixed"

        stat = MeshStat(
            name=name,
            path=first.resource_path,
            lod_level=detect_lod(name),
            draw_params_faces=draw_params_faces,
            combined_faces=combined_faces,
            single_faces=single_faces,
            ib_faces=ib_faces,
            instance_count=instance_count,
            drawcall_count=len(recs),
            face_source=face_source,
            resource_type=first.resource_type,
            resource_category=first.resource_category,
            asset_module=first.asset_module,
        )
        results.append((stat, recs))

    results.sort(key=lambda x: -x[0].total_faces)
    return results
