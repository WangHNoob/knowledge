from __future__ import annotations

import argparse

from . import build_wiki


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the game-design wiki and knowledge graph"
    )
    parser.add_argument(
        "--stage",
        choices=build_wiki.STAGES,
        default=None,
        help="Only run a single stage",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="Ignore caches and rebuild the selected stage(s)",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Only process one doc during extract stage (for example 装备异化.docx)",
    )
    args = parser.parse_args()
    build_wiki.run(stage=args.stage, force=args.force, only=args.only)


if __name__ == "__main__":
    main()
