# -*- coding: utf-8 -*-
"""
当前项目知识库评估脚本（wiki/ 目录）

评估维度：
  1. 覆盖率    - 文档数、实体数、表-文档覆盖比
  2. 摘要覆盖  - 表出现在文档中的比例（等价于 kb-builder 的语义摘要覆盖）
  3. 交叉引用密度  - 平均每张表被多少文档引用；平均每篇文档引用多少张表
  4. 信息密度  - entities.md 每个实体条目的平均字节数
  5. 检索质量  - 10 道测试题，用 wiki 作为上下文提问 LLM（需配置 API Key）

用法：
  python eval_wiki.py                  # 维度 1-4（静态分析）
  python eval_wiki.py --llm            # 全部维度
  python eval_wiki.py --llm --limit 3  # LLM 只跑前 3 题
"""

import os
import re
import sys
import json
import argparse
from collections import defaultdict

# 路径设置
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, '.cache')

EXCEL_DIR  = os.path.join(BASE_DIR, 'knowledge', 'gamedata')
WIKI_DIR   = os.path.join(BASE_DIR, 'knowledge', 'wiki')
GAMEDOCS_DIR   = os.path.join(BASE_DIR, 'knowledge', 'gamedocs')

# Registry 自动生成在 knowledge/table_registry.json
REGISTRY_PATH  = os.path.join(BASE_DIR, 'knowledge', 'table_registry.json')

ENTITIES_DIR   = os.path.join(WIKI_DIR, 'entities')
CONCEPTS_PATH  = os.path.join(WIKI_DIR, 'concepts.md')


# ── 解析 entities.md ──────────────────────────────────────────────────────────

def parse_entities(path: str) -> dict:
    """
    返回 {table_name: {"refs": [(doc, section), ...], "ref_count": int, "doc_count": int}}
    格式：
      ## TableName (N refs)
      - doc.docx -> 节1, 节2
    """
    result = {}
    if not os.path.exists(path):
        return result

    with open(path, encoding='utf-8') as f:
        content = f.read()

    current = None
    refs     = []

    for line in content.splitlines():
        m_head = re.match(r'^##\s+(.+?)\s+\((\d+) refs\)', line)
        if m_head:
            if current:
                result[current] = {
                    "refs": refs,
                    "ref_count": sum(len(r[1]) for r in refs),
                    "doc_count": len(refs),
                    "char_count": sum(
                        len(f"- {r[0]} -> {', '.join(r[1])}") for r in refs
                    ) + len(f"## {current}"),
                }
            current = m_head.group(1).strip()
            refs = []
            continue

        m_ref = re.match(r'^-\s+(.+?)\s+->\s+(.*)', line)
        if m_ref and current:
            doc      = m_ref.group(1).strip()
            sections = [s.strip() for s in m_ref.group(2).split(',') if s.strip()]
            refs.append((doc, sections))

    if current:
        result[current] = {
            "refs": refs,
            "ref_count": sum(len(r[1]) for r in refs),
            "doc_count": len(refs),
            "char_count": sum(
                len(f"- {r[0]} -> {', '.join(r[1])}") for r in refs
            ) + len(f"## {current}"),
        }

    return result


# ── 解析 concepts.md ──────────────────────────────────────────────────────────

def parse_concepts(path: str) -> dict:
    """
    返回 {concept: {"docs": [...], "doc_count": int}}
    格式：
      ## 简介 (17 docs)
      - doc.docx
    """
    result  = {}
    current = None
    docs    = []

    if not os.path.exists(path):
        return result

    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip()
            m_head = re.match(r'^##\s+(.+?)\s+\((\d+) docs\)', line)
            if m_head:
                if current:
                    result[current] = {"docs": docs, "doc_count": len(docs)}
                current = m_head.group(1).strip()
                docs    = []
                continue
            m_doc = re.match(r'^-\s+(.+)', line)
            if m_doc and current:
                docs.append(m_doc.group(1).strip())

    if current:
        result[current] = {"docs": docs, "doc_count": len(docs)}

    return result


# ── 加载全部表名（来自 skill.md 或 registry） ─────────────────────────────────

def load_all_tables() -> set:
    """返回项目中所有表名的集合。

    优先使用自动生成的 table_registry.json，否则直接扫描 gamedata/ 文件系统。
    """
    # 方式1: 从自动生成的 registry 加载
    if os.path.exists(REGISTRY_PATH):
        with open(REGISTRY_PATH, encoding='utf-8') as f:
            return set(json.load(f).keys())

    # 方式2: 直接扫描文件系统
    gamedata_dir = os.path.join(BASE_DIR, 'knowledge', 'gamedata')
    if os.path.isdir(gamedata_dir):
        tables = set()
        for root, _dirs, files in os.walk(gamedata_dir):
            for fname in files:
                if fname.lower().endswith('.xlsx') and not fname.startswith('~'):
                    stem = fname[:-5]
                    rel = os.path.relpath(root, gamedata_dir)
                    tables.add(stem if rel == '.' else rel.replace(os.sep, '/') + '/' + stem)
        return tables

    return set()


# ── 统计每篇文档引用的表 ──────────────────────────────────────────────────────

def doc_to_tables(entities: dict) -> dict:
    """返回 {doc: [table, ...]}"""
    result = defaultdict(list)
    for tbl, v in entities.items():
        for doc, _ in v["refs"]:
            result[doc].append(tbl)
    return result


# ── 静态分析 ──────────────────────────────────────────────────────────────────

def _pct(n, total):
    return f"{n / total * 100:.1f}%" if total else "N/A"


def evaluate_static(entities: dict, concepts: dict, all_tables: set):
    total_tables = len(all_tables)

    print(f"\n{'=' * 60}")
    print(f"  当前项目 wiki/ 知识库评估报告")
    print(f"{'=' * 60}")

    # ── 维度 1：覆盖率 ────────────────────────────────────────────────────────
    print("\n【维度 1】覆盖率")
    # 文档数
    all_docs = set()
    for v in entities.values():
        for doc, _ in v["refs"]:
            all_docs.add(doc)
    gamedoc_files = [f for f in os.listdir(GAMEDOCS_DIR)
                     if f.endswith('.docx')] if os.path.exists(GAMEDOCS_DIR) else []

    print(f"  设计文档总数  : {len(gamedoc_files)} 个 (.docx)")
    print(f"  已被 wiki 索引: {len(all_docs)} 个文档")
    print(f"  实体（表名）数: {len(entities)}")
    print(f"  概念（章节）数: {len(concepts)}")
    print(f"  注册表总表数  : {total_tables}")

    covered = all_tables & set(entities.keys())
    uncovered = all_tables - set(entities.keys())
    print(f"\n  表覆盖率: {len(covered)}/{total_tables} ({_pct(len(covered), total_tables)})")
    print(f"  无文档引用: {len(uncovered)} 张表")
    if uncovered and len(uncovered) <= 20:
        print(f"  未覆盖示例: {', '.join(sorted(uncovered)[:10])}")

    # ── 维度 2：摘要覆盖（等价于被至少 1 个文档引用） ─────────────────────────
    print("\n【维度 2】文档引用覆盖率（等价于语义摘要）")
    covered_1doc = sum(1 for v in entities.values() if v["doc_count"] >= 1)
    covered_3doc = sum(1 for v in entities.values() if v["doc_count"] >= 3)
    covered_5doc = sum(1 for v in entities.values() if v["doc_count"] >= 5)

    print(f"  被 ≥1 个文档引用: {covered_1doc}/{len(entities)} ({_pct(covered_1doc, len(entities))})")
    print(f"  被 ≥3 个文档引用: {covered_3doc}/{len(entities)} ({_pct(covered_3doc, len(entities))})")
    print(f"  被 ≥5 个文档引用: {covered_5doc}/{len(entities)} ({_pct(covered_5doc, len(entities))})")

    # 概念噪声分析：通用章节名是否有意义
    generic_concepts = {"简介", "内容综述", "设计目的", "设计简述", "备注", "说明"}
    noisy = [c for c in concepts if c in generic_concepts]
    meaningful = [c for c in concepts if c not in generic_concepts]
    print(f"\n  概念总数: {len(concepts)}")
    print(f"  疑似通用噪声: {len(noisy)} 个 ({', '.join(noisy[:5])})")
    print(f"  业务专属概念: {len(meaningful)} 个 ({', '.join(meaningful[:5])})")

    # ── 维度 3：交叉引用密度 ──────────────────────────────────────────────────
    print("\n【维度 3】交叉引用密度")

    # 每张表被多少文档引用
    doc_counts = [v["doc_count"] for v in entities.values()]
    avg_docs_per_table = sum(doc_counts) / len(doc_counts) if doc_counts else 0
    print(f"  平均每表被引用文档数: {avg_docs_per_table:.1f}")
    print(f"  最多被引用: {max(doc_counts) if doc_counts else 0} 个文档")

    # 被引用最多的表
    top_tables = sorted(entities.items(), key=lambda x: -x[1]["doc_count"])[:8]
    print(f"\n  被最多文档引用的表 (前 8):")
    for tbl, v in top_tables:
        docs_sample = [r[0][:15] for r, _ in zip(v["refs"], range(3))]
        print(f"    {tbl:<40} {v['doc_count']} 个文档  {v['ref_count']} 处")

    # 每篇文档引用多少张表
    d2t = doc_to_tables(entities)
    table_counts_per_doc = [len(tbls) for tbls in d2t.values()]
    avg_tables_per_doc = sum(table_counts_per_doc) / len(table_counts_per_doc) if table_counts_per_doc else 0
    print(f"\n  平均每篇文档引用表数: {avg_tables_per_doc:.1f}")
    print(f"  引用表最多的文档 (前 5):")
    top_docs = sorted(d2t.items(), key=lambda x: -len(x[1]))[:5]
    for doc, tbls in top_docs:
        print(f"    {doc:<40} {len(tbls)} 张表")

    # ── 维度 4：信息密度 ──────────────────────────────────────────────────────
    print("\n【维度 4】单实体信息密度")
    char_counts = [v["char_count"] for v in entities.values()]
    if char_counts:
        avg = sum(char_counts) / len(char_counts)
        print(f"  均值={avg:.0f} 字节  最大={max(char_counts)}  最小={min(char_counts)}")
        buckets = [(0, 30), (30, 80), (80, 200), (200, 500), (500, 99999)]
        for lo, hi in buckets:
            cnt = sum(1 for x in char_counts if lo <= x < hi)
            bar = "█" * (cnt * 35 // (len(char_counts) or 1))
            tag = f"{lo}-{hi}" if hi < 99999 else f"{lo}+"
            print(f"    [{tag:>9}] {cnt:>5} 个  {bar}")

        sparse = sorted(entities.items(), key=lambda x: x[1]["char_count"])[:5]
        print(f"\n  信息最稀薄的 5 个实体:")
        for t, v in sparse:
            print(f"    {t:<40} {v['char_count']} 字节  {v['doc_count']} 个文档")


# ── LLM 检索质量评估 ──────────────────────────────────────────────────────────

TEST_QUESTIONS = [
    {
        "id": "Q1", "type": "表-文档定位",
        "question": "PlayerDealConfig 这张表在哪些设计文档中有提到？分别在哪些章节？",
        "keywords": ["PlayerDealConfig", "docx", "简介", "设计目的"],
        "search_key": "PlayerDealConfig",
    },
    {
        "id": "Q2", "type": "系统理解",
        "question": "神秘商店系统涉及哪些配置表？",
        "keywords": ["神秘商店", "ShopConfig", "商品", "PlayerShopType"],
        "search_key": "神秘商店",
    },
    {
        "id": "Q3", "type": "跨文档概念",
        "question": "哪些设计文档都涉及了排行榜设计？",
        "keywords": ["排行榜", "docx"],
        "search_key": "排行榜",
    },
    {
        "id": "Q4", "type": "文档覆盖",
        "question": "活动相关的配置表（Activity 开头）在设计文档中有哪些引用？",
        "keywords": ["Activity", "活动", "docx"],
        "search_key": "Activity",
    },
    {
        "id": "Q5", "type": "表关联推断",
        "question": "PVP 活动模板文档中涉及了哪些核心配置表？",
        "keywords": ["pvp", "PlayerDealConfig", "PlayerShape"],
        "search_key": "pvp活动模板",
    },
    {
        "id": "Q6", "type": "章节语义",
        "question": '设计文档中的"玩法界面"章节通常描述什么内容？哪些系统有该章节？',
        "keywords": ["玩法界面", "docx", "界面"],
        "search_key": "玩法界面",
    },
    {
        "id": "Q7", "type": "高频实体分析",
        "question": "被最多文档引用的配置表是什么？说明可能的原因。",
        "keywords": ["PlayerDealConfig", "PlayerShape", "PlayerShopType", "引用"],
        "search_key": None,  # 特殊处理：取高频实体
    },
    {
        "id": "Q8", "type": "无文档表识别",
        "question": "哪些配置表完全没有在任何设计文档中提到？给出几个例子。",
        "keywords": ["未引用", "没有", "文档"],
        "search_key": None,  # 特殊处理：取未覆盖表
    },
    {
        "id": "Q9", "type": "文档完整性",
        "question": "成就系统（成就.docx）文档中涉及了哪些配置表？",
        "keywords": ["Achievement", "成就", "docx"],
        "search_key": "成就",
    },
    {
        "id": "Q10", "type": "跨系统关联",
        "question": "装备系统（装备异化）和 PVP 系统在配置表层面是否有重叠？",
        "keywords": ["BpEquip", "装备", "pvp", "PlayerDeal"],
        "search_key": None,  # 特殊处理
    },
]


def _build_context(q: dict, entities: dict, concepts: dict,
                   all_tables: set, max_chars: int = 5000) -> str:
    """为一道题从 wiki 中提取相关上下文（模拟 RAG 检索）。"""
    parts = []

    key = q.get("search_key")

    if key:
        # 在 entities.md 中按关键词匹配表名
        for tbl, v in entities.items():
            if key.lower() in tbl.lower():
                lines = [f"## {tbl} ({v['doc_count']} 个文档引用)"]
                for doc, sections in v["refs"][:8]:
                    lines.append(f"- {doc} -> {', '.join(sections)}")
                parts.append("\n".join(lines))

        # 在 concepts.md 中按关键词匹配概念名
        for concept, v in concepts.items():
            if key.lower() in concept.lower():
                lines = [f"## 概念「{concept}」 ({v['doc_count']} 个文档)"]
                for doc in v["docs"][:8]:
                    lines.append(f"- {doc}")
                parts.append("\n".join(lines))

        # 在 entities.md 中按文档名匹配
        for tbl, v in entities.items():
            for doc, sections in v["refs"]:
                if key.lower() in doc.lower():
                    # 加入这篇文档引用的表
                    lines = [f"文档「{doc}」引用的表:"]
                    doc_tables = [t for t, ev in entities.items()
                                  if any(d == doc for d, _ in ev["refs"])]
                    lines.append(", ".join(doc_tables[:20]))
                    parts.append("\n".join(lines))
                    break  # 一篇文档只加一次

    # Q7：高频实体
    if q["id"] == "Q7":
        top = sorted(entities.items(), key=lambda x: -x[1]["doc_count"])[:10]
        lines = ["## 被最多文档引用的表（Top 10）"]
        for tbl, v in top:
            lines.append(f"- {tbl}: 被 {v['doc_count']} 个文档、{v['ref_count']} 处引用")
        parts.append("\n".join(lines))

    # Q8：未覆盖表
    if q["id"] == "Q8":
        uncovered = sorted(all_tables - set(entities.keys()))[:20]
        lines = ["## 未被任何文档引用的表（部分）"]
        for t in uncovered:
            lines.append(f"- {t}")
        parts.append("\n".join(lines))

    # Q10：多文档联合（装备 + PVP）
    if q["id"] == "Q10":
        for keyword in ["装备", "BpEquip", "pvp", "PlayerDeal"]:
            for tbl, v in entities.items():
                if keyword.lower() in tbl.lower():
                    lines = [f"## {tbl}"]
                    for doc, sections in v["refs"][:5]:
                        lines.append(f"- {doc} -> {', '.join(sections)}")
                    parts.append("\n".join(lines))
                    if sum(len(p) for p in parts) > max_chars // 2:
                        break

    # 去重并截断
    seen = set()
    deduped = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
        if sum(len(x) for x in deduped) > max_chars:
            break

    return "\n\n".join(deduped) if deduped else "（未找到相关内容）"


def _score(answer: str, keywords: list) -> int:
    if not answer or not keywords:
        return 5
    hit = sum(1 for kw in keywords if kw.lower() in answer.lower())
    return int(hit / len(keywords) * 10)


def _load_llm_config() -> tuple:
    """从 .env 加载 LLM 配置，返回 (api_key, base_url, model)。"""
    env_path = os.path.join(BASE_DIR, '.env')
    if os.path.exists(env_path):
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)

    api_key  = os.environ.get('OPENAI_API_KEY') or os.environ.get('DASHSCOPE_API_KEY', '')
    base_url = os.environ.get('LLM_BASE_URL')   or os.environ.get('OPENAI_BASE_URL', '')
    model    = os.environ.get('LLM_MODEL')       or os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
    return api_key, base_url, model


def evaluate_llm(entities: dict, concepts: dict, all_tables: set, limit: int = 0):
    api_key, base_url, model = _load_llm_config()

    if not api_key:
        print("\n[SKIP] 未配置 API Key，跳过 LLM 评估（设置 OPENAI_API_KEY 或 DASHSCOPE_API_KEY）")
        return

    from openai import OpenAI
    client_kwargs = {"api_key": api_key}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = OpenAI(**client_kwargs)

    questions = TEST_QUESTIONS[:limit] if limit else TEST_QUESTIONS

    print(f"\n【维度 5】LLM 检索质量评估")
    print(f"  模型: {model} | 题目: {len(questions)} 道")
    print(f"  {'ID':<5} {'类型':<14} {'得分':>6}  问题摘要")
    print(f"  {'-' * 55}")

    total_score = 0
    results     = []

    for q in questions:
        ctx = _build_context(q, entities, concepts, all_tables)

        prompt = (
            f"以下是游戏策划知识库内容（配置表-文档交叉引用）：\n\n{ctx}\n\n"
            f"问题：{q['question']}\n\n"
            f"基于以上内容回答，信息不足时请明确说明，保持简洁。"
        )
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "你是游戏策划助手，基于提供的知识库回答问题。"},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.1,
                max_tokens=400,
            )
            answer = resp.choices[0].message.content or ""
        except Exception as e:
            answer = f"[ERROR] {e}"

        score   = _score(answer, q["keywords"])
        total_score += score
        summary = q["question"][:28] + "..." if len(q["question"]) > 28 else q["question"]
        print(f"  {q['id']:<5} {q['type']:<14} {score:>4}/10  {summary}")

        results.append({
            "id": q["id"], "type": q["type"],
            "question": q["question"],
            "score": score,
            "answer": answer,
            "context_chars": len(ctx),
        })

    max_score = len(questions) * 10
    print(f"\n  总分: {total_score}/{max_score}  ({_pct(total_score, max_score)})")

    print(f"\n{'─' * 60}")
    print(f"  各题详细回答")
    print(f"{'─' * 60}")
    for r in results:
        print(f"\n  [{r['id']}] {r['type']}  得分: {r['score']}/10")
        print(f"  问题: {r['question']}")
        answer_lines = r['answer'].replace('\r\n', '\n').split('\n')
        for line in answer_lines:
            print(f"  | {line}")

    worst = min(results, key=lambda x: x["score"])
    print(f"\n{'─' * 60}")
    print(f"  【最差题 {worst['id']}】（得分 {worst['score']}/10）")
    print(f"  问题: {worst['question']}")
    q_obj = next(q for q in questions if q["id"] == worst["id"])
    ctx_w = _build_context(q_obj, entities, concepts, all_tables)
    print(f"  上下文 ({worst['context_chars']} 字节):")
    for line in ctx_w[:600].split('\n'):
        print(f"    {line}")

    # 保存详细结果
    out_dir  = os.path.join(BASE_DIR, 'output')
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, 'eval_wiki_results.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  详细结果: {out_path}")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="当前项目 wiki/ 知识库评估")
    parser.add_argument("--llm",   action="store_true", help="启用 LLM 检索质量评估")
    parser.add_argument("--limit", type=int, default=0, help="LLM 题目数（0=全部 10 题）")
    args = parser.parse_args()

    # 检查 wiki 输出目录
    if not os.path.isdir(ENTITIES_DIR):
        print(f"[ERROR] wiki entities 目录不存在: {ENTITIES_DIR}")
        print("  请先运行 wiki_compiler.py 编译知识库。")
        sys.exit(1)

    # 加载所有 entity 文件
    entities = {}
    for fname in sorted(os.listdir(ENTITIES_DIR)):
        if fname.endswith('.md'):
            fpath = os.path.join(ENTITIES_DIR, fname)
            entities.update(parse_entities(fpath))

    if not os.path.exists(CONCEPTS_PATH):
        print(f"[ERROR] concepts 文件不存在: {CONCEPTS_PATH}")
        print("  请先运行 wiki_compiler.py 编译知识库。")
        sys.exit(1)

    concepts   = parse_concepts(CONCEPTS_PATH)
    all_tables = load_all_tables()

    if not entities:
        print("[ERROR] entities 解析结果为空，wiki/entities/ 目录中无有效数据。")
        print("  请先运行 batch_convert 生成缓存，再运行 wiki_compiler.py 编译知识库。")
        sys.exit(1)

    evaluate_static(entities, concepts, all_tables)

    if args.llm:
        evaluate_llm(entities, concepts, all_tables, limit=args.limit)
    else:
        print("\n  [提示] 用 --llm 启用 LLM 检索质量评估（维度 5）")

    print()


if __name__ == "__main__":
    main()
