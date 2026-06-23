# -*- coding: utf-8 -*-
"""批量分析目录下所有 .rdc 文件。"""

from __future__ import annotations

from pathlib import Path
from typing import Callable


def run_batch(
    input_dir: Path,
    out_root: Path,
    pipeline: Callable[[Path, Path], list],
) -> None:
    """批量处理 input_dir 下所有 .rdc 文件。

    Args:
        input_dir: 包含 .rdc 文件的目录
        out_root: 输出根目录，每个 .rdc 会在其下创建子目录
        pipeline: 处理函数，接收 (rdc_path, out_dir) 返回 records
    """
    rdcs = sorted(input_dir.glob("*.rdc"))
    if not rdcs:
        print(f"未找到 .rdc 文件: {input_dir}")
        return

    print(f"找到 {len(rdcs)} 个 .rdc 文件")
    out_root.mkdir(parents=True, exist_ok=True)

    succeeded = 0
    failed = 0

    for i, rdc in enumerate(rdcs, 1):
        case_name = rdc.stem
        case_out = out_root / case_name
        print(f"\n[{i}/{len(rdcs)}] 处理: {rdc.name}")
        print(f"  输出: {case_out}")

        try:
            pipeline(rdc, case_out)
            succeeded += 1
            print(f"  完成: {case_name}")
        except Exception as e:
            failed += 1
            print(f"  失败: {case_name} - {e}")

    print(f"\n批量处理完成: 成功 {succeeded}, 失败 {failed}, 共 {len(rdcs)}")
    print(f"输出目录: {out_root}")
