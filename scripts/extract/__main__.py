# -*- coding: utf-8 -*-
"""Extract stage: parsed markdown → wiki pages + entities/relations."""
from scripts.extract.wiki_extractor import extract_all


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Per-docx LLM wiki extraction")
    parser.add_argument("-f", "--force", action="store_true",
                        help="忽略缓存，全部重新抽取")
    parser.add_argument("--only", type=str, default=None,
                        help="只处理指定文件名（如 '装备异化.docx'）")
    args = parser.parse_args()
    extract_all(force=args.force, only=args.only)
