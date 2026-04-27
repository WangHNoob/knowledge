# Wiki 语义知识库 — 架构设计文档

> 版本: v1.0 | 日期: 2025-04-27

## 1. 背景与目标

当前策划文档（docx/xlsx）经 `doc_reader` 解析后缓存为原始 markdown，但**缺乏结构化语义**——pipeline Agent 无法直接从中提取"规则""表结构""数值惯例"等信息。

**目标**：引入 LLM 语义提取层，将原始文档转化为结构化 wiki 页面 + 知识图谱，使 Agent 能按需查询游戏设计知识。

---

## 2. 整体流程

```
策划文档 (docx/xlsx)
       │
       ▼ doc_reader 解析
.cache/*.md (原始markdown)
       │
       ▼ Step 1+2: LLM 语义提取（每个文件一次调用）
       │
       │  输入: 原始md + spec模板
       │  输出: ┬── wiki页面.md    (结构化内容)
       │        ├── entities       (实体列表)
       │        └── relationships  (关系三元组)
       │
       ▼
  wiki/                    ← 结构化页面（5种类型）
  ├── systems/
  ├── activities/
  ├── tables/
  ├── numerical/
  └── combat/
       │
       ▼ Step 3: 图谱组装（Python脚本，非LLM）
       │
       │  汇总所有文档的 entities + relationships
       │  合并同名实体，组装图结构
       │
       ▼
  wiki/graph.json          ← 知识图谱
  wiki/index.md            ← 目录索引
```

---

## 3. Wiki 页面类型

从 4 个策划角色的消费场景倒推，定义 5 种页面类型：

| 类型 | 目录 | 回答的问题 | 主消费者 |
|------|------|-----------|---------|
| `system_rule` | `wiki/systems/` | 系统规则、流程、边界条件 | 系统策划、战斗策划 |
| `table_schema` | `wiki/tables/` | 表字段定义、枚举值、外键、ID段 | 系统策划、数值策划 |
| `numerical_convention` | `wiki/numerical/` | 数值参照、公式、产消模型 | 数值策划、玩法策划 |
| `activity_template` | `wiki/activities/` | 活动结构、奖励框架、定价惯例 | 玩法策划、数值策划 |
| `combat_framework` | `wiki/combat/` | 伤害公式、属性体系、buff架构 | 战斗策划、数值策划 |

### 页面格式

每个 wiki 页面包含 YAML frontmatter + markdown 正文：

```yaml
---
type: system_rule          # 页面类型（5选1）
title: "装备异化"           # 中文标题
source: "装备异化.docx"     # 来源文件
---
```

正文按各 spec 类型定义的章节结构组织（规则清单、字段定义、数值参照等）。

---

## 4. 实体与关系提取

### 4.1 Entity（实体）

实体 = 文档中有明确含义的专有名词。

| 类型 | 含义 | 示例 |
|------|------|------|
| `system` | 游戏系统/功能 | 装备异化、宝石系统、事迹系统 |
| `table` | 配置表名 | SwitchCondition、_Buff、EquipBaseProp |
| `resource` | 资源/道具 | 精炼石、技能书、金块、七彩炫光 |
| `attribute` | 属性 | 攻击力、暴击、速度、二级属性 |
| `activity` | 活动/玩法 | 竞技狂欢、血战、远征 |
| `concept` | 通用概念 | 二级共鸣、连胜机制、产消模型 |

LLM 输出示例：

```json
[
  {"name": "装备异化", "type": "system"},
  {"name": "宝石槽",   "type": "concept"},
  {"name": "SwitchCondition", "type": "table"},
  {"name": "精炼石",   "type": "resource"}
]
```

### 4.2 Relationship（关系）

关系 = 实体之间的有向连接，带类型。

| 关系类型 | 含义 | 示例 |
|----------|------|------|
| `depends_on` | A 前置依赖 B | 装备异化 → 装备共鸣 |
| `unlocks` | A 解锁 B | 异化 → 宝石槽 |
| `configured_in` | A 配置在 B 表中 | 异化开关 → SwitchCondition |
| `produces` | A 产出 B | 剧情回顾 → 精炼石 |
| `consumes` | A 消耗 B | 装备精炼 → 精炼石 |
| `belongs_to` | A 属于 B 体系 | 宝石槽 → 宝石系统 |
| `references` | A 参考 B | 新活动 → 竞技狂欢模板 |

LLM 输出示例：

```json
[
  {"source": "装备异化", "target": "装备共鸣",       "relation": "depends_on"},
  {"source": "装备异化", "target": "宝石槽",         "relation": "unlocks"},
  {"source": "装备异化", "target": "SwitchCondition", "relation": "configured_in"}
]
```

---

## 5. 知识图谱

### 5.1 组装逻辑

Step 3 脚本汇总所有文档的 entities + relationships：

- **同名 entity 自动合并**（不同文档提到同一个"宝石槽" → 合为一个节点）
- **边附带来源信息**（记录该关系来自哪篇文档）

### 5.2 graph.json 格式

```json
{
  "nodes": [
    {"id": "装备异化", "type": "system", "wiki_page": "systems/equip_mutation.md"},
    {"id": "宝石槽",   "type": "concept", "wiki_page": null},
    {"id": "SwitchCondition", "type": "table", "wiki_page": "tables/buff_family.md"}
  ],
  "edges": [
    {"source": "装备异化", "target": "装备共鸣", "relation": "depends_on", "from_doc": "装备异化.docx"},
    {"source": "装备异化", "target": "宝石槽",   "relation": "unlocks",    "from_doc": "装备异化.docx"}
  ]
}
```

### 5.3 查询能力

| 查询类型 | 示例 | 实现方式 |
|----------|------|---------|
| 单跳查询 | "装备异化依赖什么？" | 过滤 `source=装备异化, relation=depends_on` |
| 反向查询 | "精炼石从哪产出？" | 过滤 `target=精炼石, relation=produces` |
| 多跳遍历 | "从SP黄猿出发要配哪些表？" | 沿边递归遍历 |
| 共现发现 | "装备异化和七彩宝石有关联吗？" | 检查是否有共同 entity 或路径 |

---

## 6. 实施阶段

| 阶段 | 内容 | 状态 |
|------|------|------|
| **Phase 1** | 定义 5 种页面 spec，手动验证样本 | ✅ 完成 |
| **Phase 2** | 编写 LLM Prompt（md → wiki + entities + relationships） | 待启动 |
| **Phase 3** | 图谱组装脚本（汇总 → graph.json + index.md） | 待启动 |
| **Phase 4** | Pipeline 集成（locate/fill 阶段消费图谱和 wiki） | 待启动 |

---

## 7. 已有样本

| 文件 | 类型 | 来源 |
|------|------|------|
| `wiki/systems/equip_mutation.md` | system_rule | 装备异化.docx |
| `wiki/activities/pvp_arena_carnival.md` | activity_template | pvp活动模板.docx + 数值总表 |
| `wiki/tables/buff_family.md` | table_schema | 战斗表.xlsx |
| `wiki/numerical/resource_economy.md` | numerical_convention | 怀旧服数值总表.xlsx |
