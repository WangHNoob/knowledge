# -*- coding: utf-8 -*-
"""Convert stage: docx/xlsx → parsed markdown."""
from scripts.convert.batch_convert import batch_convert


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Convert gamedocs docx to parsed markdown")
    parser.add_argument("input_dir", nargs="?", default=None,
                        help="策划文档目录 (默认: KB_DATA_DIR/gamedocs)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="强制重新转换，忽略已有缓存")
    args = parser.parse_args()
    batch_convert(args.input_dir, force=args.force)
