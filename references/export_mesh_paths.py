"""
Mesh 路径导出工具（含面数解析）
搜索指定目录下的所有 .mesh 文件，解析面数并导出到 CSV
支持 Unity YAML 格式的 .mesh 文件
"""

import os
import csv
import argparse
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_mesh_face_count_yaml(file_path):
    """解析 Unity YAML 格式 .mesh 文件，返回总面数"""
    total_faces = 0
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                m = re.search(r'indexCount:\s*(\d+)', line)
                if m:
                    total_faces += int(m.group(1)) // 3
    except (UnicodeDecodeError, IOError):
        return -1  # 无法读取（可能是二进制格式）
    return total_faces

def export_mesh_paths(search_directory, output_path, workers=16):
    """导出 mesh 文件路径和面数到 CSV"""
    if not os.path.exists(search_directory):
        print(f"错误: 目录不存在 - {search_directory}")
        return

    print(f"正在搜索目录: {search_directory}")

    mesh_files = []
    for root, dirs, files in os.walk(search_directory):
        for file in files:
            if file.endswith('.mesh'):
                mesh_files.append(os.path.join(root, file))

    print(f"找到 {len(mesh_files)} 个 .mesh 文件")
    print(f"开始解析面数（{workers} 线程）...")

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 多线程解析面数
    results = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(get_mesh_face_count_yaml, fp): fp for fp in mesh_files}
        for future in as_completed(futures):
            fp = futures[future]
            results[fp] = future.result()
            done += 1
            if done % 1000 == 0 or done == len(mesh_files):
                print(f"  进度: {done}/{len(mesh_files)}")

    # 写入 CSV
    success = 0
    binary = 0
    zero = 0
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Path', 'FaceCount'])

        for file_path in mesh_files:
            normalized = file_path.replace('\\', '/')
            assets_idx = normalized.find('/Assets/')
            if assets_idx != -1:
                normalized = normalized[assets_idx + 1:]

            face_count = results[file_path]
            if face_count == -1:
                binary += 1
            elif face_count == 0:
                zero += 1
            else:
                success += 1

            writer.writerow([normalized, face_count])

    print(f"\n导出完成: {output_path}")
    print(f"总计: {len(mesh_files)} 个文件")
    print(f"  有面数: {success}")
    print(f"  面数为0: {zero}")
    print(f"  二进制(未解析): {binary}")

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_output = os.path.join(script_dir, 'mesh_export.csv')

    parser = argparse.ArgumentParser(description='Mesh 路径+面数导出工具')
    parser.add_argument('--dir', type=str, default=r'E:\swordm3d\Assets\_Resources',
                        help='搜索目录 (默认: E:\\swordm3d\\Assets\\_Resources)')
    parser.add_argument('--output', type=str, default=default_output,
                        help='输出 CSV 路径 (默认: mesh_export.csv)')
    parser.add_argument('--workers', type=int, default=16,
                        help='线程数 (默认: 16)')
    args = parser.parse_args()

    export_mesh_paths(args.dir, args.output, args.workers)

if __name__ == "__main__":
    main()
