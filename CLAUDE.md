# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在本代码仓库中工作时提供指导。

## 概述

`kb-builder` 将游戏设计规划文档（docx/xlsx）转换为结构化 wiki，包含语义实体提取和知识图谱。管道分为 5 个阶段：`convert` → `extract` → `tables` → `graph` → `viz`。

## 常用命令

```bash
# 全流程（5 个阶段）
uv run python run_pipeline.py

# 运行单个阶段
uv run python run_pipeline.py --stage extract

# 只抽取特定 docx
uv run python run_pipeline.py --stage extract --only 装备异化.docx

# 强制重处理（忽略缓存）
uv run python run_pipeline.py --force

# 指定 LLM 模型
uv run python run_pipeline.py --model gpt-4o

# 指定数据目录
uv run python run_pipeline.py --data-dir ./my_data

# 直接运行 scripts（替代入口）
python -m scripts                    # 全流程
python -m scripts --stage tables    # 单阶段
```

## 架构

### 数据流

```
raw/gamedocs/  （.docx 源文件）
     ↓  [convert 阶段]  doc_reader.py
raw/gamedocs/.cache/  （原始 markdown）

     ↓  [extract 阶段]  wiki_extractor.py  （每个 doc 一次 LLM 调用）
wiki/systems/, wiki/activities/, 等  （结构化页面）
wiki/_meta/*.json  （实体 + 关系）

     ↓  [tables 阶段]  table_analyzer.py  （无 LLM）
wiki/_tables/schemas.json, groups.json, table_fk_registry.json

     ↓  [graph 阶段]  graph_builder.py  （确定性，无 LLM）
wiki/graph.json, wiki/index.md

     ↓  [viz 阶段]  graph_viz.py
wiki/graph.html  （交互式 D3 可视化）
```

### 核心设计约束

- **`raw/` 不可变** — 源文档从不原地修改
- **`wiki/` 完全生成** — 手动编辑下次运行会被覆盖
- **`_meta/` 是图谱重建的可信来源**
- **增量处理**：缓存基于内容哈希；未变更文件跳过重处理

### Wiki 页面类型（`processed/wiki_specs/` 中有 5 种规范）

| 类型 | 目录 | 用途 |
|------|-----------|----------|
| `system_rule` | `wiki/systems/` | 系统规则、流程、边界条件 |
| `table_schema` | `wiki/tables/` | 表字段定义、枚举、FK |
| `numerical_convention` | `wiki/numerical/` | 数值引用、公式 |
| `activity_template` | `wiki/activities/` | 活动结构、奖励框架 |
| `combat_framework` | `wiki/combat/` | 伤害公式、属性系统 |

### 实体类型

`system`, `table`, `resource`, `attribute`, `activity`, `concept`

### 关系类型

`depends_on`, `unlocks`, `configured_in`, `produces`, `consumes`, `belongs_to`, `references`

## 项目结构

```
scripts/
  config.py          # 通过 PATHS 数据类解析路径
  build_wiki.py      # 管道编排器
  doc_reader.py      # 解析 docx/xlsx 为原始 markdown
  convert/           # 批量 docx 转换
  extract/
    wiki_extractor.py   # LLM 语义提取（按文档）
    table_analyzer.py   # 确定性 xlsx schema 扫描
  graph/
    graph_builder.py    # 组装 graph.json + index.md
    graph_viz.py        # D3 交互可视化
  tables/             # 表家族页面
  viz/                # 可视化辅助

processed/wiki_specs/   # 5 种页面类型的 LLM 提取规范
raw/gamedocs/.cache/    # docx→md 中间输出
wiki/_meta/             # 每文档 JSON（实体/关系）
wiki/_tables/           # 注册表 JSON（schemas、groups、FK 边）
wiki/systems/, activities/, tables/, numerical/, combat/  # Wiki 页面
```

## 关键路径（config.py PATHS）

| 变量 | 默认值 |
|----------|---------|
| `KB_DATA_DIR` | `./knowledge`（项目根目录） |
| `KB_WIKI_SPECS_DIR` | `knowledge/processed/wiki_specs` |
| `LLM_MODEL_FAST` | `claude-3-5-haiku-latest` |

## 依赖（pyproject.toml）

- 核心：`markitdown[docx,xlsx]`, `openpyxl`, `python-docx`, `python-dotenv`
- 可选 `graph`：`networkx`
- 可选 `llm`：`litellm`, `openai`

安装：`uv sync --extra llm`
