# -*- coding: utf-8 -*-
"""
Wiki 构建编排器

三个阶段串联：
  convert : docx -> .cache md  (batch_convert)
  extract : md -> 结构化 wiki 页面 + 实体/关系 meta  (wiki_extractor)
  graph   : meta -> graph.json + index.md  (graph_builder, 确定性)

用法：
  python build_wiki.py                  # 增量（使用所有缓存）
  python build_wiki.py --force          # 强制重转 + 重抽取
  python build_wiki.py --stage extract  # 只跑单个阶段 (convert|extract|graph)
"""
from __future__ import annotations

import argparse

from scripts.convert.batch_convert import batch_convert
from scripts.extract.wiki_extractor import extract_all as wiki_extract_all
from scripts.extract.table_analyzer import run as table_run
from scripts.graph.graph_builder import run as graph_build_run
from scripts.graph.graph_viz import run as graph_viz_run
from scripts.config import PATHS


STAGES = ("convert", "extract", "tables", "graph", "viz")


def run(stage: str | None = None, force: bool = False,
        only: str | None = None) -> None:
    stages = STAGES if stage is None else (stage,)
    if stage and stage not in STAGES:
        print(f"未知 stage: {stage}；可选: {STAGES}")
        return

    for s in stages:
        print(f"\n========== stage: {s} ==========")
        if s == "convert":
            batch_convert(
                input_dir=str(PATHS.gamedocs_dir),
                force=force,
            )
        elif s == "extract":
            wiki_extract_all(force=force, only=only)
        elif s == "tables":
            table_run(force=force)
        elif s == "graph":
            graph_build_run()
        elif s == "viz":
            graph_viz_run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the wiki pipeline")
    parser.add_argument("--stage", choices=STAGES, default=None,
                        help="只执行单个阶段；默认串联 convert->extract->graph")
    parser.add_argument("-f", "--force", action="store_true",
                        help="忽略缓存：重新转换 + 重新抽取")
    parser.add_argument("--only", type=str, default=None,
                        help="extract 阶段只处理指定文件名 (如 '装备异化.docx')")
    args = parser.parse_args()
    run(stage=args.stage, force=args.force, only=args.only)
