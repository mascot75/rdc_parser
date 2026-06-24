# -*- coding: utf-8 -*-
"""主解析逻辑：遍历 XML 生成 DrawRec 列表（纯解析，不含资源分类）。"""

from __future__ import annotations

import csv
import struct
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import fields
from pathlib import Path

from .models import (
    BlendInfo, DepthInfo, DrawRec, VkCmdState, HEADER_ZH,
    DRAW_CALLS, normalize_draw_type,
)
from .xml_helpers import (
    first_text, int_named, optional_int_named, int_attr, attr_string,
    resource_named, command_buffer_id, resource_ids_in_array,
    parse_debug_name, parse_blend, parse_depth, parse_buffer,
    blob_id, read_blob, clean_name, parse_vk_pipeline_states,
)
from .classifier import classify, normalize_pass_name


# ── 面数计算 ────────────────────────────────────────────────────────
# 面数有三种来源，优先级：draw_params > indirect_args > ib_capacity
# draw_params/indirect_args 从 Draw Call 参数精确计算，ib_capacity 从 buffer 大小反推（容量上限）

def face_estimate(
    draw_type: str,
    elem: ET.Element,
    topology: str,
    indirect_args: tuple[int, int, int, int, int] | None = None,
) -> tuple[str, bool, int, int, str]:
    """从 draw call 参数精确计算三角面数。

    返回 (面数str, 是否有效, 索引/顶点数, 实例数, 来源)。
    来源: "draw_params"（直接参数）| "indirect_args"（从 buffer 解码）| "unknown"。
    """
    topo = topology or "D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST(assumed)"
    inst = 1
    count = 0
    dt = draw_type
    if dt in ("ID3D11DeviceContext::DrawIndexedInstanced",):
        count = int_named(elem, "IndexCountPerInstance")
        inst = 1 if optional_int_named(elem, "InstanceCount") is None else int_named(elem, "InstanceCount")
    elif dt in ("ID3D11DeviceContext::DrawIndexed", "vkCmdDrawIndexed"):
        count = int_named(elem, "IndexCount") or int_named(elem, "indexCount")
        inst_value = optional_int_named(elem, "InstanceCount")
        if inst_value is None:
            inst_value = optional_int_named(elem, "instanceCount")
        inst = 1 if inst_value is None else inst_value
    elif dt in ("ID3D11DeviceContext::DrawInstanced",):
        count = int_named(elem, "VertexCountPerInstance")
        inst = 1 if optional_int_named(elem, "InstanceCount") is None else int_named(elem, "InstanceCount")
    elif dt in ("ID3D11DeviceContext::Draw", "vkCmdDraw"):
        count = int_named(elem, "VertexCount") or int_named(elem, "vertexCount")
        inst_value = optional_int_named(elem, "InstanceCount")
        if inst_value is None:
            inst_value = optional_int_named(elem, "instanceCount")
        inst = 1 if inst_value is None else inst_value
    elif dt in ("ID3D11DeviceContext::DrawIndexedInstancedIndirect", "vkCmdDrawIndexedIndirect") and indirect_args:
        count = indirect_args[0]
        inst = indirect_args[1] or 0
    elif dt in ("ID3D11DeviceContext::DrawInstancedIndirect", "vkCmdDrawIndirect") and indirect_args:
        count = indirect_args[0]
        inst = indirect_args[1] or 0
    else:
        return "", False, 0, 0, "unknown"
    if not count:
        return "", False, count, inst, "unknown"
    if "TRIANGLESTRIP" in topo:
        faces = max(count - 2, 0) * inst
    elif "TRIANGLELIST" in topo or "(assumed)" in topo:
        faces = (count * inst) / 3.0
    else:
        return "", False, count, inst, "unknown"
    source = "indirect_args" if "Indirect" in draw_type else "draw_params"
    return str(int(faces) if float(faces).is_integer() else round(faces, 2)), True, count, inst, source


def decode_indirect_args(data: bytes, offset: int, indexed: bool) -> tuple[int, int, int, int, int] | None:
    """解码 D3D11 indirect draw 参数。

    从 GPU buffer 二进制数据中读取 draw 参数（IndexCount/VertexCount, InstanceCount 等）。
    indexed=True 时格式为 5×uint32（DrawIndexedInstancedIndirect），否则 4×uint32（DrawInstancedIndirect）。
    """
    if offset < 0:
        return None
    if indexed:
        if len(data) < offset + 20:
            return None
        return struct.unpack_from("<IIIiI", data, offset)
    if len(data) < offset + 16:
        return None
    a, b, c, d = struct.unpack_from("<IIII", data, offset)
    return a, b, c, 0, d


def decode_indirect_args_vk(
    data: bytes, offset: int, indexed: bool, stride: int, draw_count: int,
) -> tuple[int, int, int, int, int] | None:
    """解码 Vulkan indirect draw 参数，支持 multi-draw。"""
    if offset < 0 or draw_count <= 0:
        return None
    weighted_count = 0
    total_instance = 0
    first_count = 0
    first_index = 0
    vertex_offset = 0
    first_instance = 0
    for i in range(draw_count):
        off = offset + i * stride
        if indexed:
            if len(data) < off + 20:
                break
            a, b, c, d, e = struct.unpack_from("<IIIiI", data, off)
            if i == 0:
                first_count = a
            weighted_count += a * b
            total_instance += b
            first_index = c
            vertex_offset = d
            first_instance = e
        else:
            if len(data) < off + 16:
                break
            a, b, c, d = struct.unpack_from("<IIII", data, off)
            if i == 0:
                first_count = a
            weighted_count += a * b
            total_instance += b
            first_index = c
            first_instance = d
    if weighted_count == 0:
        return None
    if draw_count == 1:
        return first_count, total_instance, first_index, vertex_offset, first_instance
    return weighted_count, 1, 0, 0, 0


def ib_capacity_faces(byte_width: int, index_format: str, topology: str) -> str:
    """根据 IndexBuffer 字节大小反推可容纳的最大面数。

    这是 buffer 容量上限，实际 Draw Call 可能只绘制了其中一部分。
    当 Draw 参数无法获取时（如未解码的 Indirect Draw），用此值作为回退。
    """
    if byte_width <= 0:
        return ""
    index_size = 4 if ("R32" in index_format or "UINT32" in index_format) else 2
    index_count = byte_width // index_size
    if index_count <= 0:
        return ""
    if "TRIANGLESTRIP" in (topology or ""):
        faces = max(index_count - 2, 0)
    else:
        faces = index_count / 3.0
    return str(int(faces) if float(faces).is_integer() else round(faces, 2))


# ── Vulkan secondary command buffer 预扫描 ──────────────────────────

def pre_scan_vk_execute_contexts(xml: Path) -> dict[str, list[str]]:
    """预扫描 vkCmdExecuteCommands，将 secondary CB 映射到主 marker stack。"""
    marker_stacks: dict[str, list[str]] = {}
    execute_contexts: dict[str, list[str]] = {}
    try:
        for _, elem in ET.iterparse(xml, events=("end",)):
            if elem.tag != "chunk":
                continue
            cname = elem.attrib.get("name", "")
            if cname == "vkCmdBeginDebugUtilsLabelEXT":
                cb = command_buffer_id(elem)
                lbl = elem.find(".//struct[@name='Label']")
                marker = attr_string(lbl, "pLabelName") if lbl is not None else ""
                if cb != "0" and marker:
                    marker_stacks.setdefault(cb, []).append(clean_name(marker))
            elif cname == "vkCmdEndDebugUtilsLabelEXT":
                cb = command_buffer_id(elem)
                stack = marker_stacks.setdefault(cb, [])
                if stack:
                    stack.pop()
            elif cname == "vkCmdExecuteCommands":
                parent_cb = command_buffer_id(elem)
                parent_stack = marker_stacks.get(parent_cb, [])
                if parent_stack:
                    for child_cb in resource_ids_in_array(elem, "pCommandBuffers"):
                        if child_cb != "0":
                            execute_contexts[child_cb] = list(parent_stack)
            elem.clear()
    except ET.ParseError:
        pass
    return execute_contexts


# ── 主解析 ──────────────────────────────────────────────────────────

def analyze(xml: Path) -> list[DrawRec]:
    """流式解析 XML，提取所有 draw call 信息。

    遍历 RenderDoc 导出的 XML，维护 D3D11/Vulkan 的渲染状态机（VB/IB/Shader/Blend/Depth/RT），
    在遇到 draw call 时快照当前状态生成 DrawRec。不含资源分类（由 classify_step 完成）。
    """
    zip_path = find_zip_for_xml(xml)
    zf = zipfile.ZipFile(zip_path) if zip_path else None
    vk_execute_contexts = pre_scan_vk_execute_contexts(xml)

    # 状态机：遍历 XML 元素，跟踪当前渲染状态，遇到 draw call 时生成记录
    # D3D11 状态用全局变量跟踪，Vulkan 用 vk_states dict 按 command buffer 隔离
    names: dict[str, str] = {}                  # ResourceID → 调试名称（SetDebugName）
    buffer_sizes: dict[str, int] = {}           # BufferID → 字节大小
    buffer_bind_flags: dict[str, str] = {}      # BufferID → 绑定标志
    buffer_contents: dict[str, bytearray] = {}  # BufferID → 内容快照（用于解码 indirect args）
    rt_views: dict[str, str] = {}
    blend_states: dict[str, BlendInfo] = {"0": BlendInfo()}
    depth_states: dict[str, DepthInfo] = {"0": DepthInfo(False, "", "")}

    marker_stack: list[str] = []  # D3D11 marker 层级栈（BeginEvent/EndEvent）
    topology = "D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST(assumed)"  # 当前图元拓扑
    vb_ids = ["0"]    # 当前绑定的顶点 Buffer ID 列表
    ib_id = "0"       # 当前绑定的索引 Buffer ID
    ib_format = "DXGI_FORMAT_R16_UINT"  # 索引格式（R16=2字节, R32=4字节）
    vs_id = "0"
    ps_id = "0"
    blend_state = "0"
    depth_state = "0"
    current_rt = "0"
    pipeline_states: dict[str, dict[str, object]] = {}
    vk_states: dict[str, VkCmdState] = {}
    vk_marker_stacks: dict[str, list[str]] = {}
    records: list[DrawRec] = []

    for _, elem in ET.iterparse(xml, events=("end",)):
        if elem.tag != "chunk":
            continue
        cname = elem.attrib.get("name", "")

        # ── Names ──
        if cname == "ID3D11Resource::SetDebugName":
            parsed = parse_debug_name(elem)
            if parsed:
                names[parsed[0]] = clean_name(parsed[1])
        elif cname == "vkSetDebugUtilsObjectNameEXT":
            obj = resource_named(elem, "Object")
            oname = attr_string(elem, "ObjectName")
            if obj != "0" and oname:
                names[obj] = clean_name(oname)

        # ── Buffers ──
        elif cname == "ID3D11Device::CreateBuffer":
            parsed_buf = parse_buffer(elem)
            if parsed_buf:
                rid, byte_width, bind_flags = parsed_buf
                buffer_sizes[rid] = byte_width
                buffer_bind_flags[rid] = bind_flags
                data = read_blob(zf, blob_id(elem, "InitialData"))
                if data:
                    content = bytearray(byte_width or len(data))
                    content[:min(len(content), len(data))] = data[:min(len(content), len(data))]
                    buffer_contents[rid] = content
        elif cname == "vkCreateBuffer":
            buf_id = resource_named(elem, "Buffer")
            if buf_id and buf_id != "0":
                ci = elem.find(".//struct[@name='CreateInfo']")
                if ci is not None:
                    size = int_named(ci, "size")
                    usage = int_named(ci, "usage")
                    if size:
                        buffer_sizes[buf_id] = size
                        buffer_bind_flags[buf_id] = f"VkBufferUsageFlags({usage})" if usage else ""

        # ── RT views / render targets ──
        elif cname == "ID3D11Device::CreateRenderTargetView":
            view = resource_named(elem, "pView")
            res = resource_named(elem, "pResource")
            rt_views[view] = res
        elif cname == "ID3D11Device::CreateBlendState":
            parsed = parse_blend(elem)
            if parsed:
                blend_states[parsed[0]] = parsed[1]
        elif cname == "ID3D11Device::CreateDepthStencilState":
            parsed = parse_depth(elem)
            if parsed:
                depth_states[parsed[0]] = parsed[1]

        # ── Vulkan pipeline ──
        elif cname == "vkCreateGraphicsPipelines":
            pipeline_states.update(parse_vk_pipeline_states(elem))

        # ── Markers ──
        elif cname == "ID3DUserDefinedAnnotation::BeginEvent":
            marker = first_text(elem, "string", "MarkerName")
            if marker:
                marker_stack.append(clean_name(marker))
        elif cname == "ID3DUserDefinedAnnotation::EndEvent":
            if marker_stack:
                marker_stack.pop()
        elif cname == "vkCmdBeginDebugUtilsLabelEXT":
            lbl = elem.find(".//struct[@name='Label']")
            if lbl is not None:
                marker = attr_string(lbl, "pLabelName")
                if marker:
                    cb = command_buffer_id(elem)
                    vk_marker_stacks.setdefault(cb, []).append(clean_name(marker))
        elif cname == "vkCmdEndDebugUtilsLabelEXT":
            cb = command_buffer_id(elem)
            stack = vk_marker_stacks.setdefault(cb, [])
            if stack:
                stack.pop()

        # ── Topology ──
        elif cname == "ID3D11DeviceContext::IASetPrimitiveTopology":
            topology = attr_string(elem, "Topology") or attr_string(elem, "PrimitiveTopology") or topology
        elif cname == "ID3D11DeviceContext::IASetVertexBuffers":
            ids = resource_ids_in_array(elem, "ppVertexBuffers")
            if ids:
                start = int_named(elem, "StartSlot")
                num = int_named(elem, "NumBuffers", len(ids))
                if len(vb_ids) < start + num:
                    vb_ids += ["0"] * (start + num - len(vb_ids))
                for i, rid in enumerate(ids[:num]):
                    vb_ids[start + i] = rid
        elif cname == "ID3D11DeviceContext::IASetIndexBuffer":
            ib_id = resource_named(elem, "pIndexBuffer")
            ib_format = attr_string(elem, "Format") or ib_format
        elif cname == "ID3D11DeviceContext::VSSetShader":
            vs_id = resource_named(elem, "pShader")
        elif cname == "ID3D11DeviceContext::PSSetShader":
            ps_id = resource_named(elem, "pShader")
        elif cname == "ID3D11DeviceContext::OMSetBlendState":
            blend_state = resource_named(elem, "pBlendState")
        elif cname == "ID3D11DeviceContext::OMSetDepthStencilState":
            depth_state = resource_named(elem, "pDepthStencilState")
        elif cname == "ID3D11DeviceContext::OMSetRenderTargets":
            ids = resource_ids_in_array(elem, "ppRenderTargetViews")
            current_rt = ids[0] if ids else "0"

        # ── Vulkan state bindings ──
        elif cname == "vkCmdBindVertexBuffers":
            st = vk_states.setdefault(command_buffer_id(elem), VkCmdState())
            ids = resource_ids_in_array(elem, "pBuffers")
            if ids:
                start = int_named(elem, "firstBinding")
                num = int_named(elem, "bindingCount", len(ids))
                if len(st.vb_ids) < start + num:
                    st.vb_ids += ["0"] * (start + num - len(st.vb_ids))
                for i, rid in enumerate(ids[:num]):
                    st.vb_ids[start + i] = rid
        elif cname == "vkCmdBindIndexBuffer":
            st = vk_states.setdefault(command_buffer_id(elem), VkCmdState())
            st.ib_id = resource_named(elem, "buffer")
            itype = attr_string(elem, "indexType")
            if "UINT32" in (itype or ""):
                st.ib_format = "VK_INDEX_TYPE_UINT32"
            elif "UINT16" in (itype or ""):
                st.ib_format = "VK_INDEX_TYPE_UINT16"
            else:
                st.ib_format = itype or st.ib_format
        elif cname == "vkCmdBindPipeline":
            st = vk_states.setdefault(command_buffer_id(elem), VkCmdState())
            bp = attr_string(elem, "pipelineBindPoint")
            pipe_id = resource_named(elem, "pipeline")
            if bp and "GRAPHICS" in bp and pipe_id in pipeline_states:
                ps = pipeline_states[pipe_id]
                st.vs_id = str(ps.get("vs", st.vs_id))
                st.ps_id = str(ps.get("ps", st.ps_id))
                b = ps.get("blend")
                if isinstance(b, BlendInfo):
                    bkey = f"vk_pipe_blend_{pipe_id}"
                    blend_states[bkey] = b
                    st.blend_state = bkey
                d = ps.get("depth")
                if isinstance(d, DepthInfo):
                    dkey = f"vk_pipe_depth_{pipe_id}"
                    depth_states[dkey] = d
                    st.depth_state = dkey
                topo = str(ps.get("topology", ""))
                if topo:
                    st.topology = topo
            elif bp and "GRAPHICS" in bp and pipe_id != "0":
                st.vs_id = pipe_id
                st.ps_id = pipe_id
        elif cname in {"vkCmdBeginRenderPass", "vkCmdBeginRenderPass2"}:
            st = vk_states.setdefault(command_buffer_id(elem), VkCmdState())
            rp_begin = elem.find(".//struct[@name='RenderPassBegin']") or elem.find(".//struct[@name='renderPassBegin']")
            if rp_begin is None:
                rp_begin = elem
            if rp_begin is not None:
                fb = resource_named(rp_begin, "framebuffer")
                if fb and fb != "0":
                    st.current_rt = fb
        elif cname == "vkBeginCommandBuffer":
            st = vk_states.setdefault(command_buffer_id(elem), VkCmdState())
            inherit = elem.find(".//struct[@name='pInheritanceInfo']")
            if inherit is not None:
                fb = resource_named(inherit, "framebuffer")
                if fb and fb != "0":
                    st.current_rt = fb

        # ── Buffer content updates ──
        elif cname == "ID3D11DeviceContext::UpdateSubresource":
            dst = resource_named(elem, "pDstResource")
            if dst != "0":
                data = read_blob(zf, blob_id(elem, "pSrcData"))
                if data:
                    size = max(buffer_sizes.get(dst, 0), len(data))
                    content = buffer_contents.get(dst, bytearray(size))
                    if len(content) < size:
                        content.extend(b"\x00" * (size - len(content)))
                    content[:len(data)] = data
                    buffer_contents[dst] = content
        elif cname == "ID3D11DeviceContext::Unmap":
            res = resource_named(elem, "pResource")
            if res != "0":
                data = read_blob(zf, blob_id(elem, "MapWrittenData"))
                if data:
                    start_off = int_named(elem, "Byte offset to start of written data")
                    end_off = int_named(elem, "Byte offset to end of written data", start_off + len(data))
                    size = max(buffer_sizes.get(res, 0), end_off, start_off + len(data))
                    content = buffer_contents.get(res, bytearray(size))
                    if len(content) < size:
                        content.extend(b"\x00" * (size - len(content)))
                    write_len = min(len(data), max(end_off - start_off, 0))
                    content[start_off:start_off + write_len] = data[:write_len]
                    buffer_contents[res] = content

        # ── Draw calls：快照当前状态，计算面数，生成 DrawRec ────────
        elif cname in DRAW_CALLS:
            is_vk = cname.startswith("vkCmd")
            cb_id = command_buffer_id(elem) if is_vk else "0"
            vk_state = vk_states.setdefault(cb_id, VkCmdState()) if is_vk else None

            if is_vk:
                inherited_stack = vk_execute_contexts.get(cb_id, [])
                local_stack = vk_marker_stacks.setdefault(cb_id, [])
                active_marker_stack = list(inherited_stack)
                for item in local_stack:
                    if not active_marker_stack or active_marker_stack[-1] != item:
                        active_marker_stack.append(item)
            else:
                active_marker_stack = marker_stack

            active_topology = vk_state.topology if vk_state else topology
            active_vb_ids = vk_state.vb_ids if vk_state else vb_ids
            active_ib_id = vk_state.ib_id if vk_state else ib_id
            active_ib_format = vk_state.ib_format if vk_state else ib_format
            active_vs_id = vk_state.vs_id if vk_state else vs_id
            active_ps_id = vk_state.ps_id if vk_state else ps_id
            active_blend_state = vk_state.blend_state if vk_state else blend_state
            active_depth_state = vk_state.depth_state if vk_state else depth_state
            active_rt = vk_state.current_rt if vk_state else current_rt

            marker = clean_name(" / ".join(active_marker_stack)) or "(no marker)"
            pass_name = normalize_pass_name(marker)
            vs_name = "(no VS)" if is_vk and active_vs_id == "0" else names.get(active_vs_id, f"VS {active_vs_id}")
            ps_name = "(no PS)" if is_vk and active_ps_id == "0" else names.get(active_ps_id, f"PS {active_ps_id}")
            shader_source = ps_name if clean_name(ps_name) not in {"", "(no PS)"} else vs_name
            rt_res = rt_views.get(active_rt, "0")
            rt_name = names.get(active_rt) or names.get(rt_res) or (
                (f"Framebuffer {active_rt}" if is_vk else f"RTV {active_rt}") if active_rt != "0" else "(no color RT)"
            )
            mesh_type = classify(marker, shader_source, rt_name)
            vb0 = active_vb_ids[0] if active_vb_ids else "0"
            vb0_name = names.get(vb0, f"Buffer {vb0}")
            ib_name = names.get(active_ib_id, f"Buffer {active_ib_id}")

            indirect_args = None
            if cname in {"ID3D11DeviceContext::DrawIndexedInstancedIndirect", "ID3D11DeviceContext::DrawInstancedIndirect"}:
                args_buffer = resource_named(elem, "pBufferForArgs")
                args_offset = int_named(elem, "AlignedByteOffsetForArgs")
                indirect_args = decode_indirect_args(
                    bytes(buffer_contents.get(args_buffer, b"")),
                    args_offset,
                    cname == "ID3D11DeviceContext::DrawIndexedInstancedIndirect",
                )
            elif cname in {"vkCmdDrawIndexedIndirect", "vkCmdDrawIndirect"}:
                args_buffer = resource_named(elem, "buffer")
                args_offset = int_named(elem, "offset")
                stride = int_named(elem, "stride", 20)
                draw_count = int_named(elem, "count", 1)
                indirect_args = decode_indirect_args_vk(
                    bytes(buffer_contents.get(args_buffer, b"")),
                    args_offset,
                    indexed=(cname == "vkCmdDrawIndexedIndirect"),
                    stride=stride,
                    draw_count=draw_count,
                )

            faces, known, count, inst, face_src = face_estimate(cname, elem, active_topology, indirect_args)
            ib_faces = ib_capacity_faces(buffer_sizes.get(active_ib_id, 0), active_ib_format, active_topology)
            # 采用面数：优先用 Draw 参数计算值，不可用时回退到 IB 容量
            combined_faces = faces if known and faces else ib_faces
            blend = blend_states.get(active_blend_state, BlendInfo())
            depth = depth_states.get(active_depth_state, DepthInfo())

            records.append(DrawRec(
                event=int_attr(elem, "chunkIndex", len(records) + 1),
                draw_type=normalize_draw_type(cname),
                mesh_type=mesh_type,
                asset_module="",
                asset_module_source="",
                resource_type="",
                resource_category="",
                resource_path="",
                matched_mesh_name="",
                resource_match_source="",
                mesh_key="",
                marker=marker,
                pass_name=pass_name,
                topology=active_topology,
                face_estimate=faces,
                face_known=known,
                face_source=face_src,
                indirect_args_decoded=indirect_args is not None,
                ib_capacity_faces_estimate=ib_faces,
                combined_faces_estimate=combined_faces,
                index_or_vertex_count=count,
                instance_count=inst,
                vb0=vb0_name,
                ib=ib_name,
                vs=vs_name,
                ps=ps_name,
                raw_ps=ps_name,
                blend=blend.enabled or blend.alpha_to_coverage,
                depth_write="ALL" in depth.write_mask,
                depth_enable=depth.depth_enable,
                rt=clean_name(rt_name),
            ))
        elem.clear()

    if zf:
        zf.close()
    return records


def find_zip_for_xml(xml: Path) -> Path | None:
    """查找 XML 对应的 zip 二进制数据文件。"""
    candidates = [
        xml.with_suffix(""),
        xml.with_name(xml.stem),
        xml.with_suffix(".zip"),
        xml.with_name(xml.stem + ".zip"),
    ]
    for item in candidates:
        if item.exists() and zipfile.is_zipfile(item):
            return item
    return None


# ── CSV 读写 ────────────────────────────────────────────────────────

DRAWCALLS_CSV_FIELDS = [
    "event", "draw_type", "mesh_type", "marker", "pass_name", "topology",
    "face_estimate", "face_known", "face_source", "indirect_args_decoded",
    "ib_capacity_faces_estimate", "combined_faces_estimate",
    "index_or_vertex_count", "instance_count",
    "vb0", "ib", "vs", "ps", "blend", "depth_write", "depth_enable", "rt",
]


def write_drawcalls_csv(path: Path, records: list[DrawRec]) -> None:
    """将解析结果写入 drawcalls.csv（不含分类字段）。"""
    headers = [HEADER_ZH.get(k, k) for k in DRAWCALLS_CSV_FIELDS]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in records:
            w.writerow([getattr(r, k) for k in DRAWCALLS_CSV_FIELDS])


def load_drawcalls_csv(path: Path) -> list[DrawRec]:
    """从 drawcalls.csv 加载解析结果（分类字段为空）。"""
    records: list[DrawRec] = []
    zh_to_en = {v: k for k, v in HEADER_ZH.items()}
    field_names = {f.name for f in fields(DrawRec)}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # 预构建列名映射：英文字段名 → CSV列名
        col_map: dict[str, str] = {}
        for col in (reader.fieldnames or []):
            if col in field_names:
                col_map[col] = col  # CSV 列名本身就是英文
            elif col in zh_to_en:
                col_map[zh_to_en[col]] = col  # CSV 列名是中文 → 映射英文key到中文列
        def get(row: dict, key: str) -> str:
            csv_col = col_map.get(key)
            if csv_col:
                val = row.get(csv_col, "")
                if val:
                    return val
            return ""
        for row in reader:
            records.append(DrawRec(
                event=int(get(row, "event") or "0"),
                draw_type=get(row, "draw_type"),
                mesh_type=get(row, "mesh_type"),
                asset_module="",
                asset_module_source="",
                resource_type="",
                resource_category="",
                resource_path="",
                matched_mesh_name="",
                resource_match_source="",
                mesh_key="",
                marker=get(row, "marker"),
                pass_name=get(row, "pass_name"),
                topology=get(row, "topology"),
                face_estimate=get(row, "face_estimate"),
                face_known=get(row, "face_known").lower() in ("true", "1", "是"),
                face_source=get(row, "face_source"),
                indirect_args_decoded=get(row, "indirect_args_decoded").lower() in ("true", "1", "是"),
                ib_capacity_faces_estimate=get(row, "ib_capacity_faces_estimate"),
                combined_faces_estimate=get(row, "combined_faces_estimate"),
                index_or_vertex_count=int(get(row, "index_or_vertex_count") or "0"),
                instance_count=int(get(row, "instance_count") or "0"),
                vb0=get(row, "vb0"),
                ib=get(row, "ib"),
                vs=get(row, "vs"),
                ps=get(row, "ps"),
                raw_ps=get(row, "raw_ps") or get(row, "ps"),
                blend=get(row, "blend").lower() in ("true", "1", "是"),
                depth_write=get(row, "depth_write").lower() in ("true", "1", "是"),
                depth_enable=get(row, "depth_enable").lower() in ("true", "1", "是"),
                rt=get(row, "rt"),
            ))
    return records
