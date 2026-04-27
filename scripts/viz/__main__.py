# -*- coding: utf-8 -*-
"""Viz stage: graph.json → interactive graph.html."""
from scripts.graph.graph_viz import run


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Interactive wiki graph visualization")
    parser.add_argument("--open", action="store_true", help="生成后在浏览器打开")
    args = parser.parse_args()
    run(open_browser=args.open)
