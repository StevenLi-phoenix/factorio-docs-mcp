---
name: factorio-docs
description: 查询 Factorio 游戏的 Lua API 文档、Mod 开发指南和游戏机制。适用于编写 Factorio Mod、查询 API 类/方法/属性/事件等任务。使用本地 FTS5 索引，无需网络。
allowed-tools: Bash
---

# Factorio 文档 Skill

## 激活时：确认索引可用

**每次激活本 skill 时，先执行以下检查：**

```bash
python scripts/build_index.py
```

`build_index.py` 默认检测到 `references/docs.db` 已存在时直接跳过，不重建；仅当 db 缺失时才从本地 `references/runtime-api.json` 重建（无需网络，约 1 秒）。确认输出 "Already exists" 或 "Built docs.db" 后再进行查询。

## 快速开始

所有查询通过本地索引完成，**不需要联网**：

```bash
cd <skill_root>

# 全文搜索（BM25 排序，支持 porter stemming）
python scripts/search.py "inventory insert"

# 只搜索某种类型
python scripts/search.py --kind event "player built"
python scripts/search.py --kind method "fluid"
python scripts/search.py --kind attribute "energy"

# 精确名称查找
python scripts/search.py --exact LuaEntity
python scripts/search.py --exact on_built_entity

# 列出某个类的所有成员
python scripts/search.py --parent LuaEntity
python scripts/search.py --parent LuaEntity --kind method

# 增加返回数量 / 显示完整描述
python scripts/search.py --top 30 "circuit network"
python scripts/search.py --verbose "transport line"
```

## 索引内容（Factorio 2.0.76 / API v6）

| 类型 | 数量 |
|------|------|
| class + method + attribute | 3411 |
| event | 219 |
| concept | 418 |
| define | 147 |
| **合计** | **4195** |

## 索引维护

查询时完全离线。更新只在需要时显式触发：

```bash
# 检查远端版本，有新版才重建索引（Factorio 游戏更新后运行）
python scripts/update.py
```

`update.py` 会比对本地缓存的 `application_version` 和远端版本，版本相同则直接退出，不重建。

## 搜索技巧

- 多词查询自动用 OR 合并：`"get inventory"` 匹配含任意词的文档
- porter stemmer 自动处理词形变化：`insert` 也能匹配 `inserting`、`inserted`
- 先用 `--exact` 确认类名，再用 `--parent` 查成员
- 事件用 `--kind event`，减少噪音
- 结果按 BM25 相关度降序，第一条通常最准

## 常用 API 速查

| 目的 | 命令 |
|------|------|
| 操作实体 | `--exact LuaEntity` 或 `--parent LuaEntity --kind method` |
| 操作背包 | `--exact LuaInventory` |
| 地图操作 | `--exact LuaSurface` |
| 玩家对象 | `--exact LuaPlayer` |
| 注册事件 | `--kind event "<关键词>"` |
| 查看定义 | `--kind define "<关键词>"` |

## 文档资源（参考用，非运行时依赖）

完整 URL 列表见 `references/api_urls.md`。
