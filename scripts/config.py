from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    load_dotenv = None


if load_dotenv is not None:
    load_dotenv()


def _resolve_dir(value: str | None, default: Path) -> Path:
    if not value:
        return default
    return Path(value).expanduser().resolve()


@dataclass(frozen=True)
class ProjectPaths:
    project_root: Path
    data_root: Path
    gamedocs_dir: Path
    gamedata_dir: Path
    wiki_dir: Path
    wiki_meta_dir: Path
    wiki_tables_dir: Path
    wiki_tables_registry_dir: Path
    wiki_specs_dir: Path
    graph_json_path: Path
    graph_html_path: Path
    index_md_path: Path
    schemas_json_path: Path
    groups_json_path: Path
    fk_registry_json_path: Path


def build_paths() -> ProjectPaths:
    project_root = Path(__file__).resolve().parent.parent
    data_root = _resolve_dir(
        os.getenv("KB_DATA_DIR"),
        project_root / "knowledge",
    )
    wiki_specs_dir = _resolve_dir(
        os.getenv("KB_WIKI_SPECS_DIR"),
        data_root / "processed" / "wiki_specs",
    )
    wiki_dir = data_root / "wiki"
    wiki_tables_registry_dir = wiki_dir / "_tables"
    return ProjectPaths(
        project_root=project_root,
        data_root=data_root,
        gamedocs_dir=data_root / "gamedocs",
        gamedata_dir=data_root / "gamedata",
        wiki_dir=wiki_dir,
        wiki_meta_dir=wiki_dir / "_meta",
        wiki_tables_dir=wiki_dir / "tables",
        wiki_tables_registry_dir=wiki_tables_registry_dir,
        wiki_specs_dir=wiki_specs_dir,
        graph_json_path=wiki_dir / "graph.json",
        graph_html_path=wiki_dir / "graph.html",
        index_md_path=wiki_dir / "index.md",
        schemas_json_path=wiki_tables_registry_dir / "schemas.json",
        groups_json_path=wiki_tables_registry_dir / "groups.json",
        fk_registry_json_path=wiki_tables_registry_dir / "table_fk_registry.json",
    )


PATHS = build_paths()
