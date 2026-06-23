# -*- coding: utf-8 -*-
"""renderdoccmd 定位与 .rdc → .xml 转换。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path


def find_renderdoccmd(explicit: str | None = None) -> str:
    """查找 renderdoccmd.exe，支持显式路径、PATH 搜索、常见安装目录。"""
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates += [
        r"C:\Program Files\RenderDoc\renderdoccmd.exe",
        r"D:\Games\Development\renderdoccmd.exe",
        r"E:\rdc\Development\Development\renderdoccmd.exe",
        r"C:\Program Files\RenderDoc\x86\renderdoccmd.exe",
    ]
    found = shutil.which("renderdoccmd")
    if found:
        candidates.append(found)
    for item in candidates:
        if item and Path(item).exists():
            return str(Path(item))
    raise FileNotFoundError("找不到 renderdoccmd.exe，请安装 RenderDoc 或用 --renderdoccmd 指定路径。")


def path_for_external_tool(path: Path) -> str:
    """返回绝对路径，不解析 Windows subst/junction 目标。"""
    return os.path.abspath(os.fspath(path))


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


def has_zip_blob(xml: Path) -> bool:
    return find_zip_for_xml(xml) is not None


def ensure_xml(input_path: Path, out_dir: Path, force_convert: bool, renderdoccmd: str | None) -> Path:
    """确保 .rdc 已转换为 .xml，返回 .xml 路径。"""
    if input_path.suffix.lower() == ".xml":
        return input_path
    if input_path.suffix.lower() != ".rdc":
        raise ValueError(f"输入文件必须是 .rdc 或 .xml：{input_path}")
    xml = out_dir / f"{input_path.stem}.xml"
    if force_convert or not xml.exists() or not has_zip_blob(xml):
        cmd = find_renderdoccmd(renderdoccmd)
        run_cmd = [cmd, "convert", "-f", path_for_external_tool(input_path),
                   "-o", path_for_external_tool(xml), "-c", "zip.xml"]
        print(f"运行: {' '.join(run_cmd)}", flush=True)
        subprocess.run(run_cmd, cwd=str(out_dir), check=True)
    else:
        print(f"复用已有 zip.xml：{xml}", flush=True)
    return xml


def extract_platform_info(xml_path: Path) -> str:
    """从 XML 头部提取图形 API 和 GPU 信息。"""
    try:
        with xml_path.open("r", encoding="utf-8", errors="ignore") as f:
            head = f.read(300000)
    except Exception:
        return ""
    api = ""
    if "vkCmdDraw" in head or "vkCreate" in head or "Vulkan" in head:
        api = "Vulkan"
    elif "ID3D12" in head:
        api = "D3D12"
    else:
        api = "D3D11"
    gpu = ""
    gpu_patterns = [
        r'GeForce\s+(?:GTX|RTX|GT)?\s*\d+\s*(?:Ti\s*|SUPER\s*)?',
        r'Radeon\s+[A-Z]+\s*\d+[A-Z]*\s*[A-Z]*',
        r'Adreno\s*\(TM\)*\s*\d+',
        r'Mali[-\s]?[A-Z]*\d+',
        r'Intel\s*\(R\)*\s*[A-Z]+\s*[A-Z]*\s*\d*',
        r'Apple\s+M\d+',
    ]
    for pattern in gpu_patterns:
        m = re.search(pattern, head, re.IGNORECASE)
        if m:
            gpu = m.group(0).replace("(TM)", "").replace("(R)", "").strip()
            gpu = re.sub(r"\s+", " ", gpu)
            break
    return f"{api} / {gpu}" if api and gpu else api
