# TODO — kb-builder Wiki 维护与 Wikilink 修复计划

## 状态：未实现

---

## Part 1: 增量维护

详见 `INCREMENTAL_PLAN.md`。

**目标：** 有表改动或新文档进来时，不必全量重跑 pipeline。

### 工具

| 文件 | 职责 |
|---|---|
| `tools/health.py` | 快速结构检查：空文件、index 同步、断链。零 LLM 调用。 |
| `tools/refresh.py` | 基于 content_hash 检测哪些 docx/xlsx 有变化，只重抽/重扫改动文件。 |
| `knowledge/wiki/_meta/.refresh_cache.json` | docx content_hash 缓存 |
| `knowledge/wiki/_tables/cn_en_map.json` | 表族中英对照表（已存在，需接入 pipeline） |
| `knowledge/wiki/log.md` | 追加式操作日志 |

### 设计约束

- `gamedocs/` 和 `gamedata/` 是 raw/immutable 层，pipeline 不修改源文件
- `wiki/` 完全由 pipeline 生成，手动编辑会被下次运行覆盖
- `_meta/` 是 graph 重建的唯一信任源

### API

```bash
uv run python tools/refresh.py          # 检测变化，只重抽/重扫
uv run python tools/refresh.py --force  # 全量重跑
uv run python tools/health.py           # 快速检查
uv run python tools/health.py --save    # 保存报告
```

---

## Part 2: Wikilink 断层修复

### 现状断层

| 断层 | 说明 |
|---|---|
| **字段 → 文档** | docx 的 entity 只连接到表名，不连接具体字段。如文档提到"BuffId"，无链路追溯到引用它的 docx |
| **字段无 wikilink** | `wiki/tables/Buff.md` 里字段只是文本列表 `·` 分隔，没有 `[[wikilink]]` 指向引用了它的文档或实体 |
| **字段语义无解释** | `_Buff.BuffId` 只告知字段名，不告知含义/枚举值。枚举值在 xlsx 数据行里，不在 wiki 里 |
| **FK 边不到字段级** | `table_fk_registry.json` 只有表级 FK（`_Buff.Field → _BuffCondition`），没有行级引用数据 |

### 目标：打通文档 ↔ 表 ↔ 字段的双向链路

```
docx 提取: "Buff系统" → configured_in → _Buff
                                        ↓
                              wiki/tables/Buff.md
                                        ↓
字段列表: BuffId · BuffClass · Round · ...
           ↑
           └── docx 里若提到 "BuffId"，能追溯到哪些文档章节讲过它
```

### 实现方案

#### Phase A: 表族页面加 wikilink（确定性，无需 LLM）

`wiki/tables/*.md` 的字段明细改为 wikilink 格式：

```markdown
### `_Buff`
- `[[BuffId]]` · `[[BuffClass]]` · `[[Round]]` · ...
```

同时在 `_meta/` 或 `_tables/` 里新增一个**字段→文档出处**的倒排索引：
- 扫描所有 `_meta/*.json` 的 entities 和 relationships
- 如果 entity 名（中文）与某字段名完全匹配，或 entity 是 `table` 类型且表名与字段名匹配，记录这条边
- 这个倒排索引在 `graph_builder` 阶段生成，供 `table_analyzer` 写字段 wikilink 时使用

#### Phase B: 字段级语义标注（可选，需要 LLM 或人工）

当前 `table_analyzer.py` 只读 header（字段名），不读数据行（枚举值）。

可选方案：
1. **枚举值扫描** — 读 xlsx 数据行，找出哪些字段枚举值有限，生成 `字段: [val1, val2, ...]` 附录注入 wiki
2. **字段注释行注入** — 很多策划表第 1 行是中文注释（如"BuffId, Buff类型, ..."），`table_analyzer` 的 header 解析逻辑本来就用了这个信息，可以在 wiki 里展示

### 实现顺序

1. **Phase A-1**: `graph_builder.py` 新增 `_tables/field_doc_ref.json`（字段 → 文档出处倒排索引）
2. **Phase A-2**: `table_analyzer.py` 生成字段 wikilink 版本，保留当前非 wikilink 版本
3. **Phase A-3**: `cn_en_map.json` 接入 `table_analyzer.py`（表族页面显示中文名）
4. **Phase B**: 可选——字段枚举值注入（需另起工具）
