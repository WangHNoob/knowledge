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


def write_index(graph: dict, metas: list[dict]) -> None:
    nodes = graph["nodes"]
    edges = graph["edges"]

    # 按页面类型分组
    by_type: dict[str, list[dict]] = collections.defaultdict(list)
    for m in metas:
        pt = m.get("page_type") or "unknown"
        by_type[pt].append(m)

    # 入度统计：最常被引用的实体（排除 doc->entity 边，避免文档引用稀释实体间排名）
    type_map = {n["id"]: n["type"] for n in nodes}
    indeg: collections.Counter = collections.Counter()
    for e in edges:
        if type_map.get(e["source"]) == "doc":
            continue
        indeg[e["target"]] += 1
    top_entities = indeg.most_common(20)

    # 类型分布
    type_dist: collections.Counter = collections.Counter(n["type"] for n in nodes)
    rel_dist: collections.Counter = collections.Counter(e["relation"] for e in edges)

    # 文档 -> 表 引用视图
    doc_to_tables: dict[str, list[str]] = collections.defaultdict(list)
    for e in edges:
        if type_map.get(e["source"]) == "doc" and type_map.get(e["target"]) == "table":
            doc_to_tables[e["source"]].append(e["target"])

    lines: list[str] = []
    lines.append("---")
    lines.append("title: Wiki Knowledge Index")
    lines.append("type: index")
    lines.append("---")
    lines.append("")
    lines.append("# Wiki 知识索引")
    lines.append("")
    doc_node_count = sum(1 for n in nodes if n["type"] == "doc")
    table_node_count = sum(1 for n in nodes if n["type"] == "table")
    entity_node_count = len(nodes) - doc_node_count - table_node_count

    lines.append("## 概览")
    lines.append(f"- 文档数: {len(metas)} (图中 doc 节点: {doc_node_count})")
    lines.append(f"- 配置表数: {table_node_count}")
    lines.append(f"- 其它实体数: {entity_node_count}")
    lines.append(f"- 关系数: {len(edges)}")
    error_docs = [m for m in metas if m.get("error")]
    if error_docs:
        lines.append(f"- 提取失败: {len(error_docs)}")
    lines.append("")

    lines.append("## 页面类型分布")
    for page_type, dirname in PAGE_TYPE_DIRS.items():
        count = len(by_type.get(page_type, []))
        lines.append(f"- `{page_type}` ({dirname}/): {count}")
    lines.append("")

    lines.append("## 实体类型分布")
    for t, c in sorted(type_dist.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{t}`: {c}")
    lines.append("")

    lines.append("## 关系类型分布")
    for t, c in sorted(rel_dist.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{t}`: {c}")
    lines.append("")

    if top_entities:
        lines.append("## 被引用最多的实体 (Top 20，仅实体间关系)")
        lines.append("| 实体 | 被引用次数 | 类型 | Wiki 页面 |")
        lines.append("|------|-----------|------|-----------|")
        page_map = {n["id"]: n["wiki_page"] for n in nodes}
        for name, count in top_entities:
            page = page_map.get(name) or "—"
            lines.append(f"| {name} | {count} | {type_map.get(name, '?')} | {page} |")
        lines.append("")

    if doc_to_tables:
        lines.append("## 文档 → 引用的配置表")
        for doc in sorted(doc_to_tables.keys()):
            tables = sorted(set(doc_to_tables[doc]))
            lines.append(f"- **{doc}** ({len(tables)}): " + ", ".join(
                f"`{t}`" for t in tables
            ))
        lines.append("")

    # 表族汇总
    group_counts: collections.Counter = collections.Counter()
    for n in nodes:
        if n["type"] == "table":
            group_counts[n.get("group") or "_misc"] += 1
    if group_counts:
        lines.append(f"## 表族分布 (Top 30，共 {len(group_counts)} 族)")
        lines.append("| 族 | 表数 | Wiki 页面 |")
        lines.append("|----|------|-----------|")
        for g, c in group_counts.most_common(30):
            slug = re.sub(r"[^\w.-]+", "_", g).strip("_") or "_group"
            lines.append(f"| `{g}` | {c} | tables/{slug}.md |")
        lines.append("")

    lines.append("## Wiki 页面清单")
    for page_type, dirname in PAGE_TYPE_DIRS.items():
        subset = sorted(by_type.get(page_type, []), key=lambda m: m.get("title") or "")
        if not subset:
            continue
        lines.append(f"### {page_type} — `{dirname}/`")
        for m in subset:
            title = m.get("title") or m.get("source")
            src = m.get("source")
            wp = m.get("wiki_path") or "?"
            lines.append(f"- [{title}]({wp}) — 来源: {src}")
        lines.append("")

    if error_docs:
        lines.append("## 提取失败文档")
        for m in error_docs:
            lines.append(f"- {m.get('source')}: {m.get('error')}")
        lines.append("")

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
