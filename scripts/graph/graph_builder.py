# -*- coding: utf-8 -*-
"""
确定性图谱组装（无 LLM）

读取 knowledge/wiki/_meta/*.json，合并同名实体，生成：
  knowledge/wiki/graph.json
  knowledge/wiki/index.md

输出可复现：实体按名字字典序排序，边按 (source, target, relation, from_doc) 排序。
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

from scripts.config import PATHS

PROJECT_ROOT = str(PATHS.project_root)
WIKI_DIR = str(PATHS.wiki_dir)
META_DIR = str(PATHS.wiki_meta_dir)
TABLES_REGISTRY_DIR = str(PATHS.wiki_tables_registry_dir)
SCHEMAS_PATH = str(PATHS.schemas_json_path)
GRAPH_PATH = str(PATHS.graph_json_path)
INDEX_PATH = str(PATHS.index_md_path)

PAGE_TYPE_DIRS = {
    "system_rule": "systems",
    "table_schema": "tables",
    "numerical_convention": "numerical",
    "activity_template": "activities",
    "combat_framework": "combat",
}


def _load_metas() -> list[dict]:
    if not os.path.isdir(META_DIR):
        return []
    metas = []
    for fn in sorted(os.listdir(META_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(META_DIR, fn), "r", encoding="utf-8") as f:
                metas.append(json.load(f))
        except Exception as e:
            print(f"[warn] failed to load {fn}: {e}", file=sys.stderr)
    return metas


def _merge_entities(metas: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """合并同名实体。返回 (name -> resolved_type, name -> wiki_page or '')。"""
    type_votes: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    for m in metas:
        for e in m.get("entities") or []:
            name = e.get("name")
            etype = e.get("type")
            if name and etype:
                type_votes[name][etype] += 1

    # wiki_page 来自 meta.title 与 meta.wiki_path 的映射
    title_to_page: dict[str, str] = {}
    for m in metas:
        title = m.get("title")
        wp = m.get("wiki_path")
        if title and wp and title not in title_to_page:
            title_to_page[title] = wp

    resolved_type: dict[str, str] = {}
    wiki_page: dict[str, str] = {}
    for name, counter in type_votes.items():
        # 多数票；平票按类型名字典序，保证可复现
        top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        resolved_type[name] = top[0][0]
        if len(counter) > 1:
            print(
                f"[warn] entity {name!r} has conflicting types: {dict(counter)}; "
                f"picked {top[0][0]}",
                file=sys.stderr,
            )
        wiki_page[name] = title_to_page.get(name, "")
    return resolved_type, wiki_page


def _collect_edges(metas: list[dict], valid_names: set[str]) -> list[dict]:
    """收集边，去重 + 过滤端点不在实体集合中的边。"""
    seen: set[tuple[str, str, str, str]] = set()
    edges: list[dict] = []
    for m in metas:
        src_doc = m.get("source", "")
        for r in m.get("relationships") or []:
            s = r.get("source")
            t = r.get("target")
            rel = r.get("relation")
            if not s or not t or not rel:
                continue
            if s not in valid_names or t not in valid_names:
                continue
            key = (s, t, rel, src_doc)
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "source": s, "target": t,
                "relation": rel, "from_doc": src_doc,
            })
    edges.sort(key=lambda e: (e["source"], e["target"],
                              e["relation"], e["from_doc"]))
    return edges


def _load_table_registry() -> dict:
    """加载 table_analyzer.py 生成的 schemas.json；不存在时返回 {}。"""
    if not os.path.exists(SCHEMAS_PATH):
        return {}
    try:
        with open(SCHEMAS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] failed to load {SCHEMAS_PATH}: {e}", file=sys.stderr)
        return {}


def _build_table_nodes(schemas: dict,
                       existing_entity_names: set[str]) -> list[dict]:
    """为 gamedata 里所有 xlsx 生成 type=table 节点。

    如果一张表已经是 LLM 抽取出的实体（existing_entity_names），跳过以免重复。
    wiki_page 指向该表所属表族页面。
    """
    nodes: list[dict] = []
    for tname in sorted(schemas.keys()):
        if tname in existing_entity_names:
            continue
        info = schemas[tname]
        group = info.get("group") or "_misc"
        wiki_page = f"tables/{_slugify_group_for_path(group)}.md"
        nodes.append({
            "id": tname,
            "type": "table",
            "wiki_page": wiki_page,
            "group": group,
        })
    return nodes


def _slugify_group_for_path(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name).strip("_") or "_group"


def _build_doc_nodes_and_edges(
    metas: list[dict], entity_types: dict[str, str]
) -> tuple[list[dict], list[dict]]:
    """为每个 docx 生成 type=doc 节点，并添加 doc -[references]-> table 边。

    只连接 type=table 的实体（见 WIKI_ARCHITECTURE.md §4 中"表与文档的关系"语义）。
    """
    doc_nodes: list[dict] = []
    doc_edges: list[dict] = []
    seen_doc_ids: set[str] = set()
    for m in metas:
        src = m.get("source")
        if not src or src in seen_doc_ids:
            continue
        seen_doc_ids.add(src)
        doc_nodes.append({
            "id": src,
            "type": "doc",
            "wiki_page": m.get("wiki_path") or None,
        })
        # doc -[references]-> table (仅对表实体连边)
        emitted: set[str] = set()
        for e in m.get("entities") or []:
            name = e.get("name")
            if not name or name in emitted:
                continue
            if entity_types.get(name) != "table":
                continue
            emitted.add(name)
            doc_edges.append({
                "source": src, "target": name,
                "relation": "references", "from_doc": src,
            })
    doc_nodes.sort(key=lambda n: n["id"])
    doc_edges.sort(key=lambda e: (e["source"], e["target"],
                                  e["relation"], e["from_doc"]))
    return doc_nodes, doc_edges


def build_graph() -> dict:
    metas = _load_metas()
    resolved_type, wiki_page = _merge_entities(metas)

    # LLM 抽出的实体里，有些名字直接对应一张配置表；
    # 若 table_analyzer 注册表里能找到，就给它一个 wiki_page 指向表族页面。
    schemas = _load_table_registry()
    for name, etype in list(resolved_type.items()):
        if etype == "table" and name in schemas and not wiki_page.get(name):
            g = schemas[name].get("group") or "_misc"
            wiki_page[name] = f"tables/{_slugify_group_for_path(g)}.md"

    entity_nodes = [
        {"id": name, "type": resolved_type[name],
         "wiki_page": wiki_page.get(name) or None}
        for name in sorted(resolved_type.keys())
    ]

    # 全量表节点：gamedata 下每个 xlsx 都进图（不重复 LLM 已有实体）
    entity_name_set = set(resolved_type.keys())
    table_nodes = _build_table_nodes(schemas, entity_name_set)

    entity_edges = _collect_edges(metas, entity_name_set)

    # doc 节点需要看到所有 type=table 节点（含 table_analyzer 引入的）才能正确建边。
    all_table_names = entity_name_set | {n["id"] for n in table_nodes}
    # 对 doc_edges 来说，资源还是 entity_types；构造一个扩展后的类型字典，
    # 让新的 table 节点也能被识别为 "table"
    augmented_types = dict(resolved_type)
    for n in table_nodes:
        augmented_types[n["id"]] = "table"
    doc_nodes, doc_edges = _build_doc_nodes_and_edges(metas, augmented_types)

    # 合并：doc 节点排在最前，其次实体节点（按类型+名字），最后是 table 节点
    nodes = doc_nodes + entity_nodes + table_nodes
    edges = doc_edges + entity_edges
    edges.sort(key=lambda e: (e["source"], e["target"],
                              e["relation"], e["from_doc"]))

    return {
        "nodes": nodes, "edges": edges,
        "_meta_count": len(metas),
        "_table_count": len(schemas),
    }


def write_graph(graph: dict) -> None:
    os.makedirs(WIKI_DIR, exist_ok=True)
    serializable = {"nodes": graph["nodes"], "edges": graph["edges"]}
    with open(GRAPH_PATH, "w", encoding="utf-8") as f:
        json.dump(serializable, f, ensure_ascii=False, indent=2, sort_keys=False)


def _count_wiki_pages_by_type() -> dict[str, list[dict]]:
    """Scan wiki directories for all .md files, group by frontmatter type.

    Returns {page_type: [{title, wiki_path, source, summary, tables, entities}, ...]}.
    This covers ALL wiki pages (LLM-extracted + deterministically generated),
    not just those with _meta/*.json entries.
    """
    result: dict[str, list[dict]] = collections.defaultdict(list)
    yaml_pat = re.compile(r"^---\s*$")

    for page_type, dirname in PAGE_TYPE_DIRS.items():
        dir_path = os.path.join(WIKI_DIR, dirname)
        if not os.path.isdir(dir_path):
            continue
        for fn in sorted(os.listdir(dir_path)):
            if not fn.endswith(".md"):
                continue
            fpath = os.path.join(dir_path, fn)
            wiki_path = f"{dirname}/{fn}"
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
            except Exception:
                continue

            # Parse YAML frontmatter
            lines = content.split("\n")
            fm: dict[str, str] = {}
            in_fm = False
            fm_closed = False
            fm_end_line: int | None = None
            for i, line in enumerate(lines):
                if yaml_pat.match(line):
                    if not in_fm:
                        in_fm = True
                        continue
                    else:
                        fm_closed = True
                        fm_end_line = i
                        break
                if in_fm:
                    m = re.match(r"^(\w+):\s*(.*)", line)
                    if m:
                        fm[m.group(1)] = m.group(2).strip().strip('"')

            # Extract first substantive paragraph after frontmatter
            summary = ""
            if fm_closed and fm_end_line is not None:
                for i in range(fm_end_line + 1, len(lines)):
                    stripped = lines[i].strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    summary = stripped
                    break

            entry = {
                "title": fm.get("title") or fn.replace(".md", ""),
                "wiki_path": wiki_path,
                "source": fm.get("source", ""),
                "summary": summary,
            }
            result[page_type].append(entry)

    # Sort each group by title
    for pt in result:
        result[pt].sort(key=lambda m: m["title"])
    return result


def _extract_page_tables(nodes: list[dict], metas: list[dict]) -> dict[str, list[str]]:
    """Build mapping: wiki_page_path -> list of table names mentioned.

    Sources: doc->table edges from graph, and entity->table configured_in from metas.
    """
    page_tables: dict[str, set[str]] = collections.defaultdict(set)

    # From meta: title -> wiki_path, plus entities of type table
    for m in metas:
        wp = m.get("wiki_path") or ""
        if not wp:
            continue
        for e in m.get("entities") or []:
            if e.get("type") == "table":
                page_tables[wp].add(e["name"])

    # From graph: doc nodes reference table nodes
    wp_map = {n["id"]: n.get("wiki_page") or "" for n in nodes}
    type_map = {n["id"]: n["type"] for n in nodes}

    return {wp: sorted(list(tables)) for wp, tables in page_tables.items()}


def _extract_page_entities(metas: list[dict]) -> dict[str, list[str]]:
    """Build mapping: wiki_page_path -> list of non-table entity names mentioned."""
    page_ents: dict[str, set[str]] = collections.defaultdict(set)
    for m in metas:
        wp = m.get("wiki_path") or ""
        if not wp:
            continue
        for e in m.get("entities") or []:
            if e.get("type") != "table":
                page_ents[wp].add(e["name"])
    return {wp: sorted(list(ents)) for wp, ents in page_ents.items()}


def _build_keyword_index(
    nodes: list[dict],
    wikipages_by_type: dict[str, list[dict]],
) -> dict[str, list[str]]:
    """Build keyword -> [wiki_page, ...] index for concept-based lookup.

    Keywords sourced from: entity names, table names, wiki page titles,
    table group names. Grouped by first character for readability.
    """
    kw_map: dict[str, set[str]] = collections.defaultdict(set)

    # Entity & table names from graph nodes.
    # Skip individual table entries (names containing "/") — they all point to
    # the same group page and bloating the keyword index. Use rg for exact
    # table name lookups instead.
    for n in nodes:
        name = n["id"]
        wp = n.get("wiki_page") or ""
        if not wp:
            continue
        if n["type"] == "table" and "/" in name:
            continue
        kw_map[name].add(wp)

    # Table group names -> their table_schema page
    for n in nodes:
        if n["type"] == "table":
            g = n.get("group") or ""
            wp = n.get("wiki_page") or ""
            if g and wp:
                kw_map[g].add(wp)

    # Wiki page titles -> their own page
    for entries in wikipages_by_type.values():
        for e in entries:
            title = e["title"]
            wp = e["wiki_path"]
            if title and wp:
                kw_map[title].add(wp)

    # Deduplicate and sort
    return {k: sorted(v) for k, v in kw_map.items()}


def _build_relationship_lists(
    edges: list[dict],
    nodes: list[dict],
    metas: list[dict],
) -> dict[str, list[dict]]:
    """Group non-doc edges by relation type, with readable labels.

    Returns {relation_type: [{source, target, from_doc}, ...]}.
    """
    type_map = {n["id"]: n["type"] for n in nodes}
    result: dict[str, list[dict]] = collections.defaultdict(list)

    for e in edges:
        # Skip doc->table references (shown in doc->table section)
        if type_map.get(e["source"]) == "doc":
            continue
        rel = e["relation"]
        result[rel].append({
            "source": e["source"],
            "target": e["target"],
            "from_doc": e.get("from_doc", ""),
        })

    # Sort each list
    for rel in result:
        result[rel].sort(key=lambda r: (r["source"], r["target"]))

    return dict(result)


# 关系类型的中文标签
REL_LABELS: dict[str, str] = {
    "depends_on": "依赖 (depends_on)",
    "unlocks": "解锁 (unlocks)",
    "produces": "产出 (produces)",
    "consumes": "消耗 (consumes)",
    "belongs_to": "归属 (belongs_to)",
    "references": "引用 (references)",
    "configured_in": "配置于 (configured_in)",
}

# 关系类型的展示顺序
REL_ORDER: list[str] = [
    "depends_on", "unlocks", "belongs_to",
    "produces", "consumes", "configured_in", "references",
]


def write_index(graph: dict, metas: list[dict]) -> None:
    nodes = graph["nodes"]
    edges = graph["edges"]

    type_map = {n["id"]: n["type"] for n in nodes}
    page_map = {n["id"]: n.get("wiki_page") or "" for n in nodes}

    # ── P0 fix: count ALL wiki pages from filesystem, not just meta ──
    wikipages_by_type = _count_wiki_pages_by_type()
    page_tables = _extract_page_tables(nodes, metas)
    page_entities = _extract_page_entities(metas)
    keyword_index = _build_keyword_index(nodes, wikipages_by_type)
    rel_lists = _build_relationship_lists(edges, nodes, metas)

    # ── Statistics ──
    total_wiki_pages = sum(len(v) for v in wikipages_by_type.values())
    doc_node_count = sum(1 for n in nodes if n["type"] == "doc")
    table_node_count = sum(1 for n in nodes if n["type"] == "table")
    entity_node_count = len(nodes) - doc_node_count - table_node_count

    indeg: collections.Counter = collections.Counter()
    for e in edges:
        if type_map.get(e["source"]) != "doc":
            indeg[e["target"]] += 1
    top_entities = indeg.most_common(20)

    type_dist: collections.Counter = collections.Counter(
        n["type"] for n in nodes
    )
    rel_dist: collections.Counter = collections.Counter(
        e["relation"] for e in edges
    )

    doc_to_tables: dict[str, set[str]] = collections.defaultdict(set)
    for e in edges:
        if type_map.get(e["source"]) == "doc" and type_map.get(e["target"]) == "table":
            doc_to_tables[e["source"]].add(e["target"])

    group_counts: collections.Counter = collections.Counter()
    for n in nodes:
        if n["type"] == "table":
            group_counts[n.get("group") or "_misc"] += 1

    error_docs = [m for m in metas if m.get("error")]
    page_type_counts = {
        pt: len(pages) for pt, pages in wikipages_by_type.items()
    }

    # ── Generate markdown ──
    lines: list[str] = []
    L = lines.append

    L("---")
    L("title: Wiki Knowledge Index")
    L("type: index")
    L("---")
    L("")
    L("# Wiki 知识索引")
    L("")

    # ── 1. 概览 ──
    L("## 概览")
    L(f"- 源文档数: {len(metas)}")
    L(f"- Wiki 页面总数: {total_wiki_pages}")
    L(f"- 图谱节点: {len(nodes)} (doc {doc_node_count} + 实体 {entity_node_count} + 表 {table_node_count})")
    L(f"- 图谱边: {len(edges)}")
    if error_docs:
        L(f"- 提取失败文档: {len(error_docs)}")
    L("")

    # ── 2. 页面类型分布 ──
    L("## 页面类型分布")
    L("| 类型 | 目录 | 页面数 |")
    L("|------|------|--------|")
    for page_type, dirname in PAGE_TYPE_DIRS.items():
        count = page_type_counts.get(page_type, 0)
        L(f"| `{page_type}` | `{dirname}/` | {count} |")
    L("")

    # ── 3. 实体类型分布 ──
    L("## 实体类型分布")
    for t, c in sorted(type_dist.items(), key=lambda kv: (-kv[1], kv[0])):
        L(f"- `{t}`: {c}")
    L("")

    # ── 4. 关系类型分布 ──
    L("## 关系类型分布")
    for t, c in sorted(rel_dist.items(), key=lambda kv: (-kv[1], kv[0])):
        L(f"- `{t}`: {c}")
    L("")

    # ── 5. 概念 → 页面 关键词索引 ──
    if keyword_index:
        L("## 概念/关键词 → Wiki 页面（快速检索）")
        L("")
        L("> 按名称查找实体、系统、活动、配置表族对应的 Wiki 页面。")
        L("")
        # Group by first character for readability
        by_char: dict[str, list[tuple[str, list[str]]]] = collections.defaultdict(
            list
        )
        for kw, pages in keyword_index.items():
            if not kw.strip():
                continue
            by_char[kw[0].upper()].append((kw, pages))
        for char in sorted(by_char.keys()):
            items = sorted(by_char[char], key=lambda x: x[0].lower())
            L(f"### {char}")
            for kw, pages in items:
                links = " · ".join(f"[{p}]({p})" for p in pages)
                L(f"- **{kw}** → {links}")
            L("")
        L("")

    # ── 6. 实体间关系清单 ──
    if rel_lists:
        L("## 实体间关系清单")
        L("")
        for rel in REL_ORDER:
            entries = rel_lists.get(rel, [])
            if not entries:
                continue
            label = REL_LABELS.get(rel, rel)
            L(f"### {label} ({len(entries)} 条)")
            # Deduplicate (source, target) pairs
            seen: set[tuple[str, str]] = set()
            for r in entries:
                key = (r["source"], r["target"])
                if key in seen:
                    continue
                seen.add(key)
                src_page = page_map.get(r["source"], "")
                tgt_page = page_map.get(r["target"], "")
                src_link = f"[{r['source']}]({src_page})" if src_page else r["source"]
                tgt_link = f"[{r['target']}]({tgt_page})" if tgt_page else r["target"]
                L(f"- {src_link} → {tgt_link}")
            L("")

    # ── 7. 文档 → 引用的配置表 ──
    if doc_to_tables:
        L("## 文档 → 引用的配置表")
        for doc in sorted(doc_to_tables.keys()):
            tables = sorted(doc_to_tables[doc])
            L(f"- **{doc}** ({len(tables)}): " + ", ".join(
                f"`{t}`" for t in tables
            ))
        L("")

    # ── 8. 表族分布（全量） ──
    if group_counts:
        L(f"## 表族分布（共 {len(group_counts)} 族）")
        L("| 族 | 表数 | Wiki 页面 |")
        L("|----|------|-----------|")
        for g, c in group_counts.most_common():
            slug = re.sub(r"[^\w.-]+", "_", g).strip("_") or "_group"
            L(f"| `{g}` | {c} | tables/{slug}.md |")
        L("")

    # ── 9. Wiki 页面清单（含摘要 + 关联表） ──
    L("## Wiki 页面清单")
    for page_type, dirname in PAGE_TYPE_DIRS.items():
        entries = wikipages_by_type.get(page_type, [])
        if not entries:
            L(f"### {page_type} — `{dirname}/`")
            L("")
            L("*（暂无页面）*")
            L("")
            continue
        L(f"### {page_type} — `{dirname}/` ({len(entries)} 页)")
        for e in entries:
            wp = e["wiki_path"]
            title = e["title"]
            src = e["source"]
            summary = e["summary"]
            tables = page_tables.get(wp, [])
            entities = page_entities.get(wp, [])

            # Build metadata line
            meta_parts: list[str] = []
            if src:
                meta_parts.append(f"来源: {src}")
            if summary:
                # Truncate summary to keep index compact
                s = summary[:120] + "…" if len(summary) > 120 else summary
                meta_parts.append(s)
            if tables:
                meta_parts.append("关联表: " + ", ".join(f"`{t}`" for t in tables[:8]))
            if entities:
                meta_parts.append("实体: " + ", ".join(entities[:8]))

            L(f"- **[{title}]({wp})**")
            if meta_parts:
                L(f"  {' · '.join(meta_parts)}")
        L("")

    # ── 10. 提取失败文档 ──
    if error_docs:
        L("## 提取失败文档")
        for m in error_docs:
            L(f"- {m.get('source')}: {m.get('error')}")
        L("")

    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def run() -> None:
    metas = _load_metas()
    if not metas:
        print("没有 meta 文件，请先运行 wiki_extractor.py")
        return
    graph = build_graph()
    write_graph(graph)
    write_index(graph, metas)
    print(f"写入 {GRAPH_PATH}")
    print(f"  nodes: {len(graph['nodes'])}  edges: {len(graph['edges'])}")
    print(f"写入 {INDEX_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="确定性图谱组装：从 wiki/_meta/*.json 生成 graph.json"
    )
    parser.parse_args()
    run()
