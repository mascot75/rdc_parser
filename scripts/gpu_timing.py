# -*- coding: utf-8 -*-
"""GPU 耗时导出与合并。"""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .models import DrawRec


def find_qrenderdoc(explicit: str | None = None) -> str:
    """查找 qrenderdoc.exe。"""
    if explicit:
        path = Path(explicit)
        if path.name.lower() != "qrenderdoc.exe":
            path = path.with_name("qrenderdoc.exe")
        if path.is_file():
            return str(path.resolve())
        raise FileNotFoundError(f"找不到与指定工具配套的 qrenderdoc.exe: {path}")
    for cand in (
        r"C:\Program Files\RenderDoc\qrenderdoc.exe",
        r"C:\Program Files (x86)\RenderDoc\qrenderdoc.exe",
    ):
        if Path(cand).is_file():
            return cand
    import shutil
    found = shutil.which("qrenderdoc")
    if found:
        return found
    raise FileNotFoundError("找不到 qrenderdoc.exe。请安装 RenderDoc 或用 --renderdoccmd 指定路径。")


def path_for_tool(path: Path) -> str:
    """返回适合外部工具使用的绝对路径。"""
    return os.path.abspath(os.fspath(path))


def _wait_qrenderdoc(proc: subprocess.Popen, out_json: Path, timeout_s: int) -> int:
    """等待 qrenderdoc 进程完成，监控 JSON 输出文件。"""
    deadline = time.monotonic() + timeout_s if timeout_s > 0 else None
    last_size = -1
    stable_ticks = 0
    poll_s = 0.2
    min_json_bytes = 64
    stable_needed = 3

    while proc.poll() is None:
        if deadline is not None and time.monotonic() > deadline:
            proc.kill()
            proc.wait(timeout=10)
            raise TimeoutError(f"qrenderdoc 超时（{timeout_s}s）")
        if out_json.is_file():
            size = out_json.stat().st_size
            if size >= min_json_bytes and size == last_size:
                stable_ticks += 1
                if stable_ticks >= stable_needed:
                    print(f"JSON 已就绪 ({size} bytes)，结束 qrenderdoc 进程…", flush=True)
                    proc.terminate()
                    try:
                        proc.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=10)
                    return int(proc.returncode or 0)
            else:
                stable_ticks = 0
                last_size = size
        time.sleep(poll_s)

    return int(proc.returncode or 0)


def export_gpu_timing(
    rdc_path: Path,
    out_json: Path,
    renderdoccmd: str | None = None,
    runs: int = 3,
    timeout_s: int = 600,
    include_timing: bool = True,
) -> bool:
    """从 .rdc 导出 GPU 耗时数据。

    使用 qrenderdoc --ui-python 运行 UI 脚本导出 per-event 耗时。
    返回 True 表示成功。
    """
    try:
        qrenderdoc = find_qrenderdoc(renderdoccmd)
    except FileNotFoundError as e:
        print(f"错误: {e}", file=sys.stderr)
        return False

    # UI 脚本路径
    ui_script = Path(__file__).parent / "replay_export_ui.py"
    if not ui_script.is_file():
        print(f"错误: 缺少 UI 脚本: {ui_script}", file=sys.stderr)
        return False

    out_json.parent.mkdir(parents=True, exist_ok=True)

    # 清理旧文件
    if out_json.is_file():
        out_json.unlink()
    err_log = out_json.with_suffix(".err.txt")
    if err_log.is_file():
        err_log.unlink()

    # 设置环境变量
    env = os.environ.copy()
    env["RDC_TIMING_OUT"] = path_for_tool(out_json)
    env["RDC_TIMING_RDC"] = path_for_tool(rdc_path)
    env["RDC_TIMING_RUNS"] = str(max(int(runs), 1))
    env["RDC_TIMING_ERR"] = path_for_tool(err_log)
    env["RDC_INCLUDE_TIMING"] = "1" if include_timing else "0"
    env.setdefault("RDC_TIMING_AUTO_QUIT", "1")

    # 构建命令
    cmd = [
        qrenderdoc,
        "--ui-python",
        path_for_tool(ui_script),
    ]

    print(f"运行(qrenderdoc): {' '.join(f'\"{c}\"' if ' ' in c else c for c in cmd)}", flush=True)
    startup = None
    if sys.platform == "win32":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
    proc = subprocess.Popen(cmd, env=env, startupinfo=startup)

    try:
        returncode = _wait_qrenderdoc(proc, out_json, timeout_s)
    except TimeoutError as e:
        print(f"错误: {e}", file=sys.stderr)
        return False

    if returncode != 0 and (not out_json.is_file() or out_json.stat().st_size <= 0):
        print(f"错误: qrenderdoc 返回码 {returncode}", file=sys.stderr)
        if err_log.is_file():
            err_hint = err_log.read_text(encoding="utf-8", errors="replace").strip()
            if err_hint:
                print(f"UI 脚本错误:\n{err_hint}", file=sys.stderr)
        return False

    if not out_json.is_file() or out_json.stat().st_size <= 0:
        err_hint = ""
        if err_log.is_file():
            err_hint = err_log.read_text(encoding="utf-8", errors="replace").strip()
        print(
            f"错误: 未生成有效 JSON：{out_json}。可能回放失败、GPU 不支持 EventGPUDuration，或抓帧无法在本地 replay。"
            + (f"\nUI 脚本错误:\n{err_hint}" if err_hint else ""),
            file=sys.stderr,
        )
        return False

    try:
        data = json.loads(out_json.read_text(encoding="utf-8"))
        valid = bool(data.get("drawcalls")) and data.get("status") == "ok"
        if include_timing:
            valid = valid and bool(load_timing_json(out_json)) and data.get("timing_status") == "ok"
        if not valid:
            print("回放未完整成功: " + "\n".join(data.get("diagnostics", ["没有有效数据"])), file=sys.stderr)
        return valid
    except (ValueError, OSError) as exc:
        print(f"回放 JSON 无效: {exc}", file=sys.stderr)
        return False


class TimingData(dict):
    def __init__(self, *args, key_space="xml_chunk", **kwargs):
        super().__init__(*args, **kwargs)
        self.key_space = key_space


def load_replay_result(path: Path, capture: Path) -> dict:
    """Reuse only a complete export belonging to the same unchanged capture."""
    data = json.loads(path.read_text(encoding="utf-8"))
    source = data.get("source", {})
    stat = capture.stat()
    if (data.get("schema_version") != 2 or not data.get("drawcalls")
            or data.get("status") not in {"ok", "partial"}
            or Path(source.get("path", "")).resolve() != capture.resolve()
            or source.get("size") != stat.st_size or source.get("mtime_ns") != stat.st_mtime_ns):
        raise ValueError("回放数据无效、对应不同抓帧或源文件已改变，拒绝复用")
    return data


def validate_timing_alignment(records, timing):
    key_space = getattr(timing, "key_space", "xml_chunk")
    if timing and any(r.event_space != key_space for r in records):
        raise ValueError("耗时与绘制记录的事件编号类型不同，不能合并 replay event ID 和 XML chunk index")


def load_timing_json(json_path: Path) -> dict[int, float]:
    """加载耗时 JSON，返回 {event_id: duration_ms}。"""
    if not json_path.exists():
        return {}

    data = json.loads(json_path.read_text(encoding="utf-8"))
    key_space = data.get("key_space", "xml_chunk") if isinstance(data, dict) else "xml_chunk"

    # 新格式：{"timing": {...}, "shader_names": {...}}
    if isinstance(data, dict) and "timing" in data:
        data = data["timing"]

    timing: dict[int, float] = {}

    # 支持多种 JSON 格式
    if isinstance(data, dict):
        # 格式1: {"events": [{"id": 1, "duration": 0.5}, ...]}
        if "events" in data:
            for item in data["events"]:
                eid = int(item.get("id", item.get("event", 0)))
                dur = float(item.get("duration", item.get("time_ms", 0)))
                if eid > 0:
                    timing[eid] = dur
        # 格式2: {"1": 0.5, "2": 0.3, ...}
        else:
            for k, v in data.items():
                try:
                    eid = int(k)
                    dur = float(v)
                    if eid > 0:
                        timing[eid] = dur
                except (ValueError, TypeError):
                    continue
    elif isinstance(data, list):
        # 格式3: [{"event": 1, "time_ms": 0.5}, ...]
        for item in data:
            eid = int(item.get("event", item.get("id", 0)))
            dur = float(item.get("time_ms", item.get("duration", 0)))
            if eid > 0:
                timing[eid] = dur

    return TimingData({k: v for k, v in timing.items() if math.isfinite(v) and v >= 0}, key_space=key_space)


def load_shader_names_from_timing(json_path: Path) -> dict[str, str]:
    """从耗时 JSON 中提取 shader_names（如果有）。"""
    if not json_path.exists():
        return {}

    data = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "shader_names" in data:
        result = {}
        for k, v in data["shader_names"].items():
            if not v:
                continue
            # 去掉 "ResourceId::" 前缀
            key = str(k).split("::")[-1] if "::" in str(k) else str(k)
            result[key] = str(v)
        return result
    return {}


def merge_timing(
    records: list[DrawRec],
    timing: dict[int, float],
) -> list[dict[str, Any]]:
    """将耗时数据合并到 records 中。"""
    validate_timing_alignment(records, timing)
    if not records or not any(r.event in timing for r in records):
        raise ValueError("耗时与绘制记录没有匹配的事件")
    rows = []
    for r in records:
        gpu_time = timing.get(r.event)
        rows.append({
            "event": r.event,
            "pass_name": r.pass_name,
            "ps": r.ps,
            "resource_category": r.resource_category,
            "face_estimate": r.face_estimate,
            "gpu_time_ms": round(gpu_time, 6) if gpu_time is not None else "",
            "timing_available": gpu_time is not None,
        })
    return rows


def write_timing_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """写入耗时 CSV。"""
    if not rows:
        raise ValueError("没有耗时记录，拒绝写入空 CSV")

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
