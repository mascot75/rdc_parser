"""
Prefab 路径导出工具
搜索指定目录下的所有 .prefab 文件，导出路径到 CSV
"""

import os
import csv
import argparse

def export_prefab_paths(search_directory, output_path):
    """导出 prefab 文件路径到 CSV"""
    if not os.path.exists(search_directory):
        print(f"错误: 目录不存在 - {search_directory}")
        return

    print(f"正在搜索目录: {search_directory}")

    prefab_files = []
    for root, dirs, files in os.walk(search_directory):
        for file in files:
            if file.endswith('.prefab'):
                prefab_files.append(os.path.join(root, file))

    print(f"找到 {len(prefab_files)} 个 .prefab 文件")

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Path'])

        for i, file_path in enumerate(prefab_files, 1):
            normalized = file_path.replace('\\', '/')
            assets_idx = normalized.find('/Assets/')
            if assets_idx != -1:
                normalized = normalized[assets_idx + 1:]

            writer.writerow([normalized])

            if i % 500 == 0 or i == len(prefab_files):
                print(f"  进度: {i}/{len(prefab_files)}")

    print(f"\n导出完成: {output_path}")
    print(f"总计: {len(prefab_files)} 个文件")

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_output = os.path.join(script_dir, 'prefab_export.csv')

    parser = argparse.ArgumentParser(description='Prefab 路径导出工具')
    parser.add_argument('--dir', type=str, default=r'E:\swordm3d\Assets\_Resources',
                        help='搜索目录 (默认: E:\\swordm3d\\Assets\\_Resources)')
    parser.add_argument('--output', type=str, default=default_output,
                        help='输出 CSV 路径 (默认: prefab_export.csv)')
    args = parser.parse_args()

    export_prefab_paths(args.dir, args.output)

if __name__ == "__main__":
    main()
