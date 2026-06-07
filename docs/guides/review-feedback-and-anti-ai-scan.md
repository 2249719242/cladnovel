# 审查反馈自更新 & 确定性反 AI 扫描

> 本次更新围绕一个主题：**把"审查发现的问题"从一次性报告，变成可复用、会自更新的写作约束**，并把原先靠模型自查的"反 AI 词频"下沉为确定性代码。
>
> 涉及两个独立但同源的能力：
> 1. **审查教训库（review lessons）**——审查 → 记忆 → 下章自动规避的闭环。
> 2. **确定性反 AI 扫描器（anti-ai-scan）**——词库唯一真源 + 代码扫描，替代"模型通读 200 词清单自己数"。

---

## 1. 审查教训库（review lessons）

### 解决的问题

此前只有 `ai_flavor` 类问题会在审查后回流到 `.story-system/anti_patterns.json` 并注入下一章写作。其余高严重度问题（连贯性 / 设定 / 角色 / 时间线 / 逻辑 / 节奏）**只进审查报告和 `index.db` 指标，写作端学不到**，于是同类矛盾会反复出现。

### 工作机制

```
webnovel review-pipeline
  └─ append_review_lessons(project_root, result)
       ├─ 仅收录 high / critical 问题（ai_flavor 除外，它走 anti_patterns）
       ├─ 按 (类别, 归一化文本) 去重
       └─ 写入 .story-system/review_lessons.json（顶层 list，与 anti_patterns 同构）

下一章写作
  └─ RuntimeContractBuilder._load_review_lesson_rows()
       └─ lessons_for_injection()  # active、按 (严重度 × 复现次数) 排序、Top 10
            └─ 并入写作合同 anti_patterns 通道（零新增 schema / prompt 接线）
```

**自更新**体现在：同类问题再次出现时不新增条目，而是 `occurrences += 1`、刷新 `last_chapter`、严重度取更高值——反复踩的坑获得更高注入权重。

教训条目形如：

```
【审查教训·连贯性】主角用了第3章已失去的能力（修复方向：复查能力状态）（已重复 2 次，务必规避）
```

### 维护命令

```bash
webnovel review-lessons stats                 # 统计（总数 / active / resolved / 分类 / 分级）
webnovel review-lessons list --status active  # 列出（支持 --category / --limit）
webnovel review-lessons resolve --id <id>     # 标记已解决，停止注入
webnovel review-lessons reopen  --id <id>     # 重新激活
```

### 涉及文件

| 文件 | 改动 |
|------|------|
| `scripts/data_modules/review_lessons.py` | 新增：核心逻辑 + CLI |
| `scripts/review_pipeline.py` | 审查落库时调用 `append_review_lessons` |
| `scripts/data_modules/runtime_contract_builder.py` | 写前把教训并入避雷项 |
| `scripts/data_modules/webnovel.py` | 注册 `review-lessons` 子命令 |
| `skills/webnovel-review/SKILL.md` | 文档说明 |

---

## 2. 确定性反 AI 扫描器（anti-ai-scan）

### 解决的问题

原先反 AI 检测有三个结构性缺陷：

1. **靠模型自查词频**——polish-guide 把 200+ 词清单整本塞进上下文让模型自己数，而模型恰恰最不擅长精确计数；
2. **词库四处重复**——同一份词表散落在 `polish-guide.md` / `anti-ai-guide.md` / `style-adapter.md`，迟早各自漂移；
3. **纯词法黑名单易被绕过（Goodhart）**——禁了"缓缓开口"，模型改写"不疾不徐地开口"，病没变只换了词。

### 工作机制

- **词库唯一真源**：`scripts/data_modules/data/anti_ai_lexicon.json`（A–N 共 14 类高频套话 + 阈值 + said-tag 模式）。各 md 只保留原则与示例，不再抄词表。
- **词法层**：按类别统计命中数 / 千字密度，超阈值类别给出"命中样例 + 修复方向"。高频常用词类别（逻辑连接、抽象词）标 `advisory`，只提醒不计入硬风险。
- **结构层（反 Goodhart）**：不依赖具体词，抓"换词不换病"的句式问题——句长方差、短句占比、单句成段比、重复 4-gram、said-tag 占比。
- **只回吐命中**：模型不再通读词典，只收到 `findings`（自己这章的命中 + 修复方向），省 token，也不再"背违禁清单"。

### 使用

```bash
# 离线扫描任意文件（不需要活动项目）
webnovel anti-ai-scan --file 正文/第0042章.md

# 从项目按章号解析
webnovel anti-ai-scan --chapter 42
```

输出结构（摘要）：

```json
{
  "summary": {
    "flagged_lexicon_categories": ["L", "F", "K"],
    "structure_flags": ["said_tag_overuse", "sentence_len_uniform"],
    "ai_flavor_risk": "high"
  },
  "findings": [
    {"type": "lexicon", "category": "L", "detail": "万能副词密度 ...", "hint": "删掉副词，用前置动作..."},
    {"type": "structure", "category": "said_tag_overuse", "detail": "...", "hint": "改用前置动作替代"}
  ]
}
```

### 接入写作流程

`skills/webnovel-write/SKILL.md` Step 4（润色）的 Anti-AI 终检改为：**先跑 `anti-ai-scan` 定位命中，再据 `findings` 修复**；`ai_flavor_risk=high` 必须改到 medium 以下才判 `anti_ai_force_check`。

### 涉及文件

| 文件 | 改动 |
|------|------|
| `scripts/data_modules/data/anti_ai_lexicon.json` | 新增：词库 + 阈值单一真源 |
| `scripts/data_modules/anti_ai_scanner.py` | 新增：扫描器 + CLI |
| `scripts/data_modules/webnovel.py` | 注册 `anti-ai-scan`（支持离线 `--file`，不强依赖活动项目）|
| `skills/webnovel-write/SKILL.md` | Step 4 接入扫描 |
| `skills/webnovel-write/references/polish-guide.md` | 指向词库真源与扫描器 |
| `skills/webnovel-write/references/anti-ai-guide.md` | 指向词库真源 |

---

## 调参与已知限制（务必阅读）

- **阈值是启发式默认值，不是实证常数**。`density_per_1k` 与 `structural_thresholds` 都在 JSON 里，建议按**题材 / 作者文风**校准——快节奏都市文与古言对短句占比的合理区间差别很大。
- **结构层是 advisory 信号**，尤其 `repeated_ngram` 会把高频角色名也算进去，需结合人工判断，不要当硬门槛。
- **词法仍是词法**：扫描器降低了"绕词"的收益（结构层会抓），但不能根除。真正的对抗手段是**正例改写**（polish-guide 末尾示例）+ 跨章多样性，而非无限堆词。
- **教训库的 Top 10 注入上限**写死在 `DEFAULT_INJECT_LIMIT`；问题极多的书可考虑做成 `.env` 可配或分题材取值。

## 下一步建议（尚未实现）

1. **闭环验证**：对"开/关某条规则"做 A/B，用 `anti-ai-scan` 的 risk、reviewer 的 ai_flavor issue 数、追读力评分衡量，**淘汰不产生增益的规则**——像对待代码一样给规则配测试。
2. **词库按本书自更新**：让全局词库当种子，靠 `review_lessons` + `anti_patterns` 让每本书长出自己的高频病灶，并淘汰从不命中的死词。
3. **阈值挂到 genre-profile**：把结构层阈值分题材取值，避免一把尺子压平所有文风。

## 测试

- `scripts/data_modules/tests/test_review_lessons.py`（12 项）
- `scripts/data_modules/tests/test_anti_ai_scanner.py`（12 项）
- 相关回归（`test_review_schema` / `test_runtime_contract_builder` / `test_webnovel_unified_cli` / `test_prompt_integrity`）全部通过；两个新模块覆盖率 92% / 95%。
