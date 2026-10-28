# -*- coding: utf-8 -*-
"""XML 解析辅助函数与 Vulkan pipeline 解析。"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET

from .models import BlendInfo, DepthInfo


# ── 元素值提取 ────────────────────────────────────────────────────
# RenderDoc XML 中，资源属性以 <uint name="xxx">value</uint> 形式存储。
# first_text 按 tag+name 定位元素，int_named 转为整数。

def first_text(elem: ET.Element, tag: str, name: str) -> str:
    node = elem.find(f".//{tag}[@name='{name}']")
    return "" if node is None else (node.text or "").strip()


def bool_named(elem: ET.Element, name: str) -> bool:
    # VkBool32 is serialized as uint, unlike D3D bool fields.
    value = first_text(elem, "bool", name) or first_text(elem, "uint", name)
    return value.lower() in {"true", "1"}


def int_named(elem: ET.Element, name: str, default: int = 0) -> int:
    text = first_text(elem, "uint", name) or first_text(elem, "int", name)
    try:
        return int(text)
    except ValueError:
        return default


def optional_int_named(elem: ET.Element, name: str) -> int | None:
    text = first_text(elem, "uint", name) or first_text(elem, "int", name)
    if text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def int_attr(elem: ET.Element, name: str, default: int = 0) -> int:
    try:
        return int(elem.attrib.get(name, ""))
    except ValueError:
        return default


def attr_string(elem: ET.Element, name: str) -> str:
    node = elem.find(f".//*[@name='{name}']")
    if node is None:
        return ""
    return node.attrib.get("string") or (node.text or "").strip()


def resource_named(elem: ET.Element, name: str) -> str:
    node = elem.find(f".//ResourceId[@name='{name}']")
    return "0" if node is None else (node.text or "0").strip()


def _first_nonzero_resource(elem: ET.Element, *names: str) -> str:
    """返回第一个非 '0' 的 ResourceId 值。"""
    for name in names:
        val = resource_named(elem, name)
        if val and val != "0":
            return val
    return ""


def command_buffer_id(elem: ET.Element) -> str:
    cb = resource_named(elem, "commandBuffer")
    if cb != "0":
        return cb
    return resource_named(elem, "CommandBuffer")


def resource_ids_in_array(elem: ET.Element, array_name: str) -> list[str]:
    arr = elem.find(f".//array[@name='{array_name}']")
    if arr is None:
        return []
    return [(node.text or "0").strip() for node in arr.findall(".//ResourceId")]


# ── 资源解析 ────────────────────────────────────────────────────────

def parse_debug_name(elem: ET.Element) -> tuple[str, str] | None:
    rid = resource_named(elem, "pResource")
    name = first_text(elem, "string", "Name")
    if rid == "0" or not name:
        return None
    return rid, name


def parse_blend(elem: ET.Element) -> tuple[str, BlendInfo] | None:
    state = resource_named(elem, "pState")
    if state == "0":
        return None
    first_rt = elem.find(".//array[@name='RenderTarget']/struct")
    enabled = first_text(first_rt, "bool", "BlendEnable").lower() == "true" if first_rt is not None else False
    atc = first_text(elem, "bool", "AlphaToCoverageEnable").lower() == "true"
    return state, BlendInfo(enabled, atc)


def parse_depth(elem: ET.Element) -> tuple[str, DepthInfo] | None:
    state = resource_named(elem, "pState")
    if state == "0":
        return None
    return state, DepthInfo(
        depth_enable=first_text(elem, "bool", "DepthEnable").lower() == "true",
        write_mask=attr_string(elem, "DepthWriteMask"),
        func=attr_string(elem, "DepthFunc"),
    )


def parse_buffer(elem: ET.Element) -> tuple[str, int, str] | None:
    buf = resource_named(elem, "pBuffer")
    if buf == "0":
        return None
    return buf, int_named(elem, "ByteWidth"), attr_string(elem, "BindFlags")


# ── Blob 读取 ───────────────────────────────────────────────────────

def blob_id(elem: ET.Element, name: str) -> str:
    node = elem.find(f".//buffer[@name='{name}']")
    return "" if node is None else (node.text or "").strip()


def read_blob(zf: zipfile.ZipFile | None, blob: str) -> bytes:
    if not zf or not blob:
        return b""
    for name in [blob, blob.zfill(6)]:
        try:
            return zf.read(name)
        except KeyError:
            pass
    return b""


def clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().replace("|", "/")


# ── Vulkan pipeline 解析 ────────────────────────────────────────────
# Vulkan 的渲染状态（VS/PS/Blend/Depth/Topology）绑定在 pipeline 对象上，
# 而非像 D3D11 那样分别设置。需要先从 vkCreateGraphicsPipelines 解析出
# 每个 pipeline 的完整状态，再在 vkCmdBindPipeline 时关联到 command buffer。

def _parse_vk_pipeline_create_info(ci: ET.Element) -> dict[str, object]:
    """从单个 VkGraphicsPipelineCreateInfo 解析出 VS/PS/Blend/Depth/Topology。"""
    result: dict[str, object] = {}
    # 遍历 pStages 数组（array 或 struct 容器均可）
    stages_container = ci.find(".//array[@name='pStages']") or ci.find(".//struct[@name='Stages']")
    if stages_container is not None:
        for stage in stages_container.findall(".//struct"):
            stage_bit = attr_string(stage, "stage")
            if not stage_bit:
                # stage 可能是 enum 子元素
                stage_enum = stage.find(".//enum[@name='stage']")
                if stage_enum is not None:
                    stage_bit = stage_enum.get("string", "")
            module = resource_named(stage, "module")
            if module == "0":
                continue
            if "VERTEX" in stage_bit:
                result.setdefault("vs", module)
            elif "FRAGMENT" in stage_bit:
                result.setdefault("ps", module)
    # fallback：直接找第一个 module（兼容旧格式）
    if "vs" not in result and "ps" not in result:
        vs_node = ci.find(".//ResourceId[@name='module']")
        if vs_node is not None and (vs_node.text or "").strip() != "0":
            stage = ci.find(".//struct[@name='stage']")
            if stage is not None:
                stage_bit = attr_string(stage, "stage")
                if "VERTEX" in stage_bit:
                    result["vs"] = (vs_node.text or "").strip()
                elif "FRAGMENT" in stage_bit:
                    result["ps"] = (vs_node.text or "").strip()
    topo = attr_string(ci, "topology")
    if topo:
        result["topology"] = topo
    bs = ci.find(".//struct[@name='pColorBlendState']")
    if bs is not None:
        enabled = any((n.text or "").lower() in {"true", "1"}
                      for n in bs.findall(".//*[@name='blendEnable']"))
        atc = bool_named(ci, "alphaToCoverageEnable")
        result["blend"] = BlendInfo(enabled, atc)
    ds = ci.find(".//struct[@name='pDepthStencilState']")
    if ds is not None:
        de = bool_named(ds, "depthTestEnable")
        dw = bool_named(ds, "depthWriteEnable")
        result["depth"] = DepthInfo(de, "ALL" if dw else "NONE", "")
    return result


def parse_vk_pipeline_states(elem: ET.Element) -> dict[str, dict[str, object]]:
    """从 vkCreateGraphicsPipelines 元素中解析所有 pipeline 状态。"""
    states: dict[str, dict[str, object]] = {}
    # 批量创建：CreateInfos 数组
    for ci in elem.findall(".//struct[@name='CreateInfos']/struct"):
        pipe_id = _first_nonzero_resource(ci, "pPipeline", "Pipeline")
        if not pipe_id:
            continue
        states[pipe_id] = _parse_vk_pipeline_create_info(ci)
    # 单个创建：CreateInfo 结构
    ci = elem.find(".//struct[@name='CreateInfo']")
    if ci is not None:
        pipe_id = _first_nonzero_resource(ci, "pPipeline", "Pipeline")
        if not pipe_id:
            # Pipeline ID 可能在 chunk 顶层而非 CreateInfo 内部
            pipe_id = _first_nonzero_resource(elem, "pPipeline", "Pipeline")
        if pipe_id and pipe_id not in states:
            states[pipe_id] = _parse_vk_pipeline_create_info(ci)
    return states
