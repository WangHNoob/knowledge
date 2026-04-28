# Spec: table_schema

**适用场景**：描述配置表的字段定义、枚举值、外键、ID 段。主消费者：系统策划、数值策划。

## Frontmatter

```yaml
---
type: table_schema
title: "<表名或表簇名>"
source: "<源文件名.docx>"
---
```

## 章节结构（wiki_markdown 必须包含以下 h2 段落）

- `## 表用途` — 一句话说明这张表（或这组表）存什么
- `## 字段定义` — markdown 表格，列：`字段名 | 类型 | 说明 | 示例`
- `## 枚举值` — 关键字段的取值空间，按字段分组列出含义。没有就写"无"
- `## 外键关系` — 指向其他表的字段 + 被引用表名（表名必须来自提供的表名列表）
- `## ID 段惯例` — ID 区间划分规则（比如 1xx 是系统 A，2xx 是系统 B）。没有就写"无"

## 示例（Buff 系统表族）

```markdown
## 表用途
_Buff 族表管理战斗内所有 buff 的定义、叠加规则与互斥关系。

## 字段定义
| 字段名 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| Id | int | buff 主键 | 10001 |
| Type | enum | buff 类型 | ATTACK_UP |
| Duration | int | 持续帧数 | 300 |

## 枚举值
- `Type`: ATTACK_UP / DEFENSE_UP / DOT / CONTROL
- `StackMode`: REPLACE / ADD / REFRESH

## 外键关系
- `SourceSkillId` → `Skill` 表
- `MutexGroup` → `BuffStateMutex` 表

## ID 段惯例
- 1xxxx: 主动技能产生的 buff
- 2xxxx: 装备被动触发的 buff
- 9xxxx: 调试用 buff
```
