"""扫描 Unity 项目中的 Shader 文件并导出 CSV"""

import csv
import json
import os
import re
import sys
from pathlib import Path


def build_package_map(packages_dir: Path) -> dict[str, str]:
    """读取 Packages/*/package.json，构建 文件夹名 -> 包ID 的映射"""
    pkg_map = {}
    if not packages_dir.is_dir():
        return pkg_map
    for entry in packages_dir.iterdir():
        if not entry.is_dir():
            continue
        pkg_json = entry / "package.json"
        if pkg_json.is_file():
            try:
                with open(pkg_json, encoding="utf-8") as f:
                    data = json.load(f)
                if "name" in data:
                    pkg_map[entry.name] = data["name"]
            except (json.JSONDecodeError, OSError):
                pass
    return pkg_map


def extract_shader_name(filepath: str) -> str | None:
    """从 .shader 文件前 50 行中提取 Shader 名称"""
    try:
        with open(filepath, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                if i >= 50:
                    break
                m = re.match(r'Shader\s+[^"]*"([^"]+)"', line.lstrip("﻿").lstrip())
                if m:
                    return m.group(1)
        return None
    except OSError:
        return None


def normalize_path(rel: str, pkg_map: dict[str, str]) -> str:
    """去掉路径中的包版本号，并将文件夹名替换为包 ID"""
    # 去掉版本号: com.unity.cinemachine@2.9.5 -> com.unity.cinemachine
    rel = re.sub(r'/([^/]+)@[^/]+', r'/\1', rel)
    # 替换 Packages/ 下的文件夹名为包 ID
    if rel.startswith("Packages/"):
        parts = rel.split("/", 2)
        if len(parts) >= 2 and parts[1] in pkg_map:
            parts[1] = pkg_map[parts[1]]
            rel = "/".join(parts)
    return rel


def scan_shaders(project_root: str) -> list[dict]:
    """扫描 Assets 和 Packages 目录下的所有 shader 文件"""
    root = Path(project_root)
    search_dirs = [root / "Assets", root / "Packages"]
    pkg_map = build_package_map(root / "Packages")
    results = {}  # name -> path (去重)

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        for ext in ("*.shader", "*.shadergraph"):
            for fpath in search_dir.rglob(ext):
                rel = fpath.relative_to(root).as_posix()
                rel = normalize_path(rel, pkg_map)

                if ext == "*.shader":
                    name = extract_shader_name(str(fpath))
                    if not name:
                        name = fpath.stem
                else:
                    name = fpath.stem

                if name not in results:
                    results[name] = rel
                else:
                    # 同名时优先保留非 ~ 路径
                    existing = results[name]
                    existing_in_tilde = "~/" in existing or "~\\" in existing
                    current_in_tilde = "~/" in rel or "~\\" in rel
                    if existing_in_tilde and not current_in_tilde:
                        results[name] = rel

    return [{"name": n, "path": p} for n, p in sorted(results.items())]


def export_csv(shaders: list[dict], output_path: str) -> None:
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Shader Name", "Path"])
        for s in shaders:
            writer.writerow([s["name"], s["path"]])


def main():
    project_root = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
    # 如果传入的是脚本自身路径，取父目录
    if os.path.isfile(project_root):
        project_root = os.path.dirname(project_root)

    output_path = os.path.join(project_root, "ShaderList.csv")

    shaders = scan_shaders(project_root)
    export_csv(shaders, output_path)
    print(f"共 {len(shaders)} 个 Shader，已导出到: {output_path}")


if __name__ == "__main__":
    main()
