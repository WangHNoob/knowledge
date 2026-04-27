# -*- coding: utf-8 -*-
"""Graph stage: wiki/_meta/*.json + schemas.json → graph.json + index.md."""
from scripts.graph.graph_builder import run as build_run
from scripts.graph.graph_viz import run as viz_run


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Graph generation + visualization")
    parser.add_argument(
        "--stage", choices=["graph", "viz"], default="graph",
        help="graph: build graph.json+index.md; viz: build graph.html",
    )
    parser.add_argument("--open", action="store_true", help="生成后在浏览器打开")
    args = parser.parse_args()
    if args.stage == "graph":
        build_run()
    else:
        viz_run(open_browser=args.open)
