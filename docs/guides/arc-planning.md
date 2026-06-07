# 小卷（故事弧）规划层

> 在"卷（~50 章）"和"章"之间新增中间层——**小卷（故事弧 / arc）**，每个小卷是一个完整子冲突（起→升级→兑现），并就近规划该弧的**新登场人物**与**关键场景**。写作时把"当前小卷"上下文注入任务书。

## 解决的问题

1. **卷纲没有结构化的场景/人物**：原 `卷纲骨架` 只有一行"关键人物与反派层级"，`VolumeBrief.selected_scenes` 是运行时从章节拼的字符串，不是规划产物；场景/人物真源是全局 `设定集/*.md`，不按剧情段组织。
2. **卷→章跨度过大**：卷默认 ~50 章，从"整卷节拍表"直接跳到"单章章纲"，缺少中段结构，也不利于上下文聚焦。

这两个问题其实是一件事：缺失的"场景/人物"正应放在小卷这一层（卷级太宽、章级太细）。

## 数据模型

每卷一份小卷规划，**结构化 JSON 为真源**，MD 为人类可读渲染：

- `大纲/第{V}卷-小卷规划.json` — 程序读取，供"章→弧映射"与"写前注入"
- `大纲/第{V}卷-小卷规划.md` — 由 `arc render-md` 渲染，勿手写

JSON 结构：

```json
{
  "volume": 1,
  "arcs": [
    {
      "arc_id": "v1-a1", "name": "新手村崛起",
      "chapter_start": 1, "chapter_end": 12,
      "goal": "废材展露锋芒", "core_conflict": "退婚+家族内斗",
      "arc_climax": "宗门选拔夺魁打脸",
      "new_characters": [{"name": "叶良辰", "role": "前期小反派", "note": "退婚挑衅者"}],
      "key_scenes": [{"name": "林家祠堂", "function": "羞辱发生地"}],
      "foreshadow": [{"content": "玉佩发光", "action": "埋", "note": "上古血脉线索"}]
    }
  ]
}
```

约束：各小卷区间**连续、无空档、无重叠**，合并后完整覆盖本卷章节范围；建议 8–15 章/弧（弹性，仅校验提醒）。

## 工作机制

```
webnovel-plan
  └─ Step 6.5（卷纲骨架之后、批量章纲之前）
       ├─ 把卷拆成若干小卷，每弧产出 目标/弧末高潮/新人物清单/关键场景/伏笔
       ├─ 写入 大纲/第{V}卷-小卷规划.json
       ├─ arc render-md  → 渲染 .md
       └─ arc validate   → ok=true 才进 Step 7（章纲按小卷推进）

写章（extract_chapter_context）
  └─ _load_arc_context(chapter)
       └─ arc_for_chapter：先按 state.volumes_planned 定位卷，再在卷内按区间定位小卷
            └─ payload["arc_context"] = {name, goal, core_conflict, arc_climax,
                                         new_characters, key_scenes, foreshadow}
                 └─ context-agent 折进写作任务书（第2段故事 / 第3段人物 / 舞台约束）
```

**向后兼容**：没有小卷规划文件的项目，`arc_context` 返回 `{}`，写作主流程照常按整卷信息处理。

## CLI

```bash
webnovel arc list     --volume 1          # 列出某卷的小卷
webnovel arc current  --chapter 14        # 查询某章所属小卷上下文（写前注入用的紧凑包）
webnovel arc validate --volume 1          # 校验区间覆盖/重叠/空档/跨度/必填项
webnovel arc validate --volume 3 --range 101-150   # 显式给卷区间（缺省时从 state 读取）
webnovel arc render-md --volume 1         # 由 JSON 渲染人类可读 MD
```

`validate` 返回 `{ok, errors, warnings}`：`errors`（区间非法/重叠/重复 id/缺 arc_id/空列表）会挡住进入章纲；`warnings`（空档、跨度超 6–18、缺 goal/climax、缺人物/场景清单、首尾不贴合卷边界）只提示。

## 涉及文件

| 文件 | 改动 |
|------|------|
| `scripts/data_modules/arc_planner.py` | 新增：小卷 schema/映射/校验/渲染 + CLI |
| `scripts/extract_chapter_context.py` | 注入 `arc_context`（向后兼容） |
| `scripts/data_modules/webnovel.py` | 注册 `arc` 子命令 |
| `skills/webnovel-plan/SKILL.md` | 新增 Step 6.5、章纲打 arc 标签、Step 9 校验 |
| `agents/context-agent.md` | 消费 `arc_context` 写进任务书 |
| `templates/output/大纲-小卷规划.md` | 新增模板 |
| `scripts/data_modules/tests/test_arc_planner.py` | 新增 19 项单测 |

## 调参与已知限制

- 建议跨度 `8–15 章`（代码里 `ARC_MIN_CHAPTERS=6 / ARC_MAX_CHAPTERS=18` 给了缓冲），是启发式，不是硬门槛；不同题材可调。
- 注入是"软上下文"：`arc_context` 进的是写作任务书，不是 pydantic 运行时合同，**不会**覆盖章纲硬约束（章纲仍是法律）。
- 小卷规划是新增层，旧项目不强制；想用就在对应卷跑一次 Step 6.5。

## 下一步建议（尚未实现）

1. **弧末校验联动**：写到某弧最后一章时，提醒 reviewer 校验"弧末高潮是否兑现"。
2. **弧级追读力**：把追读力指标按小卷聚合，定位是哪一段子剧情掉读者。
3. **人物/场景回写设定集**：小卷的 `new_characters` 可半自动建角色卡，避免和 `设定集/*.md` 重复维护。
