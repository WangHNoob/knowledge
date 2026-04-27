# -*- coding: utf-8 -*-
"""
批量转换策划文档为 md 格式缓存

仅转换 knowledge/gamedocs/ 下的 docx 文件（gamedata/ 的 xlsx 表格不参与转换，
只在 wiki_extractor 中以表名列表的形式作为 LLM 上下文使用）。

支持懒加载：已有缓存且源文件未更新时跳过。
"""
import os

from .config import PATHS
from .doc_reader import scan_dir, read_doc


def batch_convert(input_dir=None, force=False):
    """批量转换策划文档目录下的 docx 文件。

    Args:
        input_dir: 策划文档目录 (默认来自 KB_DATA_DIR/gamedocs)
        force: 是否强制重新转换（忽略缓存）
    """
    input_dir = input_dir or str(PATHS.gamedocs_dir)
    print(f"扫描目录: {input_dir}")

    files = scan_dir(input_dir, extensions=['.docx'])
    print(f"找到 {len(files)} 个 docx 文件\n")

    success = 0
    skipped = 0
    failed = 0

    for filepath in files:
        if not force:
            cache_path = os.path.join(
                os.path.dirname(filepath), '.cache',
                os.path.basename(filepath) + '.md'
            )
            if os.path.exists(cache_path) and \
               os.path.getmtime(cache_path) >= os.path.getmtime(filepath):
                print(f"跳过(已有缓存): {filepath}")
                skipped += 1
                continue

        try:
            print(f"转换: {filepath}")
            chunks = read_doc(filepath, force=force)
            if chunks:
                print(f"  -> 成功 ({len(chunks)} chunks)")
                success += 1
            else:
                print(f"  -> 空内容")
                failed += 1
        except Exception as e:
            print(f"  -> 错误: {e}")
            failed += 1

    print(f"\n完成: 成功 {success}, 跳过 {skipped}, 失败 {failed}")
    return success, skipped, failed


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="批量转换策划文档为 md 格式")
    parser.add_argument("input_dir", nargs="?", default=None,
                        help="策划文档目录 (默认: KB_DATA_DIR/gamedocs)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="强制重新转换，忽略已有缓存")
    args = parser.parse_args()

    batch_convert(args.input_dir, force=args.force)
