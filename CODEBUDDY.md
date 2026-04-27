# CODEBUDDY.md This file provides guidance to WorkBuddy when working with code in this repository.

## Project Overview

kb-builder is a **game design knowledge base builder** that converts 策划 docx documents into a structured, LLM-generated wiki with a deterministic knowledge graph. Design reference: [WIKI_ARCHITECTURE.md](WIKI_ARCHITECTURE.md).

## Common Commands

```bash
# Full pipeline (convert -> extract -> graph), incremental
python build_wiki.py

# Force reconvert + re-extract everything
python build_wiki.py --force

# Run a single stage
python build_wiki.py --stage convert     # docx -> .cache md
python build_wiki.py --stage extract     # md -> wiki page + entities/relationships (LLM)
python build_wiki.py --stage graph       # meta -> graph.json + index.md (deterministic)

# Extract one doc only (useful for iterating on prompts)
python build_wiki.py --stage extract --only 装备异化.docx

# Individual scripts (direct invocation)
python batch_convert.py
python wiki_extractor.py [--force] [--only FILE.docx]
python graph_builder.py
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
  ├── systems/*.md          system_rule pages
  ├── tables/*.md           table_schema pages
  ├── numerical/*.md        numerical_convention pages
  ├── activities/*.md       activity_template pages
  ├── combat/*.md           combat_framework pages
  └── _meta/<stem>.json     sidecar: entities, relationships, content_hash
       │   graph_builder.py  (deterministic Python, no LLM)
       ▼
knowledge/wiki/graph.json   ← merged entity graph per WIKI_ARCHITECTURE.md §5.2
knowledge/wiki/index.md     ← stats, type breakdown, top entities, page list
```

`knowledge/gamedata/*.xlsx` (7500+ tables) is **not** converted — its filenames are injected into the extractor prompt so LLM can reference tables by exact name.

## Key Files

| File | Role |
|------|------|
| `doc_reader.py` | docx/xlsx parsing + caching + chunking; WPS image-ref repair (L33-105) |
| `batch_convert.py` | Orchestrates docx→md conversion under `knowledge/gamedocs/` |
| `wiki_specs/*.md` | 5 spec templates used as prompt fragments (system_rule, table_schema, numerical_convention, activity_template, combat_framework) |
| `wiki_extractor.py` | Per-docx LLM extraction → wiki page + meta sidecar |
| `graph_builder.py` | Merges meta sidecars into graph.json + index.md (deterministic) |
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

## Data Integrity

- `knowledge/wiki/_meta/` is the source of truth for graph assembly — do not edit by hand.
- `wiki/graph.json` and `wiki/index.md` are generated — regenerate via `python build_wiki.py --stage graph`, never edit manually.
- Legacy scripts in `old/` are preserved only as reference for business rules (CN-EN table-name mapping, xlsx noise filtering, lint heuristics). Don't import from `old/`.
