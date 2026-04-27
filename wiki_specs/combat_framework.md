# Spec: combat_framework

**适用场景**：战斗框架 — 伤害公式、属性体系、buff 架构。主消费者：战斗策划、数值策划。

## Frontmatter

```yaml
---
type: combat_framework
title: "<战斗主题>"
source: "<源文件名.docx>"
---
```

## 章节结构（wiki_markdown 必须包含以下 h2 段落）

- `## 框架概述` — 这份战斗文档覆盖的范围（伤害链 / 属性体系 / buff / 技能判定）
- `## 属性体系` — 一级属性、二级属性、派生关系
  - 推荐表格：`属性名 | 一级/二级 | 派生公式 | 备注`
- `## 伤害公式` — 用代码块给出完整公式；包含各项含义
- `## buff 架构` — buff 分类、叠加/互斥规则、结算时机
- `## 判定与顺序` — 技能结算顺序、命中/暴击/闪避判定链。没有就写"无"
- `## 相关配置表` — 涉及的 xlsx 表名（必须来自提供的表名列表）

## 示例

```markdown
## 框架概述
怀旧服战斗的伤害链与属性派生规则。

## 属性体系
| 属性名 | 级别 | 派生公式 | 备注 |
|--------|------|----------|------|
| 攻击力 | 一级 | 基础值 + 装备+buff | 见 AttrLevelUpTable |
| 暴击 | 二级 | `crit_rate = crit / (crit + 100)` | |

## 伤害公式
```
dmg = (atk - def * 0.8) * skill_coef * crit_mult * (1 + buff_sum)
crit_mult = 1.5 if crit_roll < crit_rate else 1.0
```

## buff 架构
- 分类：增益 / 减益 / 控制
- 同名 buff 按 StackMode 决定叠加（REPLACE/ADD/REFRESH）
- 结算时机：回合开始前 → 伤害结算 → 回合结束

## 判定与顺序
1. 命中判定（hit vs dodge）
2. 暴击判定（crit_rate）
3. 伤害计算
4. buff 挂接

## 相关配置表
- `_Buff` — buff 主表
- `BuffStateMutex` — 互斥分组
- `AttrLevelUpTable` — 属性成长
```
