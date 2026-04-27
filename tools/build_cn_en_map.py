# -*- coding: utf-8 -*-
"""
生成表族中英对照表。

从 knowledge/wiki/_tables/groups.json 读取所有表族名，
通过 LLM 将英文表族名翻译为中文，生成 cn_en_map.json。

Usage:
    uv run python tools/build_cn_en_map.py
    uv run python tools/build_cn_en_map.py --force   # 强制重新生成
    uv run python tools/build_cn_en_map.py --dry-run # 只看 LLM 输出，不写文件
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# 将项目根加入路径，以便 import kb_builder.config
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None
if load_dotenv:
    load_dotenv(os.path.join(_ROOT, ".env"))


def _load_groups() -> list[str]:
    groups_path = Path(_ROOT) / "knowledge" / "wiki" / "_tables" / "groups.json"
    if not groups_path.exists():
        print(f"[error] {groups_path} not found — run tables stage first")
        sys.exit(1)
    with open(groups_path, encoding="utf-8") as f:
        groups = json.load(f)
    # groups: {group_name: [table_names]}
    # 过滤掉 _misc 等纯内部分组，只取有实际表成员的正经英文族名
    skip = {"_misc"}
    names = sorted(k for k in groups if k not in skip and not k.startswith("_"))
    return names


def _call_llm(groups: list[str]) -> dict[str, str]:
    """调用 LLM 将英文表族名翻译为中文。返回 {英文: 中文}。"""
    try:
        from litellm import completion
    except ImportError:
        print("[error] litellm not installed. Run: uv sync --extra llm")
        sys.exit(1)

    groups_text = "\n".join(f"  - {g}" for g in groups)
    prompt = f"""你是一个游戏后端开发团队的策划翻译助手。

以下是一个手游的配置表族名（英文），请将每个英文表族名翻译为中文。

规则：
- 使用游戏行业标准术语（如 Arena → 竞技场，Achievement → 成就）
- 不确定时保留英文并在后面加括号标注（如 BuffSystem → Buff系统）
- 只返回 JSON 对象，不要解释，不要 markdown 代码块
- 格式：{{"表族英文名": "中文翻译", ...}}

表族名列表：
{groups_text}

返回 JSON："""

    model = os.getenv("LLM_MODEL_FAST") or os.getenv("LLM_MODEL") or "claude-3-5-haiku-latest"
    print(f"[info] calling LLM: {model}")

    response = completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )
    raw = response.choices[0].message.content.strip()

    # 去掉可能的 markdown 代码块
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        raw = raw.lstrip("json").strip()

    result = json.loads(raw)
    # 统一为 {英文: 中文}
    out: dict[str, str] = {}
    for k, v in result.items():
        # key 可能是英文也可能是中文，统一存英文->中文
        # 用户说中文放前面，所以 key 是英文
        out[k] = v
    return out


def _load_existing_map(path: Path) -> dict[str, str]:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="生成表族中英对照表")
    parser.add_argument("--force", action="store_true", help="强制重新翻译（忽略已有映射）")
    parser.add_argument("--dry-run", action="store_true", help="只输出 LLM 结果，不写文件")
    args = parser.parse_args()

    groups = _load_groups()
    print(f"[info] {len(groups)} 表族名待翻译")

    out_path = Path(_ROOT) / "knowledge" / "wiki" / "_tables" / "cn_en_map.json"
    existing = {} if args.force else _load_existing_map(out_path)

    # 已有映射中非待翻译的条目（用户可能手动加了额外条目）保留
    all_keys = set(existing.keys()) | set(groups)
    missing = [g for g in groups if g not in existing]
    print(f"[info] 已翻译: {len(groups) - len(missing)}/{len(groups)}, 待翻译: {len(missing)}")

    if missing and not args.dry_run:
        new_map = _call_llm(missing)
        # 合并
        existing.update(new_map)

    if args.dry_run:
        if missing:
            result = _call_llm(missing)
            print("\n--- LLM 翻译结果 ---")
            for k, v in result.items():
                print(f"  {k} → {v}")
        else:
            print("[info] 所有表族名已有翻译")
        return

    # 按键排序写入
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"[info] 写入 {out_path} ({len(existing)} 条)")


if __name__ == "__main__":
    main()
