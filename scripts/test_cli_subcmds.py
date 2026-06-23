# -*- coding: utf-8 -*-
"""CLI 子命令冒烟测试。"""

import sys
from pathlib import Path

# 添加父目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def test_import():
    from rdc_parser.scripts.cli import main
    assert callable(main)


def test_html_import():
    from rdc_parser.scripts.html_report import write_html_report
    assert callable(write_html_report)


def test_compare_import():
    from rdc_parser.scripts.compare import compare_records
    assert callable(compare_records)


def test_batch_import():
    from rdc_parser.scripts.batch import run_batch
    assert callable(run_batch)


def test_gpu_timing_import():
    from rdc_parser.scripts.gpu_timing import export_gpu_timing
    assert callable(export_gpu_timing)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS: {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {t.__name__} - {e}")
            failed += 1
    print(f"\n结果: {passed} 通过, {failed} 失败")
    sys.exit(1 if failed else 0)
