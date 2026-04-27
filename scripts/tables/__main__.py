# -*- coding: utf-8 -*-
"""Tables stage: xlsx → table schemas + FK registry + wiki/tables/*.md."""
from scripts.extract.table_analyzer import run


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Deterministic xlsx table analysis")
    parser.add_argument("-f", "--force", action="store_true",
                        help="忽略缓存，重新读取所有 xlsx 头部")
    args = parser.parse_args()
    run(force=args.force)
