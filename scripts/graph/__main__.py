# -*- coding: utf-8 -*-
"""Graph 阶段：wiki/_meta/*.json + schemas.json → graph.json + index.md"""
from scripts.graph.graph_builder import run as build_run
from scripts.graph.graph_viz import run as viz_run


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="图谱构建与可视化")
    parser.add_argument(
        "--stage", choices=["graph", "viz"], default="graph",
        help="graph: 构建 graph.json+index.md; viz: 构建 graph.html",
    )
    parser.add_argument("--open", action="store_true", help="生成后在浏览器打开")
    args = parser.parse_args()
    if args.stage == "graph":
        build_run()
    else:
        viz_run(open_browser=args.open)
