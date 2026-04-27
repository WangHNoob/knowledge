# -*- coding: utf-8 -*-
"""
Wiki 语义提取：每个 docx 一次 LLM 调用，产出

  1) 结构化 wiki 页面（匹配 5 种 spec 中的一种）
  2) 实体列表 entities
  3) 关系列表 relationships

产物：
  knowledge/wiki/<page_type_dir>/<slug>.md      结构化页面
  knowledge/wiki/_meta/<doc_stem>.json          实体+关系+content_hash 侧车

缓存：按原始 md 的 content_hash 跳过未变更的文档；--force 绕过。
健壮性：JSON 解析失败时重试一次；二次失败写 stub meta，不中断批次。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from typing import Optional

from scripts.config import PATHS

PROJECT_ROOT = str(PATHS.project_root)
GAMEDOCS_DIR = str(PATHS.gamedocs_dir)
GAMEDATA_DIR = str(PATHS.gamedata_dir)
WIKI_DIR = str(PATHS.wiki_dir)
META_DIR = str(PATHS.wiki_meta_dir)
SPECS_DIR = str(PATHS.wiki_specs_dir)

ENTITY_TYPES = {"system", "table", "resource", "attribute", "activity", "concept"}
RELATION_TYPES = {
    "depends_on", "unlocks", "configured_in",
    "produces", "consumes", "belongs_to", "references",
}
PAGE_TYPE_DIRS = {
    "system_rule": "systems",
    "table_schema": "tables",
    "numerical_convention": "numerical",
    "activity_template": "activities",
    "combat_framework": "combat",
}


# ---------- 辅助 ----------

def _scan_xlsx_tables(gamedata_dir: str) -> list[str]:
    """扫描 gamedata/ 下的 .xlsx 文件名（不含后缀），返回有序列表。

    同 old/wiki_compiler.py:70 的 _scan_xlsx_tables，但本流程只作为 LLM 上下文使用。
    """
    tables: set[str] = set()
    if not os.path.isdir(gamedata_dir):
        return []
    for root, _dirs, files in os.walk(gamedata_dir):
        for fname in files:
            if fname.lower().endswith(".xlsx") and not fname.startswith("~"):
                stem = fname[:-5]
                rel = os.path.relpath(root, gamedata_dir)
                if rel == ".":
                    tables.add(stem)
                else:
                    tables.add(rel.replace(os.sep, "/") + "/" + stem)
    return sorted(tables)


def _slugify(title: str, source: str) -> str:
    """从 title 生成文件名；中文 title 用 source 文件名去后缀作 fallback。"""
    s = re.sub(r"[^\w一-鿿-]+", "_", title).strip("_")
    if not s or all(not c.isascii() for c in s):
        s = os.path.splitext(os.path.basename(source))[0]
        s = re.sub(r"[^\w一-鿿-]+", "_", s).strip("_")
    return s[:80] or "page"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _load_specs() -> dict[str, str]:
    out = {}
    for page_type in PAGE_TYPE_DIRS:
        path = os.path.join(SPECS_DIR, f"{page_type}.md")
        with open(path, "r", encoding="utf-8") as f:
            out[page_type] = f.read()
    return out


def _read_cached_md(docx_path: str) -> Optional[str]:
    """读取 doc_reader 缓存的 md 全文。"""
    cache_path = os.path.join(
        os.path.dirname(docx_path), ".cache",
        os.path.basename(docx_path) + ".md",
    )
    if not os.path.exists(cache_path):
        return None
    with open(cache_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------- Prompt 构造 ----------

def _build_prompt(raw_md: str, source_name: str, specs: dict[str, str],
                  table_names: list[str]) -> str:
    # 全量注入已知表名，保证 LLM 能按精确表名完成实体对齐。
    tables_str = ", ".join(table_names)

    specs_block = "\n\n".join(
        f"### spec: {name}\n{body}" for name, body in specs.items()
    )

    return f"""你是游戏策划知识库的语义提取助手。阅读下面的策划文档原文，从 5 种 wiki 页面类型中选 1 种最匹配的，
按该类型的 spec 章节结构生成结构化页面，并抽取实体和关系。

# 输出格式（严格 JSON，不要包含 markdown 代码块围栏）

{{
  "page_type": "system_rule | table_schema | numerical_convention | activity_template | combat_framework",
  "title": "页面中文标题（通常是系统/活动/表名）",
  "wiki_markdown": "页面正文 markdown（不含 YAML frontmatter，由下游补齐）",
  "entities": [ {{"name": "实体中文名或表英文名", "type": "system|table|resource|attribute|activity|concept"}} ],
  "relationships": [ {{"source": "...", "target": "...", "relation": "depends_on|unlocks|configured_in|produces|consumes|belongs_to|references"}} ]
}}

# 5 种页面 spec

{specs_block}

# 抽取规则

- **entity.type 只能是** system / table / resource / attribute / activity / concept
- **relationship.relation 只能是** depends_on / unlocks / configured_in / produces / consumes / belongs_to / references
- 所有 relationship 的 source 和 target 必须同时出现在 entities 列表中
- 如果实体是配置表，name 必须使用下方"已知配置表列表"中的精确名字（这样跨文档才能合并同名实体）
- 实体名保持文档原用词；不要编造文中没提到的实体
- wiki_markdown 严格按所选 spec 的 h2 章节组织；章节缺内容时写"无"或"见正文"，不要删除章节

# 已知配置表列表（LLM 参考，共 {len(table_names)} 张表；只有被文档提到的才写入 entities）

{tables_str}

# 待处理文档：{source_name}

{raw_md}
"""


# ---------- LLM 调用 ----------

def _call_llm(prompt: str, max_tokens: int = 8192,
              response_format_json: bool = True) -> str:
    try:
        from litellm import completion
    except ImportError:
        raise RuntimeError("litellm not installed. Run: pip install litellm")

    model = os.getenv("LLM_MODEL_FAST", "claude-3-5-haiku-latest")

    kwargs = dict(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    if response_format_json:
        # 某些模型不支持；失败时由调用方兜底
        try:
            response = completion(
                **kwargs,
                response_format={"type": "json_object"},
            )
            return response.choices[0].message.content
        except Exception:
            pass
    response = completion(**kwargs)
    return response.choices[0].message.content


def _parse_json_lenient(text: str) -> Optional[dict]:
    """尽力解析 JSON，先直接 parse，失败时剥离 ```json ... ``` 围栏再试。"""
    try:
        return json.loads(text)
    except Exception:
        pass
    # 剥离围栏
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 截取第一个 { 到最后一个 }
    l = text.find("{")
    r = text.rfind("}")
    if l >= 0 and r > l:
        try:
            return json.loads(text[l:r + 1])
        except Exception:
            pass
    return None


# ---------- 校验 & 落盘 ----------

def _validate(payload: dict) -> tuple[dict, list[str]]:
    """原地过滤非法枚举值，返回 (清洗后的 payload, 警告列表)。"""
    warnings: list[str] = []
    page_type = payload.get("page_type")
    if page_type not in PAGE_TYPE_DIRS:
        warnings.append(f"unknown page_type={page_type!r}, fallback to system_rule")
        payload["page_type"] = "system_rule"

    ents = payload.get("entities") or []
    kept_ents = []
    seen_names: set[str] = set()
    for e in ents:
        name = (e.get("name") or "").strip()
        etype = e.get("type")
        if not name:
            continue
        if etype not in ENTITY_TYPES:
            warnings.append(f"drop entity {name!r}: bad type {etype!r}")
            continue
        if name in seen_names:
            continue
        seen_names.add(name)
        kept_ents.append({"name": name, "type": etype})
    payload["entities"] = kept_ents

    rels = payload.get("relationships") or []
    kept_rels = []
    for r in rels:
        s = (r.get("source") or "").strip()
        t = (r.get("target") or "").strip()
        rel = r.get("relation")
        if not s or not t or rel not in RELATION_TYPES:
            warnings.append(f"drop relation {s!r}-[{rel}]->{t!r}: invalid")
            continue
        if s not in seen_names or t not in seen_names:
            warnings.append(
                f"drop relation {s!r}-[{rel}]->{t!r}: endpoint not in entities"
            )
            continue
        kept_rels.append({"source": s, "target": t, "relation": rel})
    payload["relationships"] = kept_rels

    return payload, warnings


def _write_wiki_page(payload: dict, source_name: str) -> str:
    page_type = payload["page_type"]
    title = payload.get("title") or os.path.splitext(source_name)[0]
    slug = _slugify(title, source_name)
    subdir = os.path.join(WIKI_DIR, PAGE_TYPE_DIRS[page_type])
    os.makedirs(subdir, exist_ok=True)

    # 同名 slug 追加数字
    wiki_path = os.path.join(subdir, f"{slug}.md")
    i = 2
    while os.path.exists(wiki_path):
        existing_src = _read_source_field(wiki_path)
        if existing_src == source_name:
            break  # 是我们自己上一次的产物，覆盖
        wiki_path = os.path.join(subdir, f"{slug}_{i}.md")
        i += 1

    frontmatter = (
        "---\n"
        f"type: {page_type}\n"
        f'title: "{title}"\n'
        f'source: "{source_name}"\n'
        "---\n\n"
    )
    body = (payload.get("wiki_markdown") or "").strip() + "\n"
    with open(wiki_path, "w", encoding="utf-8") as f:
        f.write(frontmatter + body)
    return os.path.relpath(wiki_path, WIKI_DIR).replace(os.sep, "/")


def _read_source_field(wiki_path: str) -> Optional[str]:
    try:
        with open(wiki_path, "r", encoding="utf-8") as f:
            head = f.read(400)
        m = re.search(r'source:\s*"([^"]+)"', head)
        return m.group(1) if m else None
    except Exception:
        return None


def _write_meta(doc_stem: str, source_name: str, content_hash: str,
                payload: dict, wiki_relpath: str, warnings: list[str],
                error: Optional[str] = None) -> None:
    os.makedirs(META_DIR, exist_ok=True)
    meta = {
        "source": source_name,
        "content_hash": content_hash,
        "page_type": payload.get("page_type"),
        "title": payload.get("title"),
        "wiki_path": wiki_relpath,
        "entities": payload.get("entities", []),
        "relationships": payload.get("relationships", []),
        "warnings": warnings,
    }
    if error:
        meta["error"] = error
    path = os.path.join(META_DIR, f"{doc_stem}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2, sort_keys=True)


def _load_meta(doc_stem: str) -> Optional[dict]:
    path = os.path.join(META_DIR, f"{doc_stem}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ---------- 单文档处理 ----------

def extract_one(docx_path: str, specs: dict[str, str],
                table_names: list[str], force: bool = False) -> dict:
    source_name = os.path.basename(docx_path)
    doc_stem = os.path.splitext(source_name)[0]

    raw_md = _read_cached_md(docx_path)
    if raw_md is None:
        print(f"  [skip] 没有 .cache md，先运行 batch_convert: {source_name}")
        return {"status": "no_cache"}

    ch = _content_hash(raw_md)
    if not force:
        prev = _load_meta(doc_stem)
        if prev and prev.get("content_hash") == ch and not prev.get("error"):
            print(f"  [cache] {source_name}")
            return {"status": "cached"}

    prompt = _build_prompt(raw_md, source_name, specs, table_names)

    # 1st 尝试
    raw = _call_llm(prompt)
    payload = _parse_json_lenient(raw)

    # 重试一次
    if payload is None:
        retry = (
            "你上次输出的不是合法 JSON。请只输出一个 JSON 对象，不要任何额外文字或围栏。\n"
            "按同样的 schema 再输出一次。"
        )
        raw2 = _call_llm(prompt + "\n\n" + retry)
        payload = _parse_json_lenient(raw2)

    if payload is None:
        print(f"  [error] JSON parse failed after retry: {source_name}")
        _write_meta(
            doc_stem, source_name, ch,
            {"page_type": "system_rule", "title": doc_stem,
             "entities": [], "relationships": []},
            "", [], error="json_parse_failed",
        )
        return {"status": "parse_failed"}

    payload, warnings = _validate(payload)
    wiki_relpath = _write_wiki_page(payload, source_name)
    _write_meta(doc_stem, source_name, ch, payload, wiki_relpath, warnings)

    ne = len(payload["entities"])
    nr = len(payload["relationships"])
    print(f"  [ok] {source_name} -> {wiki_relpath}  ({ne} entities, {nr} rels)")
    return {"status": "ok", "wiki_path": wiki_relpath,
            "entities": ne, "relationships": nr}


# ---------- CLI ----------

def extract_all(force: bool = False, only: Optional[str] = None) -> None:
    if not os.path.isdir(GAMEDOCS_DIR):
        print(f"gamedocs 目录不存在: {GAMEDOCS_DIR}")
        return

    specs = _load_specs()
    table_names = _scan_xlsx_tables(GAMEDATA_DIR)
    print(f"加载 {len(specs)} 份 spec, {len(table_names)} 张配置表\n")

    docs = sorted(
        os.path.join(GAMEDOCS_DIR, f)
        for f in os.listdir(GAMEDOCS_DIR)
        if f.lower().endswith(".docx") and not f.startswith("~")
    )
    if only:
        docs = [p for p in docs if os.path.basename(p) == only]
        if not docs:
            print(f"没找到 {only}")
            return

    for p in docs:
        print(f"\n处理: {os.path.basename(p)}")
        try:
            extract_one(p, specs, table_names, force=force)
        except Exception as e:
            print(f"  [error] {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Per-docx LLM wiki extraction")
    parser.add_argument("-f", "--force", action="store_true",
                        help="忽略缓存，全部重新抽取")
    parser.add_argument("--only", type=str, default=None,
                        help="只处理指定文件名（如 '装备异化.docx'）")
    args = parser.parse_args()
    extract_all(force=args.force, only=args.only)
