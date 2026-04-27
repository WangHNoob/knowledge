# -*- coding: utf-8 -*-
"""
Wiki 公有知识编译器 -- 从解析缓存中自动提取 entity/concept 交叉引用。

职责：
1. 自动扫描 gamedata/ 获取全部表名集合
2. 自动生成/更新 table_registry.json + CN-EN 映射
3. 扫描 .cache/*.md，提取每个表名出现的位置 (entity)，支持中文关键词匹配
4. 扫描 .cache/*.md，提取章节标题的跨文档关联 (concept)
5. 运行 lint 检查（未收录表名、孤立文档等）
6. 生成 knowledge/wiki/ 下的索引文件

Usage:
    from wiki_compiler import compile_wiki
    stats = compile_wiki(cache_dir, knowledge_dir)
"""

import os
import re
import json

# 加载 .env 环境变量（LLM API Key 等）
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import sys
import hashlib
import argparse
import webbrowser
import statistics
from collections import defaultdict
from datetime import date

try:
    import networkx as nx
    from networkx.algorithms import community as nx_community
    HAS_NETWORKX = True
except ImportError:
    HAS_NETWORKX = False

# 超过此大小的缓存文件跳过 entity/lint 详细扫描（避免 1MB+ xlsx 卡死）
MAX_SCAN_SIZE = 200 * 1024  # 200KB

# Graph node/edge colors
TYPE_COLORS = {
    "doc":     "#4CAF50",   # 源文档
    "entity":  "#2196F3",   # 表名
    "concept": "#FF9800",   # 跨文档概念
    "group":   "#9C27B0",   # 表分组
}

EDGE_COLORS = {
    "EXTRACTED": "#555555",
    "INFERRED":  "#FF5722",
    "AMBIGUOUS": "#BDBDBD",
}

COMMUNITY_COLORS = [
    "#E91E63", "#00BCD4", "#8BC34A", "#FF5722", "#673AB7",
    "#FFC107", "#009688", "#F44336", "#3F51B5", "#CDDC39",
]


# ==============================================================
# 表名词表加载
# ==============================================================

def _scan_xlsx_tables(gamedata_dir):
    """扫描 gamedata/ 目录，从 xlsx 文件名自动提取全部表名。

    递归扫描所有 .xlsx 文件，文件名（去后缀）即为表名。
    无需手动维护 skill.md 或其他配置文件。

    Args:
        gamedata_dir: knowledge/gamedata/ 目录路径

    Returns:
        set[str]: 全部表名集合，例如 {"Ability", "Actor", "config/MetaConfig"}
    """
    tables = set()
    if not os.path.isdir(gamedata_dir):
        return tables

    for root, _dirs, files in os.walk(gamedata_dir):
        for fname in files:
            if fname.lower().endswith('.xlsx') and not fname.startswith('~'):
                stem = fname[:-5]  # 去掉 .xlsx
                # 计算相对于 gamedata_dir 的路径作为表名
                rel = os.path.relpath(root, gamedata_dir)
                if rel == '.':
                    tables.add(stem)
                else:
                    # 子目录的表用 正斜杠 连接: "config/LevelConfig"
                    tables.add(rel.replace(os.sep, '/') + '/' + stem)

    return tables


# ==============================================================
# CN-EN 中英映射（文件夹提取 + LLM 翻译 + hash 懒更新）
# ==============================================================

def _extract_table_groups(knowledge_dir, all_tables=None):
    """从文件系统自动构建 table_registry.json，提取文件夹/前缀分组。

    优先使用已有的 knowledge/table_registry.json（避免不必要的重生成）；
    如果不存在或文件列表不一致，则从 all_tables 自动重建。

    Returns:
        tuple: (groups, registry_hash)
            groups: {folder_or_prefix: [table_name, ...]}
            registry_hash: str, sha256 of sorted table names
    """
    registry_path = os.path.join(knowledge_dir, 'table_registry.json')

    # 尝试从已有 registry 加载
    registry = {}
    if os.path.exists(registry_path):
        try:
            with open(registry_path, 'r', encoding='utf-8') as f:
                registry = json.load(f)
        except Exception:
            registry = {}

    # 检查是否需要重建：文件列表不一致
    if all_tables and set(registry.keys()) != all_tables:
        # 自动重建 registry
        gamedata_dir = os.path.join(knowledge_dir, 'gamedata')
        registry = _build_registry_from_fs(gamedata_dir, all_tables)
        os.makedirs(knowledge_dir, exist_ok=True)
        with open(registry_path, 'w', encoding='utf-8') as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
        print(f"  [i] Wiki: auto-generated {registry_path} ({len(registry)} tables)")

    if not registry:
        return {}, ''

    # 计算 hash
    sorted_names = sorted(registry.keys())
    registry_hash = hashlib.sha256('|'.join(sorted_names).encode()).hexdigest()[:16]

    # 按文件夹分组
    folder_groups = defaultdict(list)  # folder -> [table_names]
    root_tables = []  # 无文件夹的散表

    for table_name, rel_path in registry.items():
        # 路径中有 / 表示有文件夹
        parts = rel_path.replace('\\', '/').split('/')
        if len(parts) > 1:
            folder = parts[0]
            folder_groups[folder].append(table_name)
        else:
            root_tables.append(table_name)

    # 散表按前缀聚合（提取首个大写词作为前缀）
    prefix_groups = defaultdict(list)
    for table in root_tables:
        # PascalCase 拆分: HeroLevel -> Hero, EquipRefine -> Equip
        parts = re.findall(r'[A-Z_][a-z0-9]*', table)
        prefix = parts[0] if parts else table
        # 跳过太短的前缀（单字符无意义）
        if len(prefix) >= 2:
            prefix_groups[prefix].append(table)

    # 合并: 文件夹组 + 前缀组（只保留有 2+ 张表的前缀）
    groups = dict(folder_groups)
    for prefix, tables in prefix_groups.items():
        if len(tables) >= 2 and prefix not in groups:
            groups[prefix] = tables

    return groups, registry_hash


def _build_registry_from_fs(gamedata_dir, all_tables):
    """从文件系统构建 table registry。

    Args:
        gamedata_dir: knowledge/gamedata/ 目录
        all_tables: 全部表名集合

    Returns:
        dict: {table_name: relative_path}，例如 {"Ability": "Ability.xlsx"}
    """
    registry = {}
    if not os.path.isdir(gamedata_dir):
        return registry

    for root, _dirs, files in os.walk(gamedata_dir):
        for fname in files:
            if fname.lower().endswith('.xlsx') and not fname.startswith('~'):
                stem = fname[:-5]
                rel_path = os.path.relpath(root, gamedata_dir)
                if rel_path == '.':
                    registry[stem] = fname
                else:
                    full_rel = os.path.join(rel_path, fname).replace(os.sep, '/')
                    registry[rel_path.replace(os.sep, '/') + '/' + stem] = full_rel

    return registry


def _build_cn_en_map(wiki_dir, groups, registry_hash, force=False):
    """读取中英映射，检测新增 group 并自动触发翻译脚本。

    映射文件 cn_en_map.json 由 IDE AI 生成/维护，不依赖外部 API。
    当 registry hash 变化或文件缺失时，自动调用 build_cn_en_map.py
    输出结构化翻译指令，让 IDE AI 补全。

    Returns:
        dict: {chinese_keyword: folder_or_prefix}  e.g. {'神秘商店': 'MysteryShop'}
    """
    cache_path = os.path.join(wiki_dir, 'cn_en_map.json')

    # hash 一致且不强制刷新 -> 直接用缓存
    if os.path.exists(cache_path) and not force:
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            if cached.get('registry_hash') == registry_hash:
                mapping = cached.get('mapping', {})
                print(f"  [i] Wiki: loaded {len(mapping)} CN-EN terms from cache")
                return mapping
        except Exception:
            pass

    if not groups:
        return {}

    # hash 不一致或无缓存 -> 尝试读旧缓存
    mapping = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            mapping = cached.get('mapping', {})
            translated_en = set(mapping.values())
            new_groups = [g for g in sorted(groups.keys()) if g not in translated_en]
            if not new_groups:
                print(f"  [i] Wiki: loaded {len(mapping)} CN-EN terms (hash stale but complete)")
                return mapping
        except Exception:
            pass

    # 缺失或不完整 -> 调用 build_cn_en_map.py 输出翻译指令
    _trigger_cn_en_build()
    return mapping


def _trigger_cn_en_build():
    """调用 build_cn_en_map.py 输出翻译指令供 IDE AI 执行。

    查找顺序：项目根目录 > 当前脚本目录。
    """
    import subprocess
    search_dirs = [
        os.path.dirname(os.path.abspath(__file__)),  # 项目根
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cli'),  # cli/ 子目录
    ]
    for search_dir in search_dirs:
        build_script = os.path.join(search_dir, 'build_cn_en_map.py')
        if os.path.exists(build_script):
            subprocess.run([sys.executable, build_script], cwd=os.path.dirname(build_script))
            return
    print(f"  [WARN] build_cn_en_map.py not found (searched: {search_dirs})")



# ==============================================================
# Entity 扫描
# ==============================================================

def _parse_sections(text):
    """将 markdown 文本拆分为 (章节标题, 章节内容) 列表。

    支持 #, ##, ### 级别标题。

    Returns:
        list[tuple[str, str]]: [(section_title, section_content), ...]
    """
    sections = []
    current_title = '(top)'
    current_lines = []

    for line in text.split('\n'):
        if line.startswith('#') and ' ' in line and not line.startswith('####'):
            # 保存上一章节
            if current_lines:
                sections.append((current_title, '\n'.join(current_lines)))
            current_title = line.lstrip('#').strip()
            current_lines = []
        else:
            current_lines.append(line)

    # 最后一个章节
    if current_lines:
        sections.append((current_title, '\n'.join(current_lines)))

    return sections


def _scan_entity_refs(cache_dir, known_tables, cn_groups=None):
    """扫描缓存文件，返回表名->位置映射。

    用单次扫描提取所有英文词，和已知表名集做交集。
    如果有 cn_groups，同时扫描中文关键词并关联到该组下所有表名。

    Args:
        cn_groups: {chinese_keyword: [table_name, ...]}

    Returns:
        dict: {table_name: [(filename, section_title), ...]}
    """
    entity_refs = defaultdict(list)

    if not os.path.isdir(cache_dir):
        return entity_refs

    # 预编译: 提取所有 英文/下划线 开头的词（表名一般是 PascalCase 或 _前缀）
    word_pattern = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\b')
    # 中文字符检测（用于快速跳过纯 ASCII 章节）
    has_cjk = re.compile(r'[\u4e00-\u9fff]')

    for fname in sorted(os.listdir(cache_dir)):
        if not fname.endswith('.md'):
            continue

        fpath = os.path.join(cache_dir, fname)

        # 跳过超大文件的详细扫描
        if os.path.getsize(fpath) > MAX_SCAN_SIZE:
            continue

        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                text = f.read()
        except Exception:
            continue

        # 去掉 .md 后缀得到原始文件名
        doc_name = fname[:-3] if fname.endswith('.md') else fname

        sections = _parse_sections(text)
        for section_title, section_content in sections:
            # Pass 1: 英文表名集合交集
            words_in_section = set(word_pattern.findall(section_content))
            matched_tables = words_in_section & known_tables
            for table in matched_tables:
                ref = (doc_name, section_title)
                if ref not in entity_refs[table]:
                    entity_refs[table].append(ref)

            # Pass 2: 中文关键词匹配（跳过无中文字符的章节）
            if cn_groups:
                combined = section_title + section_content
                if not has_cjk.search(combined):
                    continue
                for cn_word, table_list in cn_groups.items():
                    if cn_word in combined:
                        for table in table_list:
                            ref = (doc_name, section_title)
                            if ref not in entity_refs[table]:
                                entity_refs[table].append(ref)

    return dict(entity_refs)


# ==============================================================
# Concept 扫描
# ==============================================================

def _scan_concept_refs(cache_dir):
    """扫描缓存文件，提取章节标题并建立跨文档关联。

    同名章节标题出现在多个文档中 = 同一个概念。

    Returns:
        dict: {concept_name: [doc_name, ...]}
    """
    concept_docs = defaultdict(list)

    if not os.path.isdir(cache_dir):
        return concept_docs

    # 要过滤的通用标题（无领域意义）
    skip_titles = {
        '(top)', '', 'Sheet1', 'Sheet2', 'Sheet3',
        'Sheet4', 'Sheet5',
    }

    for fname in sorted(os.listdir(cache_dir)):
        if not fname.endswith('.md'):
            continue

        fpath = os.path.join(cache_dir, fname)
        doc_name = fname[:-3] if fname.endswith('.md') else fname

        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('# ') or line.startswith('## '):
                        title = line.lstrip('#').strip()
                        if title and title not in skip_titles:
                            if doc_name not in concept_docs[title]:
                                concept_docs[title].append(doc_name)
        except Exception:
            continue

    # 只保留在 2+ 个文档中出现的概念（跨文档关联才有价值）
    cross_doc = {
        concept: docs
        for concept, docs in concept_docs.items()
        if len(docs) >= 2
    }

    return cross_doc


# ==============================================================
# Lint
# ==============================================================

def _run_lint(entity_refs, concept_refs, cache_dir, known_tables):
    """运行知识健康检查。

    concept_refs 由 _scan_concept_refs() 提供（已扫描过跨文档概念），
    本函数直接复用，不再重复扫描。

    Returns:
        dict: {
            'unknown_tables': [(name, docs), ...],
            'orphan_docs': [doc_name, ...],
            'single_source_concepts': int,
        }
    """
    report = {
        'unknown_tables': [],
        'orphan_docs': [],
        'single_source_concepts': 0,
    }

    if not os.path.isdir(cache_dir):
        return report

    # 1. 扫描缓存文件中的 PascalCase 词，找出不在 known_tables 中的
    unknown_counts = defaultdict(set)  # {name: set(doc_names)}
    pascal_pattern = re.compile(r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b')

    # 已引用的文档集合（entity 索引中出现过的）
    referenced_docs = set()
    for refs in entity_refs.values():
        for doc_name, _ in refs:
            referenced_docs.add(doc_name)

    # 也加入 concept 引用的文档
    for docs in concept_refs.values():
        for doc_name in docs:
            referenced_docs.add(doc_name)

    for fname in sorted(os.listdir(cache_dir)):
        if not fname.endswith('.md'):
            continue

        fpath = os.path.join(cache_dir, fname)
        doc_name = fname[:-3] if fname.endswith('.md') else fname

        # 跳过超大文件的 PascalCase 扫描
        if os.path.getsize(fpath) > MAX_SCAN_SIZE:
            if doc_name not in referenced_docs:
                report['orphan_docs'].append(doc_name)
            continue

        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                text = f.read()
        except Exception:
            continue

        # 检测未知 PascalCase 表名
        for match in pascal_pattern.finditer(text):
            name = match.group(1)
            if name not in known_tables and len(name) > 4:
                unknown_counts[name].add(doc_name)

        # 2. 孤立文档检测
        if doc_name not in referenced_docs:
            report['orphan_docs'].append(doc_name)

    # 只报告在 2+ 个文档中出现的未知表名（过滤噪音）
    report['unknown_tables'] = [
        (name, sorted(docs))
        for name, docs in sorted(unknown_counts.items())
        if len(docs) >= 2
    ]

    # 3. 利用已有的 concept_refs 计算单源概念数
    # concept_refs 只包含 2+ docs 的概念；总概念数需要扫描文件标题
    # 但为避免重复扫描，这里只统计 concept_refs 中的跨文档概念数作为参考
    report['cross_doc_concepts'] = len(concept_refs)

    return report


# ==============================================================
# 表结构索引（从 SQLite 索引 + openpyxl 读取列名）
# ==============================================================

def _build_table_schemas(knowledge_dir):
    """从 SQLite 索引和 xlsx 文件读取所有表的列名。

    优先从 knowledge/index.db 中已索引的大表读取列名（PRAGMA table_info），
    未索引的小表 fallback 到 openpyxl 读取 xlsx 前2行。

    Returns:
        dict: {table_name: [col1, col2, ...]}
    """
    import sqlite3

    schemas = {}

    gamedata_dir = os.path.join(knowledge_dir, 'gamedata')
    db_path = os.path.join(knowledge_dir, 'index.db')

    # 从 table_registry.json 获取全量表名→路径映射
    registry_path = os.path.join(knowledge_dir, 'table_registry.json')
    registry = {}
    if os.path.exists(registry_path):
        try:
            with open(registry_path, 'r', encoding='utf-8') as f:
                registry = json.load(f)
        except Exception:
            pass

    def _has_cjk(s):
        return bool(re.search(r'[\u4e00-\u9fff]', str(s)))

    def _clean(name):
        return re.sub(r'[^a-zA-Z0-9_]', '_', name)

    # 1. 从 SQLite 索引中读取已索引的大表列名
    if db_path and os.path.exists(db_path):
        try:
            conn = sqlite3.connect(db_path, timeout=3)
            existing_tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}

            for tbl_name in registry:
                clean = _clean(tbl_name)
                if clean in existing_tables:
                    cols = [r[1] for r in conn.execute(
                        f'PRAGMA table_info([{clean}])'
                    ).fetchall()]
                    # 过滤占位列
                    cols = [c for c in cols if c and c not in ('_rowid',) and not c.startswith('_col')]
                    if cols:
                        # 优先保留中文列名（对游戏策划更直观）
                        cn_cols = [c for c in cols if _has_cjk(c)]
                        schemas[tbl_name] = cn_cols if cn_cols else cols
            conn.close()
        except Exception:
            pass

    # 2. Fallback：用 openpyxl 读小表的列名
    try:
        import openpyxl
        has_openpyxl = True
    except ImportError:
        has_openpyxl = False

    if has_openpyxl and registry:
        for tbl_name, rel_path in sorted(registry.items()):
            if tbl_name in schemas:
                continue  # 已从 SQLite 获取
            xlsx_path = os.path.join(gamedata_dir, rel_path)
            if not os.path.exists(xlsx_path):
                continue
            try:
                wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
                ws = wb.active
                rows = list(ws.iter_rows(min_row=2, max_row=2, values_only=True))
                wb.close()
                if rows:
                    cols = [str(c).strip() for c in rows[0] if c is not None and str(c).strip()]
                    cn_cols = [c for c in cols if _has_cjk(c)]
                    if cn_cols:
                        schemas[tbl_name] = cn_cols
                    elif cols:
                        schemas[tbl_name] = cols
            except Exception:
                continue

    return schemas


def _write_table_schema_index(wiki_dir, schemas):
    """生成 wiki/table_schema.md — 每张表的字段速查表。"""
    today = date.today().isoformat()
    lines = [
        '# Table Schema Index',
        f'> Auto-compiled from gamedata. Last updated: {today}',
        f'> Total: {len(schemas)} tables.',
        '',
    ]

    for table_name in sorted(schemas.keys()):
        headers = schemas[table_name]
        col_str = ' | '.join(headers[:20])
        if len(headers) > 20:
            col_str += f' ... (+{len(headers)-20})'
        lines.append(f'- **{table_name}**: {col_str}')

    path = os.path.join(wiki_dir, 'table_schema.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path


# ==============================================================
# 文件生成
# ==============================================================

def _write_entity_index(wiki_dir, entity_refs):
    """生成 wiki/entities.md — 所有实体关系保存在单个文件中。"""
    lines = ['# Entity Cross-Reference', '']
    lines.append('> Auto-compiled from gamedocs cache.')
    lines.append('')

    # 按引用文档数降序排列
    sorted_entities = sorted(
        entity_refs.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    for table, refs in sorted_entities:
        lines.append(f'## {table} ({len(refs)} refs)')
        # 按文档分组
        doc_sections = defaultdict(list)
        for doc_name, section in refs:
            doc_sections[doc_name].append(section)

        for doc_name, sections in sorted(doc_sections.items()):
            section_str = ', '.join(s for s in sections if s != '(top)')
            if section_str:
                lines.append(f'- {doc_name} -> {section_str}')
            else:
                lines.append(f'- {doc_name}')

        lines.append('')

    path = os.path.join(wiki_dir, 'entities.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path


def _write_concept_index(wiki_dir, concept_refs):
    """生成 wiki/concepts.md。"""
    today = date.today().isoformat()
    lines = [
        '---',
        'title: "Concept Cross-Reference"',
        'type: concept-index',
        'tags: [concept]',
        f'last_updated: {today}',
        '---',
        '',
        '# Concept Cross-Reference',
        '',
        '> Auto-compiled. Only concepts appearing in 2+ documents.',
        '',
    ]

    # 按文档数降序排列
    sorted_concepts = sorted(
        concept_refs.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )

    for concept, docs in sorted_concepts:
        lines.append(f'## {concept} ({len(docs)} docs)')
        for doc in docs:
            lines.append(f'- {doc}')
        lines.append('')

    path = os.path.join(wiki_dir, 'concepts.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path


def _write_wiki_index(wiki_dir, entity_refs, concept_refs):
    """生成 wiki/index.md（Agent 消费入口）。"""
    today = date.today().isoformat()
    lines = [
        '---',
        'title: "Wiki Knowledge Index"',
        'type: index',
        f'last_updated: {today}',
        '---',
        '',
        '# Wiki Knowledge Index',
        f'> Auto-compiled. Last updated: {today}',
        '',
    ]

    # 统计
    all_docs = set()
    for refs in entity_refs.values():
        for doc_name, _ in refs:
            all_docs.add(doc_name)
    for docs in concept_refs.values():
        for doc in docs:
            all_docs.add(doc)

    lines.append(f'## Stats')
    lines.append(f'- {len(all_docs)} documents')
    lines.append(f'- {len(entity_refs)} entities (table names)')
    lines.append(f'- {len(concept_refs)} cross-doc concepts')
    lines.append('')

    # Top entities
    lines.append('## Top Entities')
    top_entities = sorted(
        entity_refs.items(),
        key=lambda x: len(x[1]),
        reverse=True
    )[:20]
    for table, refs in top_entities:
        doc_names = sorted(set(d for d, _ in refs))
        doc_str = ', '.join(doc_names[:5])
        if len(doc_names) > 5:
            doc_str += f' (+{len(doc_names)-5})'
        lines.append(f'- {table}: {doc_str}')
    lines.append('')

    # Top concepts
    if concept_refs:
        lines.append('## Top Concepts')
        top_concepts = sorted(
            concept_refs.items(),
            key=lambda x: len(x[1]),
            reverse=True
        )[:15]
        for concept, docs in top_concepts:
            doc_str = ', '.join(docs[:5])
            if len(docs) > 5:
                doc_str += f' (+{len(docs)-5})'
            lines.append(f'- {concept}: {doc_str}')
        lines.append('')

    path = os.path.join(wiki_dir, 'index.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path


def _write_lint_report(wiki_dir, lint_result):
    """生成 wiki/lint_report.md。"""
    today = date.today().isoformat()
    lines = [
        '---',
        'title: "Wiki Lint Report"',
        'type: lint-report',
        f'last_updated: {today}',
        '---',
        '',
        '# Wiki Lint Report',
        f'> Generated: {today}',
        '',
    ]

    # 未知表名
    unknown = lint_result.get('unknown_tables', [])
    if unknown:
        lines.append(f'## Unknown Table Names ({len(unknown)})')
        lines.append('> PascalCase words in 2+ docs, not in gamedata/')
        lines.append('')
        for name, docs in unknown[:30]:
            lines.append(f'- {name} (in {len(docs)} docs): {", ".join(docs[:3])}')
        lines.append('')

    # 孤立文档
    orphans = lint_result.get('orphan_docs', [])
    if orphans:
        lines.append(f'## Orphan Documents ({len(orphans)})')
        lines.append('> No known entity references found')
        lines.append('')
        for doc in orphans:
            lines.append(f'- {doc}')
        lines.append('')

    # 跨文档概念
    cross = lint_result.get('cross_doc_concepts', 0)
    lines.append(f'## Cross-Doc Concepts: {cross}')
    lines.append('> Section titles appearing in 2+ documents')
    lines.append('')

    if not unknown and not orphans:
        lines.append('[OK] No issues found.')

    path = os.path.join(wiki_dir, 'lint_report.md')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path


# ==============================================================
# Knowledge Graph
# ==============================================================

def _build_graph_nodes(entity_refs, concept_refs, groups, cache_dir):
    """从现有数据构建图谱节点。

    节点类型:
    - doc:     源文档(.cache/*.md)
    - entity:  表名
    - concept: 跨文档概念
    - group:   表分组(文件夹/前缀)
    """
    nodes = []
    seen_ids = set()

    # Doc nodes
    if os.path.isdir(cache_dir):
        for fname in sorted(os.listdir(cache_dir)):
            if not fname.endswith('.md'):
                continue
            doc_name = fname[:-3]
            node_id = f"doc:{doc_name}"
            if node_id not in seen_ids:
                seen_ids.add(node_id)
                nodes.append({
                    "id": node_id,
                    "label": doc_name,
                    "type": "doc",
                    "color": TYPE_COLORS["doc"],
                })

    # Entity nodes
    for table in sorted(entity_refs.keys()):
        node_id = f"entity:{table}"
        if node_id not in seen_ids:
            seen_ids.add(node_id)
            nodes.append({
                "id": node_id,
                "label": table,
                "type": "entity",
                "color": TYPE_COLORS["entity"],
            })

    # Concept nodes
    for concept in sorted(concept_refs.keys()):
        node_id = f"concept:{concept}"
        if node_id not in seen_ids:
            seen_ids.add(node_id)
            nodes.append({
                "id": node_id,
                "label": concept,
                "type": "concept",
                "color": TYPE_COLORS["concept"],
            })

    # Group nodes
    for group_name in sorted(groups.keys()):
        node_id = f"group:{group_name}"
        if node_id not in seen_ids:
            seen_ids.add(node_id)
            nodes.append({
                "id": node_id,
                "label": group_name,
                "type": "group",
                "color": TYPE_COLORS["group"],
            })

    return nodes


def _build_graph_edges(entity_refs, concept_refs, groups):
    """Pass 1: 从现有数据构建确定性边。

    边类型:
    - doc → entity (REFERENCES):      文档引用了某表
    - doc → concept (SHARES_CONCEPT): 文档包含某跨文档概念
    - entity → group (BELONGS_TO):    表属于某分组
    """
    edges = []
    seen = set()

    # doc → entity
    for table, refs in entity_refs.items():
        for ref in refs:
            doc_name = ref[0]
            src = f"doc:{doc_name}"
            tgt = f"entity:{table}"
            key = (src, tgt)
            if key not in seen:
                seen.add(key)
                edges.append({
                    "id": f"{src}->{tgt}:EXTRACTED",
                    "from": src,
                    "to": tgt,
                    "type": "EXTRACTED",
                    "relationship": "REFERENCES",
                    "color": EDGE_COLORS["EXTRACTED"],
                    "confidence": 1.0,
                })

    # doc → concept
    for concept, docs in concept_refs.items():
        for doc_name in docs:
            src = f"doc:{doc_name}"
            tgt = f"concept:{concept}"
            key = (src, tgt)
            if key not in seen:
                seen.add(key)
                edges.append({
                    "id": f"{src}->{tgt}:EXTRACTED",
                    "from": src,
                    "to": tgt,
                    "type": "EXTRACTED",
                    "relationship": "SHARES_CONCEPT",
                    "color": EDGE_COLORS["EXTRACTED"],
                    "confidence": 1.0,
                })

    # entity → group
    for group_name, tables in groups.items():
        for table in tables:
            src = f"entity:{table}"
            tgt = f"group:{group_name}"
            key = (src, tgt)
            if key not in seen:
                seen.add(key)
                edges.append({
                    "id": f"{src}->{tgt}:EXTRACTED",
                    "from": src,
                    "to": tgt,
                    "type": "EXTRACTED",
                    "relationship": "BELONGS_TO",
                    "color": EDGE_COLORS["EXTRACTED"],
                    "confidence": 1.0,
                })

    return edges


def _deduplicate_edges(edges):
    """合并重复和双向边，保留置信度最高者。"""
    best = {}
    for e in edges:
        a, b = e["from"], e["to"]
        key = (min(a, b), max(a, b))
        existing = best.get(key)
        if not existing or e.get("confidence", 0) > existing.get("confidence", 0):
            best[key] = e

    deduped = []
    for edge in best.values():
        rel_type = edge.get("type", "EXTRACTED")
        edge.setdefault("id", f"{edge['from']}->{edge['to']}:{rel_type}")
        edge.setdefault("color", EDGE_COLORS.get(rel_type, EDGE_COLORS["EXTRACTED"]))
        edge.setdefault("confidence", 1.0 if rel_type == "EXTRACTED" else 0.7)
        edge.setdefault("relationship", "")
        deduped.append(edge)
    return deduped


def _detect_communities(nodes, edges):
    """Louvain 社区检测。"""
    if not HAS_NETWORKX:
        return {}

    G = nx.Graph()
    for n in nodes:
        G.add_node(n["id"])
    for e in edges:
        G.add_edge(e["from"], e["to"])

    if G.number_of_edges() == 0:
        return {}

    try:
        communities = nx_community.louvain_communities(G, seed=42)
        node_to_community = {}
        for i, comm in enumerate(communities):
            for node_id in comm:
                node_to_community[node_id] = i
        return node_to_community
    except Exception:
        return {}


def _call_llm(prompt, max_tokens=4096):
    """调用 LLM 进行语义推断。"""
    try:
        from litellm import completion
    except ImportError:
        print("  [ERROR] litellm not installed. Run: pip install litellm")
        return ""

    model = os.getenv("LLM_MODEL_FAST", "claude-3-5-haiku-latest")

    try:
        response = completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"  [ERROR] LLM call failed: {e}")
        return ""


def _build_inferred_edges(nodes, existing_edges, cache_dir, graph_dir):
    """Pass 2: LLM 语义推断隐式关系。

    对每个 doc 节点，调用 LLM 分析其内容与知识库中其他节点的隐式关系。
    支持断点续传（.inferred_edges.jsonl）和内容哈希缓存（.cache.json）。
    """
    # Check LLM availability
    try:
        from litellm import completion  # noqa: F401
    except ImportError:
        print("  [WARN] litellm not installed, skipping inference pass")
        return []

    os.makedirs(graph_dir, exist_ok=True)
    checkpoint_path = os.path.join(graph_dir, '.inferred_edges.jsonl')
    cache_path = os.path.join(graph_dir, '.cache.json')

    # Load cache
    cache = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except Exception:
            pass

    # Load checkpoint
    completed_ids = set()
    checkpoint_edges = []
    if os.path.exists(checkpoint_path):
        for line in open(checkpoint_path, 'r', encoding='utf-8').readlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                completed_ids.add(record["page_id"])
                for edge in record.get("edges", []):
                    checkpoint_edges.append(edge)
            except Exception:
                continue

    # Build context for prompts
    node_list = "\n".join(f"- {n['id']} ({n['type']})" for n in nodes)
    node_ids = {n['id'] for n in nodes}
    existing_summary = "\n".join(
        f"- {e['from']} -> {e['to']} ({e.get('relationship', 'EXTRACTED')})"
        for e in existing_edges[:50]
    )

    # Process doc nodes that haven't been inferred or content changed
    doc_nodes = [n for n in nodes if n['type'] == 'doc']
    changed_nodes = []

    for node in doc_nodes:
        doc_name = node['label']
        fpath = os.path.join(cache_dir, f"{doc_name}.md")
        if not os.path.exists(fpath):
            continue

        content_hash = ''
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        except Exception:
            continue

        if node['id'] in completed_ids:
            cached = cache.get(node['id'], {})
            if cached.get('hash') == content_hash:
                # Content unchanged, use cached edges
                for edge in cached.get('edges', []):
                    rel_type = edge.get("type", "INFERRED")
                    confidence = float(edge.get("confidence", 0.7))
                    checkpoint_edges.append({
                        "id": f"{node['id']}->{edge['to']}:{rel_type}",
                        "from": node['id'],
                        "to": edge["to"],
                        "type": rel_type,
                        "relationship": edge.get("relationship", ""),
                        "color": EDGE_COLORS.get(rel_type, EDGE_COLORS["INFERRED"]),
                        "confidence": confidence,
                    })
                continue

        changed_nodes.append((node, fpath, content, content_hash))

    if not changed_nodes:
        print(f"  [i] Graph inference: all {len(doc_nodes)} doc nodes up-to-date")
        return checkpoint_edges

    print(f"  [i] Graph inference: {len(changed_nodes)} doc nodes to infer...")

    new_edges = list(checkpoint_edges)

    for i, (node, fpath, content, content_hash) in enumerate(changed_nodes, 1):
        # Skip oversized files
        if os.path.getsize(fpath) > MAX_SCAN_SIZE:
            print(f"    [{i}/{len(changed_nodes)}] {node['label']}: skipped (too large)")
            continue

        content_preview = content[:2000]

        prompt = f"""分析这篇游戏策划文档，识别其与知识库中其他节点的隐式语义关系。

文档: {node['id']}
内容:
{content_preview}

知识库中的所有节点:
{node_list}

已有确定性边:
{existing_summary}

返回严格 JSON 格式:
{{
  "edges": [
    {{"to": "节点id", "relationship": "一句话描述关系", "confidence": 0.0-1.0, "type": "INFERRED 或 AMBIGUOUS"}}
  ]
}}

规则:
- 只包含上述节点列表中存在的节点
- confidence >= 0.7 -> INFERRED, < 0.7 -> AMBIGUOUS
- 不要重复已有边
- 重点关注：表间依赖、文档间设计约束、跨系统影响
- 没有新关系则返回 {{"edges": []}}
- 只返回 JSON，不要其他文字"""

        print(f"    [{i}/{len(changed_nodes)}] {node['label']}: ", end="", flush=True)

        try:
            raw = _call_llm(prompt, max_tokens=1024)
            raw = raw.strip()

            # Extract JSON
            match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", raw)
            if match:
                raw = match.group(0)
            else:
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)

            inferred = json.loads(raw)
            if isinstance(inferred, dict):
                edges_list = inferred.get("edges", [])
            elif isinstance(inferred, list):
                edges_list = inferred
            else:
                edges_list = []

            page_edges = []
            valid_rels = []

            for rel in edges_list:
                if isinstance(rel, dict) and "to" in rel:
                    if rel["to"] not in node_ids:
                        continue
                    confidence = float(rel.get("confidence", 0.7))
                    rel_type = rel.get("type") or ("INFERRED" if confidence >= 0.7 else "AMBIGUOUS")
                    edge = {
                        "id": f"{node['id']}->{rel['to']}:{rel_type}",
                        "from": node['id'],
                        "to": rel['to'],
                        "type": rel_type,
                        "relationship": rel.get("relationship", ""),
                        "color": EDGE_COLORS.get(rel_type, EDGE_COLORS["INFERRED"]),
                        "confidence": confidence,
                    }
                    page_edges.append(edge)
                    new_edges.append(edge)
                    valid_rels.append({
                        "to": rel["to"],
                        "relationship": rel.get("relationship", ""),
                        "confidence": confidence,
                        "type": rel_type,
                    })

            # Update cache
            cache[node['id']] = {"hash": content_hash, "edges": valid_rels}

            # Append checkpoint
            record = {"page_id": node['id'], "edges": page_edges, "ts": date.today().isoformat()}
            with open(checkpoint_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            print(f"found {len(page_edges)} edges")

        except (json.JSONDecodeError, TypeError, ValueError) as e:
            print(f"JSON parse error: {str(e)[:60]}")
        except Exception as e:
            print(f"error: {str(e)[:80]}")

    # Save cache
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

    return new_edges


def _generate_graph_report(nodes, edges, communities, entity_refs, groups):
    """生成图谱健康报告，针对游戏策划场景的特殊检查。"""
    today = date.today().isoformat()
    n_nodes = len(nodes)
    n_edges = len(edges)

    if n_nodes == 0:
        return f"# Graph Health Report — {today}\n\nNo graph data available.\n"

    # Build NetworkX graph
    G = nx.Graph() if HAS_NETWORKX else None
    if G:
        for n in nodes:
            G.add_node(n["id"])
        for e in edges:
            G.add_edge(e["from"], e["to"])

    # Metrics
    degrees = dict(G.degree()) if G else {}
    edges_per_node = n_edges / n_nodes if n_nodes else 0

    # Health rating
    if edges_per_node >= 2.0:
        health = "✅ healthy"
    elif edges_per_node >= 1.0:
        health = "⚠️ warning"
    else:
        health = "🔴 critical"

    # Orphan nodes (degree == 0)
    orphans = sorted([n["id"] for n in nodes if degrees.get(n["id"], 0) == 0])
    orphan_count = len(orphans)

    # God nodes (degree > mean + 2*std)
    deg_values = list(degrees.values()) if degrees else [0]
    mean_deg = statistics.mean(deg_values) if deg_values else 0
    std_deg = statistics.stdev(deg_values) if len(deg_values) > 1 else 0
    god_threshold = mean_deg + 2 * std_deg
    god_nodes = sorted(
        [(n["id"], degrees.get(n["id"], 0)) for n in nodes if degrees.get(n["id"], 0) > god_threshold],
        key=lambda x: x[1],
        reverse=True,
    )

    # Orphan entities (tables not in any group)
    all_grouped_tables = set()
    for tables in groups.values():
        all_grouped_tables.update(tables)
    ungrouped_tables = sorted([t for t in entity_refs if t not in all_grouped_tables])

    # Community stats
    community_count = len(set(communities.values())) if communities else 0
    comm_members = {}
    for node_id, comm_id in communities.items():
        comm_members.setdefault(comm_id, []).append(node_id)

    # Fragile bridges
    cross_comm_edges = {}
    for e in edges:
        ca = communities.get(e["from"], -1)
        cb = communities.get(e["to"], -1)
        if ca >= 0 and cb >= 0 and ca != cb:
            key = (min(ca, cb), max(ca, cb))
            cross_comm_edges.setdefault(key, []).append(e)
    fragile_bridges = [
        (pair, edge_list[0])
        for pair, edge_list in sorted(cross_comm_edges.items())
        if len(edge_list) == 1
    ]

    # Build report
    lines = [
        f"# Graph Health Report — {today}",
        "",
        "## Health Summary",
        f"- **{n_nodes}** nodes, **{n_edges}** edges ({edges_per_node:.2f} edges/node — {health})",
        f"- **{orphan_count}** orphan nodes",
        f"- **{community_count}** communities",
        f"- **{len(ungrouped_tables)}** ungrouped tables",
        "",
    ]

    # Orphan section
    lines.append(f"## 🔴 Orphan Nodes ({orphan_count})")
    if orphans:
        lines.append("这些节点没有任何图谱连接，考虑添加引用关系：")
        for o in orphans[:30]:
            lines.append(f"- `{o}`")
        if len(orphans) > 30:
            lines.append(f"- ... 及另外 {len(orphans) - 30} 个")
    else:
        lines.append("无孤立节点")
    lines.append("")

    # God nodes
    lines.append("## 🟡 Hub Nodes (高连接节点)")
    if god_nodes:
        lines.append("这些节点连接数远超平均值 (degree > μ+2σ)：")
        lines.append("")
        lines.append("| Node | Degree | Type |")
        lines.append("|---|---|---|")
        for node_id, deg in god_nodes[:20]:
            node_type = [n["type"] for n in nodes if n["id"] == node_id]
            t = node_type[0] if node_type else "?"
            lines.append(f"| `{node_id}` | {deg} | {t} |")
    else:
        lines.append("无异常高连接节点")
    lines.append("")

    # Ungrouped tables
    lines.append(f"## 🟠 Ungrouped Tables ({len(ungrouped_tables)})")
    if ungrouped_tables:
        lines.append("这些表不属于任何已知分组：")
        for t in ungrouped_tables[:30]:
            lines.append(f"- `{t}`")
        if len(ungrouped_tables) > 30:
            lines.append(f"- ... 及另外 {len(ungrouped_tables) - 30} 张")
    else:
        lines.append("所有表均已归属分组")
    lines.append("")

    # Fragile bridges
    lines.append("## 🟡 Fragile Bridges")
    if fragile_bridges:
        lines.append("社区间仅由 1 条边连接，断开将导致知识孤岛：")
        for (ca, cb), edge in fragile_bridges:
            lines.append(f"- Community {ca} ↔ Community {cb} via `{edge['from']}` → `{edge['to']}`")
    else:
        lines.append("无脆弱桥接")
    lines.append("")

    # Community overview
    lines.append("## 🟢 Community Overview")
    if comm_members:
        lines.append("")
        lines.append("| Community | Nodes | Key Members |")
        lines.append("|---|---|---|")
        for comm_id in sorted(comm_members.keys()):
            members = comm_members[comm_id]
            members_sorted = sorted(members, key=lambda m: degrees.get(m, 0), reverse=True)
            key_members = ", ".join(members_sorted[:5])
            if len(members_sorted) > 5:
                key_members += ", ..."
            lines.append(f"| {comm_id} | {len(members)} | {key_members} |")
    else:
        lines.append("无社区信息（需安装 networkx）")
    lines.append("")

    # Suggested actions
    lines.append("## Suggested Actions")
    actions = []
    if orphans:
        actions.append(f"1. 为孤立节点添加引用关系 (最高优先级: {orphans[0]})")
    if ungrouped_tables:
        actions.append(f"{len(actions)+1}. 将未分组表归入合适分组 (共 {len(ungrouped_tables)} 张)")
    if fragile_bridges:
        actions.append(f"{len(actions)+1}. 加强脆弱桥接的跨社区引用")
    if god_nodes:
        actions.append(f"{len(actions)+1}. 审查 Hub 节点：连接多但内容薄时需补充")
    if not actions:
        actions.append("1. 图谱状态良好，保持当前引用质量")
    lines.extend(actions)
    lines.append("")

    return "\n".join(lines)


def _render_graph_html(nodes, edges):
    """生成自包含的 vis.js 可视化 HTML。"""
    nodes_json = json.dumps(nodes, indent=2, ensure_ascii=False)
    edges_json = json.dumps(edges, indent=2, ensure_ascii=False)

    legend_items = "".join(
        f'<span style="background:{color};padding:3px 8px;margin:2px;border-radius:3px;font-size:12px">{t}</span>'
        for t, color in TYPE_COLORS.items()
    )

    n_extracted = len([e for e in edges if e.get('type') == 'EXTRACTED'])
    n_inferred = len([e for e in edges if e.get('type') == 'INFERRED'])
    n_ambiguous = len([e for e in edges if e.get('type') == 'AMBIGUOUS'])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>游戏策划知识图谱</title>
<script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
<style>
  body {{ margin: 0; background: #1a1a2e; font-family: 'Inter', sans-serif; color: #eee; }}
  #graph {{ width: 100vw; height: 100vh; }}
  #controls {{
    position: fixed; top: 10px; left: 10px; background: rgba(10,10,30,0.88);
    padding: 14px; border-radius: 10px; z-index: 10; max-width: 280px;
    backdrop-filter: blur(8px); border: 1px solid rgba(255,255,255,0.08);
  }}
  #controls h3 {{ margin: 0 0 10px; font-size: 15px; letter-spacing: 0.5px; }}
  #search {{ width: 100%; padding: 6px 8px; margin-bottom: 10px; background: #222; color: #eee; border: 1px solid #444; border-radius: 6px; font-size: 13px; }}
  #controls p {{ margin: 10px 0 0; font-size: 11px; color: #9ea3b0; line-height: 1.5; }}
  .filter-group {{ margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(255,255,255,0.1); }}
  .filter-group label {{ display: block; font-size: 12px; color: #bbb; margin-bottom: 4px; }}
  .slider-row {{ display: flex; align-items: center; gap: 8px; margin-top: 4px; }}
  .slider-row input[type=range] {{ flex: 1; accent-color: #FF5722; }}
  .slider-val {{ font-size: 12px; color: #FF5722; min-width: 28px; text-align: right; font-weight: bold; }}
  .cb-row {{ display: flex; align-items: center; gap: 6px; font-size: 12px; margin: 3px 0; cursor: pointer; }}
  .cb-row input {{ accent-color: #FF5722; }}
  #drawer {{
    position: fixed; top: 0; right: 0; width: clamp(480px, 33vw, 720px); max-width: 100vw; height: 100vh;
    background: rgba(7, 10, 24, 0.96); border-left: 1px solid rgba(255,255,255,0.08);
    box-shadow: -18px 0 36px rgba(0,0,0,0.35); z-index: 20; display: none;
    flex-direction: column; backdrop-filter: blur(10px);
  }}
  #drawer.open {{ display: flex; }}
  #drawer-header {{ padding: 18px 18px 12px; border-bottom: 1px solid rgba(255,255,255,0.08); }}
  #drawer-topline {{ display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }}
  #drawer-title {{ margin: 0; font-size: 20px; line-height: 1.2; }}
  #drawer-close {{ background: transparent; color: #9ea3b0; border: 0; font-size: 24px; line-height: 1; cursor: pointer; padding: 0; }}
  #drawer-meta {{ margin-top: 8px; font-size: 12px; color: #9ea3b0; }}
  #drawer-content {{ flex: 1; min-height: 0; padding: 14px 18px 18px; overflow: auto; }}
  #drawer-markdown {{ color: #e6e8ef; font-size: 13px; line-height: 1.72; }}
  #drawer-markdown h1, #drawer-markdown h2, #drawer-markdown h3 {{ margin: 1.2em 0 0.55em; color: #fff; }}
  #drawer-markdown h1 {{ font-size: 24px; }} #drawer-markdown h2 {{ font-size: 20px; }} #drawer-markdown h3 {{ font-size: 17px; }}
  #drawer-markdown p {{ margin: 0 0 0.95em; }}
  #drawer-markdown ul, #drawer-markdown ol {{ margin: 0 0 1em 1.35em; padding: 0; }}
  #drawer-markdown li {{ margin: 0.35em 0; }}
  #drawer-markdown hr {{ border: 0; border-top: 1px solid rgba(255,255,255,0.1); margin: 1.2em 0; }}
  #drawer-related {{ padding: 12px 18px 0; font-size: 12px; color: #9ea3b0; }}
  #drawer-related-list {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }}
  .related-chip {{
    background: rgba(255,255,255,0.08); color: #f1f2f7; border: 1px solid rgba(255,255,255,0.08);
    border-radius: 999px; font-size: 12px; padding: 5px 10px; cursor: pointer;
  }}
  @media (max-width: 960px) {{ #drawer {{ width: 100vw; }} }}
  #stats {{
    position: fixed; top: 10px; right: 10px; background: rgba(10,10,30,0.88);
    padding: 10px 14px; border-radius: 10px; font-size: 12px;
    backdrop-filter: blur(8px); border: 1px solid rgba(255,255,255,0.08);
  }}
</style>
</head>
<body>
<div id="controls">
  <h3>游戏策划知识图谱</h3>
  <input id="search" type="text" placeholder="搜索节点..." oninput="searchNodes(this.value)">
  <div>{legend_items}</div>
  <div class="filter-group">
    <label>边类型</label>
    <div class="cb-row"><input type="checkbox" id="cb-extracted" checked onchange="applyFilters()"><span style="color:#888">━</span> 确定性 ({n_extracted})</div>
    <div class="cb-row"><input type="checkbox" id="cb-inferred" checked onchange="applyFilters()"><span style="color:#FF5722">━</span> 推断 ({n_inferred})</div>
    <div class="cb-row"><input type="checkbox" id="cb-ambiguous" onchange="applyFilters()"><span style="color:#BDBDBD">━</span> 模糊 ({n_ambiguous})</div>
  </div>
  <div class="filter-group">
    <label>最低置信度</label>
    <div class="slider-row">
      <input type="range" id="conf-slider" min="0" max="100" value="50" oninput="applyFilters()">
      <span class="slider-val" id="conf-val">0.50</span>
    </div>
  </div>
  <p>点击节点查看详情，点击空白处恢复全图。</p>
</div>
<div id="graph"></div>
<aside id="drawer">
  <div id="drawer-header">
    <div id="drawer-topline">
      <h2 id="drawer-title"></h2>
      <button id="drawer-close" onclick="clearSelection()" aria-label="关闭">×</button>
    </div>
    <div id="drawer-meta"></div>
  </div>
  <div id="drawer-related">
    关联节点
    <div id="drawer-related-list"></div>
  </div>
  <div id="drawer-content">
    <div id="drawer-markdown"></div>
  </div>
</aside>
<div id="stats"></div>
<script>
const originalNodes = {nodes_json};
const originalEdges = {edges_json}.map(edge => ({{
  ...edge,
  id: edge.id || `${{edge.from}}->${{edge.to}}:${{edge.type || "INFERRED"}}`,
}}));
const nodes = new vis.DataSet(originalNodes);
const edges = new vis.DataSet(originalEdges);
const adjacency = new Map();
const searchInput = document.getElementById("search");
const statsEl = document.getElementById("stats");
const controls = {{
  extracted: document.getElementById("cb-extracted"),
  inferred: document.getElementById("cb-inferred"),
  ambiguous: document.getElementById("cb-ambiguous"),
  confSlider: document.getElementById("conf-slider"),
  confValue: document.getElementById("conf-val"),
}};
const nodeMap = new Map(originalNodes.map(node => [node.id, node]));
let activeNodeId = null;

function hexToRgba(color, alpha) {{
  if (!color) return `rgba(255, 255, 255, ${{alpha}})`;
  const normalized = color.replace("#", "");
  const value = normalized.length === 3 ? normalized.split("").map(ch => ch + ch).join("") : normalized;
  const intValue = Number.parseInt(value, 16);
  const r = (intValue >> 16) & 255;
  const g = (intValue >> 8) & 255;
  const b = intValue & 255;
  return `rgba(${{r}}, ${{g}}, ${{b}}, ${{alpha}})`;
}}

function escapeHtml(text) {{
  return (text || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}}

function rebuildAdjacency(filteredEdges) {{
  adjacency.clear();
  for (const node of originalNodes) adjacency.set(node.id, new Set());
  for (const edge of filteredEdges) {{
    if (!adjacency.has(edge.from)) adjacency.set(edge.from, new Set());
    if (!adjacency.has(edge.to)) adjacency.set(edge.to, new Set());
    adjacency.get(edge.from).add(edge.to);
    adjacency.get(edge.to).add(edge.from);
  }}
}}

function currentEdgeState() {{
  const minConf = parseInt(controls.confSlider.value, 10) / 100;
  controls.confValue.textContent = minConf.toFixed(2);
  return {{ showExtracted: controls.extracted.checked, showInferred: controls.inferred.checked, showAmbiguous: controls.ambiguous.checked, minConf }};
}}

function passesEdgeFilters(edge, edgeState) {{
  const typeOk = (edge.type === "EXTRACTED" && edgeState.showExtracted)
    || (edge.type === "INFERRED" && edgeState.showInferred)
    || (edge.type === "AMBIGUOUS" && edgeState.showAmbiguous);
  const confOk = (edge.confidence ?? 1.0) >= edgeState.minConf;
  return typeOk && confOk;
}}

function searchNodes(q) {{ applyFilters(q, activeNodeId); }}

function clearSelection() {{
  activeNodeId = null;
  document.getElementById("drawer").classList.remove("open");
  applyFilters(searchInput.value, null);
}}

function openDrawer(node, relatedIds) {{
  document.getElementById("drawer").classList.add("open");
  document.getElementById("drawer-title").textContent = node.label;
  const communityText = Number.isInteger(node.group) && node.group >= 0 ? ` · 社区 ${{node.group}}` : "";
  document.getElementById("drawer-meta").textContent = `${{node.type}}${{communityText}}`;
  document.getElementById("drawer-markdown").innerHTML = `<p>类型: ${{node.type}}</p><p>连接数: ${{relatedIds ? relatedIds.size - 1 : 0}}</p>`;

  const relatedList = document.getElementById("drawer-related-list");
  relatedList.innerHTML = "";
  const relatedNodes = originalNodes.filter(item => relatedIds.has(item.id) && item.id !== node.id).sort((a, b) => a.label.localeCompare(b.label));
  if (relatedNodes.length === 0) {{
    const empty = document.createElement("span"); empty.textContent = "无直接关联"; relatedList.appendChild(empty); return;
  }}
  for (const related of relatedNodes) {{
    const chip = document.createElement("button"); chip.className = "related-chip"; chip.textContent = related.label;
    chip.onclick = () => focusNode(related.id); relatedList.appendChild(chip);
  }}
}}

function applyFilters(query = searchInput.value, selectedNodeId = activeNodeId) {{
  const lower = (query || "").trim().toLowerCase();
  const edgeState = currentEdgeState();
  const filteredEdges = originalEdges.filter(edge => passesEdgeFilters(edge, edgeState));
  rebuildAdjacency(filteredEdges);
  const relatedIds = selectedNodeId ? new Set([selectedNodeId, ...(adjacency.get(selectedNodeId) || [])]) : null;
  const filteredNodeIds = new Set();
  for (const edge of filteredEdges) {{ filteredNodeIds.add(edge.from); filteredNodeIds.add(edge.to); }}

  let visibleNodeCount = 0;
  const nodeUpdates = originalNodes.map(node => {{
    const matchesSearch = !lower || node.label.toLowerCase().includes(lower);
    const isActive = selectedNodeId === node.id;
    const isConnected = filteredNodeIds.has(node.id);
    const isRelated = !relatedIds || relatedIds.has(node.id);
    const hidden = !selectedNodeId && !lower && !isConnected;
    const emphasized = matchesSearch && isRelated && (isConnected || !!lower || isActive);
    if (!hidden) visibleNodeCount += 1;
    return {{
      id: node.id, hidden,
      color: {{
        background: emphasized ? node.color : hexToRgba(node.color, hidden ? 0.05 : 0.14),
        border: emphasized ? hexToRgba(node.color, 0.96) : hexToRgba(node.color, hidden ? 0.08 : 0.22),
        highlight: {{ background: node.color, border: hexToRgba(node.color, 1) }},
        hover: {{ background: node.color, border: hexToRgba(node.color, 1) }},
      }},
      font: {{ color: emphasized ? "#f2f3f8" : hidden ? "rgba(242,243,248,0.08)" : "rgba(242,243,248,0.2)" }},
      borderWidth: isActive ? 5 : 2, size: isActive ? 18 : 12,
    }};
  }});

  const edgeUpdates = originalEdges.map(edge => {{
    const enabled = passesEdgeFilters(edge, edgeState);
    if (!enabled) return {{ id: edge.id, hidden: true }};
    const matchesSearch = !lower || nodeMap.get(edge.from)?.label.toLowerCase().includes(lower) || nodeMap.get(edge.to)?.label.toLowerCase().includes(lower);
    const isRelated = !relatedIds || relatedIds.has(edge.from) || relatedIds.has(edge.to);
    const touchesActive = !!selectedNodeId && (edge.from === selectedNodeId || edge.to === selectedNodeId);
    const emphasized = matchesSearch && isRelated;
    return {{ id: edge.id, hidden: false, width: touchesActive ? 2.8 : emphasized ? 1.2 : 0.6, color: emphasized ? edge.color : hexToRgba(edge.color, 0.08) }};
  }});

  nodes.update(nodeUpdates);
  edges.update(edgeUpdates);
  if (selectedNodeId) {{ const activeNode = nodeMap.get(selectedNodeId); if (activeNode) openDrawer(activeNode, relatedIds || new Set([selectedNodeId])); }}
  const focusSuffix = selectedNodeId && nodeMap.get(selectedNodeId) ? ` · 聚焦: ${{nodeMap.get(selectedNodeId).label}}` : "";
  statsEl.textContent = `${{visibleNodeCount}} 节点 · ${{filteredEdges.length}} 边${{focusSuffix}}`;
}}

const container = document.getElementById("graph");
const nodeCount = originalNodes.length;
const gravConst = nodeCount > 80 ? -8000 : nodeCount > 30 ? -5000 : -2000;
const springLen = nodeCount > 80 ? 250 : nodeCount > 30 ? 200 : 150;

const network = new vis.Network(container, {{ nodes, edges }}, {{
  nodes: {{ shape: "dot", font: {{ color: "#ddd", size: 12, strokeWidth: 3, strokeColor: "#111" }}, borderWidth: 1.5, scaling: {{ min: 8, max: 40, label: {{ enabled: true, min: 10, max: 20, drawThreshold: 6, maxVisible: 24 }} }} }},
  edges: {{ width: 0.8, smooth: {{ type: "continuous" }}, arrows: {{ to: {{ enabled: true, scaleFactor: 0.4 }} }}, color: {{ inherit: false }}, hoverWidth: 2 }},
  physics: {{ stabilization: {{ iterations: 250, updateInterval: 25, fit: true }}, barnesHut: {{ gravitationalConstant: gravConst, springLength: springLen, springConstant: 0.02, damping: 0.15 }}, minVelocity: 0.75 }},
  interaction: {{ hover: true, tooltipDelay: 150, hideEdgesOnDrag: true, hideEdgesOnZoom: true }},
}});

network.once("stabilizationIterationsDone", function () {{ network.fit({{ animation: {{ duration: 400, easingFunction: "easeInOutQuad" }} }}); }});

function focusNode(nodeId) {{
  activeNodeId = nodeId;
  applyFilters(searchInput.value, nodeId);
  const node = nodeMap.get(nodeId) || nodes.get(nodeId);
  const relatedIds = new Set([nodeId, ...(adjacency.get(nodeId) || [])]);
  openDrawer(node, relatedIds);
  network.focus(nodeId, {{ scale: 1.1, animation: {{ duration: 300, easingFunction: "easeInOutQuad" }} }});
}}

network.on("click", params => {{
  if (params.nodes.length > 0) focusNode(params.nodes[0]);
  else clearSelection();
}});

applyFilters();
</script>
</body>
</html>"""


def _build_and_save_graph(knowledge_dir, cache_dir, entity_refs, concept_refs, groups,
                          no_infer=False, skip_graph=False):
    """构建并保存知识图谱。

    Returns:
        dict: {json_path, html_path, report_path, node_count, edge_count}
              如果 skip_graph=True，返回空 dict。
    """
    if skip_graph:
        return {}

    graph_dir = os.path.join(knowledge_dir, 'graph')
    os.makedirs(graph_dir, exist_ok=True)

    today = date.today().isoformat()

    # Build nodes and edges
    print("  [i] Graph: building nodes and edges...")
    nodes = _build_graph_nodes(entity_refs, concept_refs, groups, cache_dir)
    edges = _build_graph_edges(entity_refs, concept_refs, groups)

    n_extracted = len(edges)
    print(f"  [i] Graph: {len(nodes)} nodes, {n_extracted} extracted edges")

    # Pass 2: LLM inference
    if not no_infer:
        print("  [i] Graph: running semantic inference (Pass 2)...")
        inferred = _build_inferred_edges(nodes, edges, cache_dir, graph_dir)
        edges.extend(inferred)
        print(f"  [i] Graph: {len(inferred)} inferred edges")
    else:
        print("  [i] Graph: skipping inference (--no-infer)")

    # Deduplicate
    before_dedup = len(edges)
    edges = _deduplicate_edges(edges)
    if before_dedup != len(edges):
        print(f"  [i] Graph: dedup {before_dedup} -> {len(edges)} edges")

    # Community detection
    print("  [i] Graph: running community detection...")
    communities = _detect_communities(nodes, edges)
    for node in nodes:
        comm_id = communities.get(node["id"], -1)
        if comm_id >= 0:
            node["color"] = COMMUNITY_COLORS[comm_id % len(COMMUNITY_COLORS)]
        node["group"] = comm_id

    # Compute degree-based node sizing
    degree_map = {}
    for e in edges:
        degree_map[e["from"]] = degree_map.get(e["from"], 0) + 1
        degree_map[e["to"]] = degree_map.get(e["to"], 0) + 1
    for node in nodes:
        node["value"] = degree_map.get(node["id"], 0) + 1

    # Save graph.json
    graph_data = {"nodes": nodes, "edges": edges, "built": today}
    json_path = os.path.join(graph_dir, 'graph.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(graph_data, f, ensure_ascii=False, indent=2)
    print(f"  [OK] graph/graph.json ({len(nodes)} nodes, {len(edges)} edges)")

    # Save graph.html
    html = _render_graph_html(nodes, edges)
    html_path = os.path.join(graph_dir, 'graph.html')
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  [OK] graph/graph.html")

    # Generate and save report
    report = _generate_graph_report(nodes, edges, communities, entity_refs, groups)
    report_path = os.path.join(graph_dir, 'graph-report.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"  [OK] graph/graph-report.md")

    return {
        'json_path': json_path,
        'html_path': html_path,
        'report_path': report_path,
        'node_count': len(nodes),
        'edge_count': len(edges),
    }


# ==============================================================
# 主入口
# ==============================================================

def compile_wiki(cache_dir, knowledge_dir, force=False, skip_graph=False, no_infer=False):
    """编译 wiki 索引。

    Args:
        cache_dir: .cache/ 目录路径
        knowledge_dir: knowledge/ 目录路径
        force: 是否强制重编译（暂留，目前每次都重编译）
        skip_graph: 是否跳过图谱构建
        no_infer: 是否跳过 LLM 语义推断

    Returns:
        dict: {generated_files, entity_count, concept_count, lint, graph}
    """
    wiki_dir = os.path.join(knowledge_dir, 'wiki')
    os.makedirs(wiki_dir, exist_ok=True)

    # 1. 自动扫描全部表名（从 gamedata/ 文件系统）
    gamedata_dir = os.path.join(knowledge_dir, 'gamedata')
    known_tables = _scan_xlsx_tables(gamedata_dir)
    print(f"  [i] Wiki: {len(known_tables)} table names from filesystem scan")

    # 2. 自动构建/更新 table_registry + CN-EN 翻译
    groups, registry_hash = _extract_table_groups(knowledge_dir, all_tables=known_tables)
    cn_en_map = _build_cn_en_map(wiki_dir, groups, registry_hash, force=force)
    # 构建 cn_groups: {chinese_keyword: [table_names]}
    cn_groups = {}
    if cn_en_map and groups:
        for cn_word, en_folder in cn_en_map.items():
            if en_folder in groups:
                cn_groups[cn_word] = groups[en_folder]
    if cn_groups:
        print(f"  [i] Wiki: {len(cn_groups)} Chinese keywords for entity matching")

    # 3. Entity 扫描（英文 + 中文）
    entity_refs = _scan_entity_refs(cache_dir, known_tables, cn_groups)
    print(f"  [i] Wiki: {len(entity_refs)} entities referenced in cache")

    # 4. Concept 扫描
    concept_refs = _scan_concept_refs(cache_dir)
    print(f"  [i] Wiki: {len(concept_refs)} cross-doc concepts found")

    # 5. Lint
    lint_result = _run_lint(entity_refs, concept_refs, cache_dir, known_tables)
    if lint_result['unknown_tables']:
        print(f"  [WARN] {len(lint_result['unknown_tables'])} unknown table names in 2+ docs")
    if lint_result['orphan_docs']:
        print(f"  [WARN] {len(lint_result['orphan_docs'])} orphan documents")

    # 6. 生成文件
    files = []
    files.append(_write_entity_index(wiki_dir, entity_refs))
    files.append(_write_concept_index(wiki_dir, concept_refs))
    files.append(_write_wiki_index(wiki_dir, entity_refs, concept_refs))
    files.append(_write_lint_report(wiki_dir, lint_result))

    # 6.5. 表结构索引（从 SQLite + openpyxl 读取，不依赖 .cache/ 文件）
    schemas = _build_table_schemas(knowledge_dir)
    if schemas:
        files.append(_write_table_schema_index(wiki_dir, schemas))
        print(f"  [i] Wiki: {len(schemas)} table schemas indexed")
    else:
        print(f"  [WARN] table_schema.md 未生成（registry 未找到或无列名数据）")

    for f in files:
        print(f"  [OK] {os.path.relpath(f, knowledge_dir)}")

    # 7. Graph (新增)
    graph_result = _build_and_save_graph(
        knowledge_dir, cache_dir, entity_refs, concept_refs, groups,
        no_infer=no_infer, skip_graph=skip_graph,
    )

    return {
        'generated_files': len(files),
        'entity_count': len(entity_refs),
        'concept_count': len(concept_refs),
        'schema_count': len(schemas),
        'lint': lint_result,
        'graph': graph_result,
    }


# -- CLI --
if __name__ == '__main__':
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    KNOWLEDGE_DIR = os.path.join(SCRIPT_DIR, 'knowledge')
    CACHE_DIR = os.path.join(KNOWLEDGE_DIR, 'gamedocs', '.cache')

    parser = argparse.ArgumentParser(description="Wiki 公有知识编译器")
    parser.add_argument('--skip-graph', action='store_true', help='跳过知识图谱构建')
    parser.add_argument('--no-infer', action='store_true', help='跳过 LLM 语义推断（更快）')
    parser.add_argument('--force', action='store_true', help='强制重编译')
    parser.add_argument('--open', action='store_true', help='构建后在浏览器中打开图谱')
    args = parser.parse_args()

    print("[+] Compiling wiki...")
    stats = compile_wiki(CACHE_DIR, KNOWLEDGE_DIR,
                         force=args.force,
                         skip_graph=args.skip_graph,
                         no_infer=args.no_infer)

    graph_info = ""
    if stats.get('graph'):
        g = stats['graph']
        graph_info = f", {g['node_count']} graph nodes, {g['edge_count']} edges"

    print(f"\n[OK] Wiki compiled: {stats['entity_count']} entities, "
          f"{stats['concept_count']} concepts, "
          f"{stats['generated_files']} files generated{graph_info}")

    if args.open and stats.get('graph', {}).get('html_path'):
        webbrowser.open(f"file://{os.path.abspath(stats['graph']['html_path'])}")
