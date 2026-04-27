# 增量 Wiki 维护计划

## 状态：尚未实现

---

## 参考架构（来自 llm-wiki-agent）

参考项目（`llm-wiki-agent-main/`）使用三个关键设计：
1. **`raw/` 不可变** — 源文档从不原地修改；变更 = 新文件
2. **`log.md` 只追加日志** — 每次 ingest/refresh 都有时间戳和可追溯性
3. **`refresh.py` 基于哈希的变更检测** — 只重处理变更的源文件

---

## 当前状态 vs 目标

| 场景 | 当前行为 | 目标增量行为 |
|---|---|---|
| 新增 docx | 全量重新提取所有文档 | 检测新文件，只提取该文件 |
| docx 内容变更 | `content_hash` 缓存存在，需 `--force` | `refresh.py` 自动检测哈希漂移，只重新提取该文件 |
| 新增 xlsx | 全量重新扫描全部 1243 个文件 | 检测新文件，追加到 schemas.json |
| xlsx 结构变更 | 缓存键使用 mtime+size，自动刷新 | 相同 — 已确定性工作 |
| 新增 wiki 页面 | 全管道重新运行 | `graph_builder` 是确定性的 — 添加/移除节点 |
| 健康检查 | 无 | `health.py` 检查孤儿文件、断链、索引同步 |

---

## 待创建的新文件

```
tools/
  health.py      # 快速结构检查：空文件、索引同步、断链
  refresh.py      # 基于哈希的变更检测 + 选择性重新摄入

knowledge/wiki/
  log.md          # 只追加操作日志
  _meta/
    .refresh_cache.json   # 每个 docx 的 content_hash 缓存
    .xlsx_cache.json      # 每个 xlsx 的 (mtime, size) 缓存
```

---

## 设计约束

- **`knowledge/gamedocs/` 和 `knowledge/gamedata/` 是原始/不可变层**
  — 视为只读；管道从不修改源文件
- **`knowledge/wiki/` 完全生成** — 手动编辑下次运行会被覆盖
- **`_meta/` 是图谱重建的可信来源** — graph.json/index.md 仅从 `_meta/*.json` 重新生成
- **增量图谱更新** — `graph_builder` 应 diff 旧版 vs 新版 `_meta/`，只添加/移除受影响的节点/边，而不是全量重建

---

## 增量 API（拟）

```bash
# 全流程（不变）
uv run python run_pipeline.py

# 选择性重运行（新）
uv run python tools/refresh.py          # 检测变更源，只重新提取
uv run python tools/refresh.py --force  # 强制重新摄入全部

# 健康检查（新）
uv run python tools/health.py
uv run python tools/health.py --save    # 保存报告到 wiki/health-report.md

# 单 docx 重新提取（新）
uv run python run_pipeline.py --stage extract --only 装备异化.docx

# xlsx 变更后，重新运行 tables + graph（新）
uv run python run_pipeline.py --stage tables
uv run python run_pipeline.py --stage graph
uv run python run_pipeline.py --stage viz
```

---

## 实现顺序

1. **`tools/health.py`** — 最简单，零 LLM 调用，验证现有 wiki 结构
2. **`wiki/log.md`** — 简单的只追加日志，集成到管道各阶段
3. **`tools/refresh.py`** — docx 的哈希缓存，检测漂移，选择性重新提取
4. **`_meta/.xlsx_cache.json`** — 正式化 xlsx 缓存路径（已存在于 `_tables/` 中）
5. **增量 `graph_builder`** — diff `_meta/` 以避免仅 1 个文档变更时全量重建
