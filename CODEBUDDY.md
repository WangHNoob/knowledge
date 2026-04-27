# CODEBUDDY.md This file provides guidance to WorkBuddy when working with code in this repository.

## Project Overview

kb-builder is a **game design knowledge base builder** that converts 策划 docx documents into a structured, LLM-generated wiki with a deterministic knowledge graph. Design reference: [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md).

## Common Commands

```bash
# Full pipeline (convert -> extract -> tables -> graph), incremental
python build_wiki.py

# Force reconvert + re-extract + rescan xlsx
python build_wiki.py --force

# Run a single stage
python build_wiki.py --stage convert     # docx -> .cache md
python build_wiki.py --stage extract     # md -> wiki page + entities/relationships (LLM)
python build_wiki.py --stage tables      # xlsx -> table schemas + FK + wiki/tables/*.md (deterministic)
python build_wiki.py --stage graph       # meta + schemas -> graph.json + index.md (deterministic)
python build_wiki.py --stage viz         # graph.json + FK registry -> graph.html (interactive vis.js)

# Extract one doc only (useful for iterating on prompts)
python build_wiki.py --stage extract --only 装备异化.docx

# Individual scripts (direct invocation)
python batch_convert.py
python wiki_extractor.py [--force] [--only FILE.docx]
python table_analyzer.py [--force]
python graph_builder.py
python graph_viz.py [--open]
```

No build/test/lint tooling is wired — scripts run directly.

## Pipeline

```
knowledge/gamedocs/*.docx
       │   batch_convert.py  (doc_reader + WPS repair)
       ▼
knowledge/gamedocs/.cache/*.md           ← raw markdown per doc
       │   wiki_extractor.py  (1 LLM call per doc)
       │   inputs: raw md + 5 spec templates + xlsx filename list
       │   outputs: wiki page + entities/relationships
       ▼
knowledge/wiki/
  ├── systems/*.md          system_rule pages (LLM-generated)
  ├── tables/*.md           table_schema pages (one per family, deterministic)
  ├── numerical/*.md        numerical_convention pages (LLM-generated)
  ├── activities/*.md       activity_template pages (LLM-generated)
  ├── combat/*.md           combat_framework pages (LLM-generated)
  ├── _meta/<stem>.json     sidecar per docx: entities, relationships, content_hash
  └── _tables/              deterministic xlsx analysis (table_analyzer.py)
      ├── schemas.json           all 7500+ tables with fields/path/mtime/group
      ├── groups.json            family → member-table list
      └── table_fk_registry.json all FK edges detected by field convention
       │   graph_builder.py  (deterministic Python, no LLM)
       ▼
knowledge/wiki/graph.json   ← merged graph: doc nodes + entity nodes + table nodes
knowledge/wiki/index.md     ← stats, type breakdown, top entities, doc→table, table families
```

`knowledge/gamedata/*.xlsx` (7500+ tables) feeds two places:
- Filenames injected into LLM extractor prompt (so LLM references tables by exact name)
- `table_analyzer.py` reads each header row to produce schemas + FK edges + per-family pages

## Key Files

| File | Role |
|------|------|
| `doc_reader.py` | docx/xlsx parsing + caching + chunking; WPS image-ref repair (L33-105) |
| `batch_convert.py` | Orchestrates docx→md conversion under `knowledge/gamedocs/` |
| `wiki_specs/*.md` | 5 spec templates used as prompt fragments (system_rule, table_schema, numerical_convention, activity_template, combat_framework) |
| `wiki_extractor.py` | Per-docx LLM extraction → wiki page + meta sidecar |
| `table_analyzer.py` | Scans all xlsx → schemas.json, groups.json, table_fk_registry.json, wiki/tables/*.md (deterministic, zero LLM) |
| `graph_builder.py` | Merges doc metas + table schemas into graph.json + index.md (deterministic) |
| `graph_viz.py` | Produces self-contained interactive `wiki/graph.html` (vis.js CDN); filters to docs + LLM entities + FK-connected tables |
| `build_wiki.py` | CLI orchestrator |
| `old/` | Archived legacy pipeline (wiki_compiler, eval_wiki, init, etc.) — kept for reference, not imported |

## Implementation Details

- **Entity / relation enums are enforced** (`wiki_extractor._validate`):
  - entity types: `system | table | resource | attribute | activity | concept`
  - relation types: `depends_on | unlocks | configured_in | produces | consumes | belongs_to | references`
  - any entity/relation outside these enums is dropped with a warning logged into the meta file
- **Extraction caching**: `content_hash` of the raw md is stored in `_meta/<stem>.json`; re-runs skip unchanged docs unless `--force`.
- **LLM call**: uses `litellm.completion` with `model=os.getenv("LLM_MODEL_FAST")` (set in `.env`). Falls back to `claude-3-5-haiku-latest`. One retry on JSON parse failure, then a stub meta is written with `error` field so the batch keeps going.
- **Graph determinism**: nodes sorted by entity name; edges sorted by `(source, target, relation, from_doc)`. Same meta input → byte-identical `graph.json`.
- **Entity type conflicts** (same name, different types across docs): majority vote, alphabetical tie-break, warning to stderr.
- **xlsx filename list** is truncated to 500 names in the prompt to cap token cost.
- **Table families** are derived by (1) folder prefix and (2) PascalCase first-word grouping (salvaged from [old/wiki_compiler.py:105](old/wiki_compiler.py#L105)). Singletons go to `_misc`.
- **FK detection** is convention-based: a field matching `^(.+?)[_]?[Ii][Dd]s?$` whose stem (case-insensitive) matches another table name becomes a `references` FK edge. Stored in `_tables/table_fk_registry.json`, not in `graph.json` (to keep the graph focused on entities/docs/tables rather than inter-table edges).
- **Table cache**: `_tables/schemas.json` keys each table by `(rel_path, mtime, size)`. Unchanged xlsx files are reused from cache on rerun — full rescan of 7500 tables takes ~3min first time, ~1s thereafter.
- **Table header detection**: reads first 3 rows, picks the row with highest identifier-like score (English field names preferred over Chinese comment rows). Imperfect on mixed headers — some tables end up with Chinese headers.

## Data Integrity

- `knowledge/wiki/_meta/` is the source of truth for graph assembly — do not edit by hand.
- `wiki/graph.json` and `wiki/index.md` are generated — regenerate via `python build_wiki.py --stage graph`, never edit manually.
- Legacy scripts in `old/` are preserved only as reference for business rules (CN-EN table-name mapping, xlsx noise filtering, lint heuristics). Don't import from `old/`.
