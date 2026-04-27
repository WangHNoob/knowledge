# Incremental Wiki Maintenance Plan

## Status: Not Yet Implemented

---

## Reference Architecture (from llm-wiki-agent)

The reference project (`llm-wiki-agent-main/`) uses three key ideas:
1. **`raw/` is immutable** — source documents are never modified in-place; changes = new files
2. **`log.md` append-only log** — every ingest/refresh is timestamped and traceable
3. **`refresh.py` hash-based change detection** — only reprocesses changed sources

---

## Current State vs Target

| Scenario | Current Behavior | Target Incremental Behavior |
|---|---|---|
| New docx added | Full re-extract all docs | Detect new file, extract only it |
| docx content changed | `content_hash` cache exists, needs `--force` | `refresh.py` auto-detects hash drift, re-extracts only that file |
| New xlsx added | Full rescan all 1243 files | Detect new files, append to schemas.json |
| xlsx structure changed | Cache key uses mtime+size, auto-refresh | Same — already works deterministically |
| New wiki page | Full pipeline re-run | `graph_builder` is deterministic — add/remove nodes |
| Health check | None | `health.py` checks orphans, broken wikilinks, index sync |

---

## New Files to Create

```
tools/
  health.py      # Fast structural checks: empty files, index sync, broken wikilinks
  refresh.py      # Hash-based change detection + selective re-ingest

knowledge/wiki/
  log.md          # Append-only operation log
  _meta/
    .refresh_cache.json   # Per-docx content_hash cache
    .xlsx_cache.json      # Per-xlsx (mtime, size) cache
```

---

## Design Constraints

- **`knowledge/gamedocs/` and `knowledge/gamedata/` are the raw/immutable layer**
  — treat as read-only; pipeline never modifies source files
- **`knowledge/wiki/` is fully generated** — any manual edits are overwritten on next run
- **`_meta/` is the source of truth for graph rebuild** — graph.json/index.md regenerated from `_meta/*.json` only
- **Incremental graph update** — instead of full rebuild, `graph_builder` should diff old vs new `_meta/` and only add/remove affected nodes/edges

---

## Incremental API (proposed)

```bash
# Full pipeline (unchanged)
uv run python run_pipeline.py

# Selective re-run (new)
uv run python tools/refresh.py          # detect changed sources, re-extract only
uv run python tools/refresh.py --force  # force re-ingest all

# Health check (new)
uv run python tools/health.py
uv run python tools/health.py --save    # save report to wiki/health-report.md

# Single-docx re-extract (new)
uv run python run_pipeline.py --stage extract --only 装备异化.docx

# After xlsx changes, re-run tables + graph (new)
uv run python run_pipeline.py --stage tables
uv run python run_pipeline.py --stage graph
uv run python run_pipeline.py --stage viz
```

---

## Implementation Order

1. **`tools/health.py`** — easiest, zero LLM calls, validates existing wiki structure
2. **`wiki/log.md`** — simple append-only log, integrate into pipeline stages
3. **`tools/refresh.py`** — hash cache for docx, detect drift, selective re-extract
4. **`_meta/.xlsx_cache.json`** — formalize xlsx cache path (already exists in `_tables/`)
5. **Incremental `graph_builder`** — diff `_meta/` to avoid full rebuild when only 1 doc changed
