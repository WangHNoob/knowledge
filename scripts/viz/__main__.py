# -*- coding: utf-8 -*-
"""Viz 阶段：graph.json → 交互式 graph.html"""
from scripts.graph.graph_viz import run


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="交互式知识图谱可视化")
    parser.add_argument("--open", action="store_true", help="生成后在浏览器打开")
    args = parser.parse_args()
    run(open_browser=args.open)
