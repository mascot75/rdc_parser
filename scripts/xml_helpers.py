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
    vs_node = ci.find(".//ResourceId[@name='module']")
    if vs_node is not None and (vs_node.text or "").strip() != "0":
        stage = ci.find(".//struct[@name='stage']")
        if stage is not None:
            stage_bit = attr_string(stage, "stage")
            if "VERTEX" in stage_bit:
                result["vs"] = (vs_node.text or "").strip()
            elif "FRAGMENT" in stage_bit:
                result["ps"] = (vs_node.text or "").strip()
    for stage in ci.findall(".//struct[@name='Stages']//struct"):
        stage_bit = attr_string(stage, "stage")
        module = resource_named(stage, "module")
        if module == "0":
            continue
        if "VERTEX" in stage_bit:
            result.setdefault("vs", module)
        elif "FRAGMENT" in stage_bit:
            result.setdefault("ps", module)
    topo = attr_string(ci, "topology")
    if topo:
        result["topology"] = topo
    bs = ci.find(".//struct[@name='pColorBlendState']")
    if bs is not None:
        enabled = first_text(bs, "bool", "blendEnable").lower() == "true"
        atc = first_text(bs, "bool", "alphaToCoverageEnable").lower() == "true"
        result["blend"] = BlendInfo(enabled, atc)
    ds = ci.find(".//struct[@name='pDepthStencilState']")
    if ds is not None:
        de = first_text(ds, "bool", "depthTestEnable").lower() == "true"
        dw = first_text(ds, "bool", "depthWriteEnable").lower() == "true"
        result["depth"] = DepthInfo(de, "ALL" if dw else "NONE", "")
    return result


def parse_vk_pipeline_states(elem: ET.Element) -> dict[str, dict[str, object]]:
    """从 vkCreateGraphicsPipelines 元素中解析所有 pipeline 状态。"""
    states: dict[str, dict[str, object]] = {}
    for ci in elem.findall(".//struct[@name='CreateInfos']/struct"):
        pipe_id = resource_named(ci, "pPipeline") or resource_named(ci, "Pipeline")
        if not pipe_id or pipe_id == "0":
            continue
        states[pipe_id] = _parse_vk_pipeline_create_info(ci)
    pipe_id = resource_named(elem, "pPipeline")
    if pipe_id and pipe_id != "0" and pipe_id not in states:
        ci = elem.find(".//struct[@name='CreateInfo']")
        if ci is not None:
            states[pipe_id] = _parse_vk_pipeline_create_info(ci)
    return states
