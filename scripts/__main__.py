# -*- coding: utf-8 -*-
"""
kb-builder 知识库构建流程入口

用法：
    python -m scripts                  # 全流程
    python -m scripts --stage convert  # 单阶段
"""
from scripts.build_wiki import run as build_run, STAGES


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="kb-builder 知识库构建流程")
    parser.add_argument("--stage", choices=STAGES, default=None,
                        help="只执行单个阶段；默认全部")
    parser.add_argument("-f", "--force", action="store_true",
                        help="忽略缓存，强制重跑")
    parser.add_argument("--only", type=str, default=None,
                        help="extract 阶段只处理指定文件名")
    args = parser.parse_args()
    build_run(stage=args.stage, force=args.force, only=args.only)
