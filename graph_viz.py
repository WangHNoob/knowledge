# -*- coding: utf-8 -*-
"""
知识图谱可视化（交互式 HTML）

过滤策略（默认视图）：
  seed = 所有 doc 节点 + 所有 LLM 抽取的实体（system/activity/concept/...）
         + LLM 抽取时命中的 table 节点
         + seed 沿 FK 1 跳邻居（table-table）

然后把 seed 子图内的所有边（doc->table, entity->entity, FK）都放进视图。

产物：
  knowledge/wiki/graph.html    单文件 vis.js 可视化（CDN）

用法：
  python graph_viz.py [--open]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
WIKI_DIR = os.path.join(PROJECT_ROOT, "knowledge", "wiki")
GRAPH_JSON = os.path.join(WIKI_DIR, "graph.json")
FK_REGISTRY = os.path.join(WIKI_DIR, "_tables", "table_fk_registry.json")
HTML_OUT = os.path.join(WIKI_DIR, "graph.html")

# 节点类型 → 颜色（vis.js group）
TYPE_COLORS = {
    "doc":       "#ff8a5b",   # 橙：策划文档
    "system":    "#4fc3f7",   # 蓝：系统
    "activity":  "#ba68c8",   # 紫：活动
    "concept":   "#aed581",   # 绿：概念
    "resource":  "#ffd54f",   # 黄：资源
    "attribute": "#f06292",   # 粉：属性
    "table":     "#90a4ae",   # 灰：配置表
}


def _load_graph() -> dict:
    with open(GRAPH_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_fk() -> list[dict]:
    if not os.path.exists(FK_REGISTRY):
        return []
    with open(FK_REGISTRY, "r", encoding="utf-8") as f:
        return json.load(f)


def _filter_subgraph(graph: dict, fk_edges: list[dict]) -> tuple[list, list]:
    nodes = graph["nodes"]
    edges = graph["edges"]

    by_type: dict[str, list[dict]] = {}
    for n in nodes:
        by_type.setdefault(n["type"], []).append(n)

    id_to_node = {n["id"]: n for n in nodes}

    # seed：非 table 节点 + LLM 抽取时作为实体出现的 table 节点
    # 这里判定"LLM 抽取的 table"：graph.json 里的 table 节点没有 group 字段（有 group
    # 的是 table_analyzer 新加的全量 table）。
    seed: set[str] = set()
    for n in nodes:
        if n["type"] != "table":
            seed.add(n["id"])
        else:
            if "group" not in n:  # LLM 侧扩展出来的 table
                seed.add(n["id"])

    # 所有出现在 FK 边中的表节点都拉进视图（用户要求拉全量 705 条 FK）
    fk_nodes: set[str] = set()
    for e in fk_edges:
        fk_nodes.add(e["source"])
        fk_nodes.add(e["target"])

    # seed 沿 FK 1 跳邻居（冗余安全）
    fk_neighbors: set[str] = set()
    for e in fk_edges:
        if e["source"] in seed:
            fk_neighbors.add(e["target"])
        if e["target"] in seed:
            fk_neighbors.add(e["source"])

    keep = seed | fk_neighbors | fk_nodes
    keep &= set(id_to_node.keys())

    # 收集边：现有 graph.json 的边（两端都在 keep）+ FK 边（两端都在 keep）
    kept_edges: list[dict] = []
    seen_edge_keys: set[tuple] = set()

    for e in edges:
        if e["source"] in keep and e["target"] in keep:
            key = (e["source"], e["target"], e.get("relation", ""))
            if key in seen_edge_keys:
                continue
            seen_edge_keys.add(key)
            kept_edges.append({
                "from": e["source"],
                "to": e["target"],
                "label": e.get("relation", ""),
                "title": f"{e.get('relation','')}  (from {e.get('from_doc','')})",
                "arrows": "to",
                "color": {"color": "#888"},
                "edge_kind": "semantic",
            })

    for e in fk_edges:
        if e["source"] in keep and e["target"] in keep:
            key = (e["source"], e["target"], "fk")
            if key in seen_edge_keys:
                continue
            seen_edge_keys.add(key)
            kept_edges.append({
                "from": e["source"],
                "to": e["target"],
                "label": e.get("field", "FK"),
                "title": f"FK: {e['source']}.{e.get('field','?')} → {e['target']}",
                "arrows": "to",
                "dashes": True,
                "color": {"color": "#6aa9c2"},
                "edge_kind": "fk",
            })

    # 节点 payload
    kept_nodes: list[dict] = []
    for nid in sorted(keep):
        n = id_to_node[nid]
        t = n["type"]
        kept_nodes.append({
            "id": nid,
            "label": nid,
            "group": t,
            "title": _node_tooltip(n),
            "color": {"background": TYPE_COLORS.get(t, "#cccccc"),
                      "border": "#333"},
            "wiki_page": n.get("wiki_page"),
        })
    return kept_nodes, kept_edges


def _node_tooltip(n: dict) -> str:
    parts = [f"<b>{n['id']}</b>",
             f"type: {n['type']}"]
    if n.get("wiki_page"):
        parts.append(f"page: {n['wiki_page']}")
    if n.get("group"):
        parts.append(f"group: {n['group']}")
    return "<br>".join(parts)


# =================================================================
# HTML
# =================================================================

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Wiki 知识图谱</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  html, body { margin:0; height:100%; font-family: -apple-system, "Segoe UI", sans-serif; background:#1a1a2e; color:#eee; }
  #graph { width:100vw; height:100vh; }
  #panel {
    position:fixed; top:14px; left:14px;
    background:rgba(20,20,40,0.9); backdrop-filter: blur(8px);
    padding:14px 16px; border-radius:10px;
    border:1px solid rgba(255,255,255,0.08);
    max-width:320px; z-index:10;
    font-size:13px; line-height:1.5;
  }
  #panel h3 { margin:0 0 8px; font-size:14px; color:#fff; }
  #panel .legend { margin:8px 0; }
  #panel .legend span {
    display:inline-block; padding:2px 8px; margin:2px 4px 2px 0;
    border-radius:10px; font-size:11px; color:#111;
  }
  #panel .stats { color:#aaa; font-size:12px; margin-top:6px; }
  #panel label { display:block; margin:4px 0; cursor:pointer; }
  #panel input[type=text] { width:100%; padding:4px 6px; border-radius:4px;
                            border:1px solid #444; background:#222; color:#eee; }
  #detail {
    position:fixed; right:14px; top:14px; width:300px;
    background:rgba(20,20,40,0.9); backdrop-filter: blur(8px);
    padding:14px 16px; border-radius:10px;
    border:1px solid rgba(255,255,255,0.08);
    z-index:10; font-size:13px; display:none;
    max-height: 80vh; overflow-y: auto;
  }
  #detail a { color:#4fc3f7; text-decoration: none; }
  #detail a:hover { text-decoration: underline; }
</style>
</head>
<body>
<div id="panel">
  <h3>Wiki 知识图谱</h3>
  <div class="stats" id="stats"></div>
  <div class="legend" id="legend"></div>
  <label><input type="checkbox" id="f_doc"      checked> 文档 (doc)</label>
  <label><input type="checkbox" id="f_system"   checked> 系统 (system)</label>
  <label><input type="checkbox" id="f_activity" checked> 活动 (activity)</label>
  <label><input type="checkbox" id="f_concept"  checked> 概念 (concept)</label>
  <label><input type="checkbox" id="f_resource" checked> 资源 (resource)</label>
  <label><input type="checkbox" id="f_attribute" checked> 属性 (attribute)</label>
  <label><input type="checkbox" id="f_table"    checked> 配置表 (table)</label>
  <label><input type="checkbox" id="f_fk"       checked> 显示 FK 虚线边</label>
  <input type="text" id="search" placeholder="搜索节点..." />
</div>
<div id="detail"></div>
<div id="graph"></div>

<script>
const RAW_NODES = __NODES__;
const RAW_EDGES = __EDGES__;
const TYPE_COLORS = __COLORS__;

const legendEl = document.getElementById('legend');
Object.entries(TYPE_COLORS).forEach(([t, c]) => {
  const s = document.createElement('span');
  s.style.background = c;
  s.textContent = t;
  legendEl.appendChild(s);
});
document.getElementById('stats').innerHTML =
  `节点: ${RAW_NODES.length} · 边: ${RAW_EDGES.length}`;

const nodesDS = new vis.DataSet(RAW_NODES);
const edgesDS = new vis.DataSet(RAW_EDGES);

const container = document.getElementById('graph');
const network = new vis.Network(container, { nodes: nodesDS, edges: edgesDS }, {
  physics: {
    solver: "forceAtlas2Based",
    forceAtlas2Based: { gravitationalConstant: -50, springLength: 90, avoidOverlap: 0.3 },
    stabilization: { iterations: 200 }
  },
  interaction: { hover: true, tooltipDelay: 150 },
  nodes: { shape: "dot", size: 14, font: { color: "#fff", size: 13 } },
  edges: { font: { color: "#aaa", size: 10, strokeWidth: 0 }, smooth: { type: "continuous" } }
});

function applyFilters() {
  const active = {
    doc:       document.getElementById('f_doc').checked,
    system:    document.getElementById('f_system').checked,
    activity:  document.getElementById('f_activity').checked,
    concept:   document.getElementById('f_concept').checked,
    resource:  document.getElementById('f_resource').checked,
    attribute: document.getElementById('f_attribute').checked,
    table:     document.getElementById('f_table').checked,
  };
  const fkOn = document.getElementById('f_fk').checked;
  const q = document.getElementById('search').value.trim().toLowerCase();

  const visibleNodeIds = new Set();
  RAW_NODES.forEach(n => {
    if (!active[n.group]) return;
    if (q && !n.id.toLowerCase().includes(q)) return;
    visibleNodeIds.add(n.id);
  });

  nodesDS.update(RAW_NODES.map(n => ({
    id: n.id, hidden: !visibleNodeIds.has(n.id)
  })));
  edgesDS.update(RAW_EDGES.map((e, i) => ({
    id: i,
    hidden: !(visibleNodeIds.has(e.from) && visibleNodeIds.has(e.to)
              && (fkOn || e.edge_kind !== 'fk'))
  })));
}
// 为边补 id（vis-network 需要）
RAW_EDGES.forEach((e, i) => e.id = i);
edgesDS.clear();
edgesDS.add(RAW_EDGES);

['f_doc','f_system','f_activity','f_concept','f_resource','f_attribute','f_table','f_fk']
  .forEach(id => document.getElementById(id).addEventListener('change', applyFilters));
document.getElementById('search').addEventListener('input', applyFilters);

// 节点点击：展示详情 + 邻居
const detailEl = document.getElementById('detail');
network.on('click', params => {
  if (!params.nodes.length) { detailEl.style.display = 'none'; return; }
  const id = params.nodes[0];
  const n = RAW_NODES.find(x => x.id === id);
  if (!n) return;
  const neighbors = RAW_EDGES.filter(e => e.from === id || e.to === id);
  const outgoing = neighbors.filter(e => e.from === id)
    .map(e => `<li>→ ${e.to} <span style="color:#888">(${e.label || e.edge_kind})</span></li>`).join('');
  const incoming = neighbors.filter(e => e.to === id)
    .map(e => `<li>← ${e.from} <span style="color:#888">(${e.label || e.edge_kind})</span></li>`).join('');
  const pageLink = n.wiki_page
    ? `<p><a href="${n.wiki_page}" target="_blank">打开 wiki 页面</a></p>` : '';
  detailEl.innerHTML = `
    <h3 style="margin:0 0 6px">${n.id}</h3>
    <p style="color:#aaa;margin:0">type: ${n.group}</p>
    ${pageLink}
    <p><b>出边 (${outgoing ? neighbors.filter(e=>e.from===id).length : 0}):</b></p>
    <ul style="margin:4px 0 8px;padding-left:18px">${outgoing || '<li style="color:#666">无</li>'}</ul>
    <p><b>入边 (${incoming ? neighbors.filter(e=>e.to===id).length : 0}):</b></p>
    <ul style="margin:4px 0;padding-left:18px">${incoming || '<li style="color:#666">无</li>'}</ul>
  `;
  detailEl.style.display = 'block';
});
</script>
</body>
</html>
"""


def run(open_browser: bool = False) -> str:
    if not os.path.exists(GRAPH_JSON):
        print(f"[error] {GRAPH_JSON} 不存在，请先运行 graph_builder.py")
        return ""

    graph = _load_graph()
    fk_edges = _load_fk()
    nodes, edges = _filter_subgraph(graph, fk_edges)

    n_fk = sum(1 for e in edges if e.get("edge_kind") == "fk")
    n_sem = len(edges) - n_fk
    print(f"裁剪后：{len(nodes)} 节点 · {len(edges)} 边 (语义 {n_sem} / FK {n_fk})")

    html = (HTML_TEMPLATE
            .replace("__NODES__", json.dumps(nodes, ensure_ascii=False))
            .replace("__EDGES__", json.dumps(edges, ensure_ascii=False))
            .replace("__COLORS__", json.dumps(TYPE_COLORS, ensure_ascii=False)))

    with open(HTML_OUT, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"写入 {HTML_OUT}")

    if open_browser:
        webbrowser.open("file:///" + HTML_OUT.replace(os.sep, "/"))
    return HTML_OUT


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Interactive wiki graph visualization")
    parser.add_argument("--open", action="store_true", help="生成后在浏览器打开")
    args = parser.parse_args()
    run(open_browser=args.open)
