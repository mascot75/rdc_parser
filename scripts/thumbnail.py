# -*- coding: utf-8 -*-
"""缩略图导出：从 .rdc 文件提取抓帧缩略图。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from .converter import find_renderdoccmd, path_for_external_tool


def resolve_rdc_for_thumbnail(input_path: Path) -> Path | None:
    """解析 .rdc 路径：直接 .rdc 输入，或 .xml 同目录同名 .rdc。"""
    if input_path.suffix.lower() == ".rdc":
        return input_path if input_path.is_file() else None
    if input_path.suffix.lower() == ".xml":
        sibling = input_path.with_suffix(".rdc")
        return sibling if sibling.is_file() else None
    return None


def _ascii_safe_thumb_temp_png() -> Path:
    """生成 ASCII 安全的临时 PNG 路径。

    renderdoccmd 不支持非 ASCII 路径（如中文目录），所以缩略图必须先
    导出到纯 ASCII 临时目录，成功后再拷回目标位置。
    """
    name = f"rdc_thumb_{uuid.uuid4().hex}.png"
    roots: list[str] = []
    for key in ("TEMP", "TMP"):
        v = os.environ.get(key)
        if v:
            roots.append(v)
    try:
        roots.append(tempfile.gettempdir())
    except FileNotFoundError:
        pass
    roots.append(r"C:\Windows\Temp")
    roots.append(r"C:\tmp")
    seen: set[str] = set()
    for r in roots:
        if not r or r in seen:
            continue
        seen.add(r)
        p = Path(os.path.abspath(os.path.join(r, name)))
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            str(p).encode("ascii")
        except (OSError, UnicodeEncodeError):
            continue
        return p
    fallback = Path(r"C:\tmp") / name
    try:
        fallback.parent.mkdir(parents=True, exist_ok=True)
        str(fallback).encode("ascii")
        return fallback
    except (OSError, UnicodeEncodeError):
        return Path(os.path.abspath(name))


def export_rdc_capture_thumbnail(
    rdc_path: Path, out_png: Path, renderdoccmd: str | None,
) -> bool:
    """从 .rdc 导出抓帧内置缩略图。

    RenderDoc 不同版本的 thumb 命令行参数略有差异，本函数尝试多种写法。
    非 ASCII 路径会先写入 ASCII 安全临时目录再拷回。
    """
    try:
        cmd_exe = find_renderdoccmd(renderdoccmd)
    except FileNotFoundError:
        return False

    out_png.parent.mkdir(parents=True, exist_ok=True)
    rdc_abs = path_for_external_tool(rdc_path)
    tmp_out = _ascii_safe_thumb_temp_png()
    tmp_abs = str(tmp_out)

    cmd_variants: list[list[str]] = [
        [cmd_exe, "thumb", "-o", tmp_abs, "-f", "png", rdc_abs],
        [cmd_exe, "thumb", "--out", tmp_abs, "--format", "png", rdc_abs],
        [cmd_exe, "thumb", f"--out={tmp_abs}", "--format=png", rdc_abs],
        [cmd_exe, "thumb", "-o", tmp_abs, rdc_abs],
    ]

    run_kw: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(rdc_path.parent),
    }
    if sys.platform == "win32":
        run_kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    for cmd in cmd_variants:
        try:
            if tmp_out.is_file():
                tmp_out.unlink()
        except OSError:
            pass
        try:
            if out_png.is_file():
                out_png.unlink()
        except OSError:
            pass
        try:
            subprocess.run(cmd, check=False, **run_kw)
        except OSError:
            continue
        if tmp_out.is_file() and tmp_out.stat().st_size > 0:
            ok = False
            try:
                shutil.copy2(tmp_out, out_png)
                ok = out_png.is_file() and out_png.stat().st_size > 0
            except OSError:
                ok = False
            finally:
                try:
                    tmp_out.unlink()
                except OSError:
                    pass
            if ok:
                return True
    return False
