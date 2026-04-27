# -*- coding: utf-8 -*-
"""
一键运行 kb-builder 完整 pipeline。

依赖 uv（需预先安装）。自动完成：
  1. 用 uv sync --extra llm 安装/更新依赖
  2. 用 uv run 执行 pipeline（自动使用正确的 Python + 环境）

用法：
  uv run python run_pipeline.py                  # 完整流程
  uv run python run_pipeline.py --stage extract # 只跑单个阶段
  uv run python run_pipeline.py --force         # 忽略缓存，强制重跑
  uv run python run_pipeline.py --data-dir ./my_data  # 指定数据目录
  uv run python run_pipeline.py --model gpt-4o  # 指定 LLM 模型
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def _find_uv() -> str:
    """从 PATH 中找到 uv 可执行文件路径。"""
    result = subprocess.run(
        ["uv", "--version"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError("uv not found in PATH. 请先安装 uv: https://github.com/astral-sh/uv")
    # 返回 "uv" 让 shell/PATH 解析
    return "uv"


def _project_root() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="kb-builder: 游戏策划文档 -> 结构化 Wiki + 知识图谱"
    )
    parser.add_argument(
        "--stage",
        choices=["convert", "extract", "tables", "graph", "viz"],
        default=None,
        help="只运行指定阶段；默认运行全部",
    )
    parser.add_argument(
        "-f", "--force", action="store_true",
        help="忽略缓存，强制重新转换/抽取",
    )
    parser.add_argument(
        "--only", type=str, default=None,
        help="extract 阶段只处理指定文件名（如 装备异化.docx）",
    )
    parser.add_argument(
        "--data-dir", type=str, default=None,
        help="数据根目录（默认: ./knowledge）",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="LLM 模型（默认: claude-3-5-haiku-latest）",
    )
    args = parser.parse_args()

    root = _project_root()
    uv = _find_uv()

    # 设置环境变量（传递给 uv run）
    env = os.environ.copy()
    if args.data_dir:
        env["KB_DATA_DIR"] = os.path.abspath(args.data_dir)
    if args.model:
        env["LLM_MODEL_FAST"] = args.model

    # 阶段1: uv sync 安装依赖
    print("== [1/2] 安装依赖 ==")
    sync_result = subprocess.run(
        [uv, "sync", "--extra", "llm"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )
    if sync_result.returncode != 0:
        print(sync_result.stderr, file=sys.stderr)
        sys.exit(sync_result.returncode)
    print("依赖就绪\n")

    # 阶段2: uv run 执行 pipeline
    kb_args = ["--stage", args.stage] if args.stage else []
    if args.force:
        kb_args.append("--force")
    if args.only:
        kb_args += ["--only", args.only]

    print(f"== [2/2] 运行 pipeline: python -m scripts {' '.join(kb_args) or '(all stages)'} ==")
    run_result = subprocess.run(
        [uv, "run", "python", "-m", "scripts"] + kb_args,
        cwd=root,
        env=env,
    )
    sys.exit(run_result.returncode)


if __name__ == "__main__":
    main()
