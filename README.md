# KB Builder

A pipeline tool that converts game design documents (docx/xlsx) into a structured wiki with knowledge graph visualization.

## Pipeline

```
raw/gamedocs/ (docx source files)
  ↓ [convert] → doc_reader → raw markdown
  ↓ [extract] → wiki_extractor (LLM) → structured wiki pages + entity/relation meta
  ↓ [tables]  → table_analyzer → table schemas & registries
  ↓ [graph]   → graph_builder  → graph.json + index.md
  ↓ [viz]     → graph_viz     → interactive D3 visualization
```

## Quick Start

```bash
# Install dependencies
uv sync --extra llm

# Full pipeline
uv run python run_pipeline.py

# Single stage
uv run python run_pipeline.py --stage extract

# Force reprocess
uv run python run_pipeline.py --force
```

## Output Structure

| Directory | Description |
|-----------|-------------|
| `wiki/systems/` | System rules, processes, constraints |
| `wiki/activities/` | Activity templates and reward frameworks |
| `wiki/tables/` | Table schema definitions |
| `wiki/numerical/` | Numerical references and formulas |
| `wiki/combat/` | Combat formulas and attribute systems |
| `wiki/_meta/` | Entity and relation data (JSON) |
| `wiki/_tables/` | Table registries (schemas, groups, FK) |

## Tech Stack

- Python, LiteLLM (multi-LLM support)
- NetworkX (knowledge graph)
- D3.js (graph visualization)
