# 全面体检与优化建议（2026-06-11）

> 审查范围：7 个 skills、4 个 agents、scripts/（约 2.5 万行）、data_modules、dashboard、测试与 CI。
> 验证方法：通读 prompt 层与核心代码，在 Linux + Python 3.10 上实际跑通全部测试（约 570 个），并用最小复现脚本验证了关键 bug。

## 总体评价

Phase 1–5 的收束基本兑现了：`.story-system` 合同树 + CHAPTER_COMMIT 主链是真实存在的 SSOT，事后投影（state/index/summary/memory/vector）有失败隔离；确定性反 AI 扫描、审查教训自更新、小卷规划三个 fork 新增能力设计扎实；测试规模大且有 90% 覆盖率门槛。当前的主要风险不在大架构，而在三处会真实触发的 bug、几处 prompt 层与代码层的契约错位，以及缺少测试 CI 导致这些问题没被拦住。

## 问题总表

| 编号 | 严重度 | 位置 | 一句话描述 |
|------|--------|------|-----------|
| P0-1 | 高 | `scripts/conftest.py:57` | Python 3.10/3.11 下整个测试套件报错（3.12 专属参数） |
| P0-2 | 高 | `event_log_store.py` + `data-agent.md` | accepted_events 缺 `event_id` 时投影链整体崩溃 |
| P0-3 | 高 | `context_manager.py:332`、`memory_contract_adapter.py:236` | genre-profiles 读取路径在插件安装下永远不存在，静默失效 |
| P1-4 | 中 | `reviewer.md` vs `webnovel-write/SKILL.md` | reviewer 被要求落盘 JSON 但没有 Write 工具 |
| P1-5 | 中 | `chapter_commit.py` | 「projection 失败只补跑失败项」没有对应命令 |
| P1-6 | 中 | `data-agent.md` Step E | data-agent 直写向量索引，与边界声明和 VectorProjectionWriter 职责冲突 |
| P1-7 | 中 | commit 链 | 大纲履约仍由 data-agent 自评，无确定性校验兜底 |
| P2-8 | 中 | `.github/workflows/` | 没有任何测试 CI，发版流程不跑 pytest |
| P2-9 | 低 | agents frontmatter | 全部 `model: inherit`，无法为机械型 agent 降配省成本 |
| P2-10 | 低 | `state_manager.py` | JSON+SQLite 双写遗留复杂度，缺少明确退役计划 |
| P2-11 | 低 | 杂项 | 命名/体积/本机 hack 等工程卫生问题 |

## P0：会真实触发的 bug

### P0-1 测试套件在 Python 3.10/3.11 上全线报错

`conftest.py` 的 `_SafeTemporaryDirectory.__init__` 向 `tempfile.TemporaryDirectory` 传 `delete=True`，该参数 3.12 才引入。README 和 requirements 声称支持 Python 3.10+，但实测 3.10 下 36 个测试因此 fail/error，所有用 `tempfile.TemporaryDirectory` 的用例全军覆没。修复很小：按 `sys.version_info >= (3, 12)` 条件传参（我已在沙箱验证，修复后约 570 个测试全绿）。这条同时暴露了 P2-8——有测试 CI 矩阵的话第一天就会被发现。

### P0-2 缺 `event_id` 会让整条投影链崩溃

`StoryEvent` schema 要求每个事件必须有 `event_id`，但 `data-agent.md` 第 7.1 节的字段硬性约定逐项列了 `event_type`、`subject`、各 payload 字段，唯独没提 `event_id`。data-agent 按自己的 spec 输出时大概率不带它。而 `ChapterCommitService.apply_projections` 里 `EventLogStore.write_events` 在逐 writer 的 try/except 之外，一旦 pydantic 校验失败，整个投影阶段直接抛异常退出，commit 文件停留在全 pending 状态（已用最小脚本复现 `ValidationError: event_id Field required`）。

建议修复：在 `_normalize_events` 里自动生成确定性 `event_id`（如 `sha1(chapter + event_type + subject + payload)` 截断），缺失时兜底而不是报错。确定性 ID 还有附带收益——SQLite 镜像的 `INSERT OR IGNORE ... event_id UNIQUE` 在重跑 commit 时才真正幂等，否则每次重跑生成新 ID 会导致事件重复入库。同时把 `write_events` 移入失败隔离范围，并在 data-agent.md 7.1 补充该字段说明。

### P0-3 genre-profiles 在插件安装下是死路径

`context_manager.py` 和 `memory_contract_adapter.py` 都从 `{project_root}/.claude/references/genre-profiles.md` 读题材画像（taxonomy 同理），但全仓库没有任何代码或 skill 步骤会把这个文件放到书项目下——它实际位于 `${CLAUDE_PLUGIN_ROOT}/references/`。这是项目模板时代的遗留路径。后果是 `load-context` 的 `genre_profile_excerpt` 永远为空且被 try/except 静默吞掉，context-agent 文档里承诺的「基础包含 genre_profile_excerpt」实际从未兑现。建议参照 `reference_search.py` 的做法用 `Path(__file__)` 相对插件根解析，保留项目级同名文件作为可选覆盖。

## P1：契约错位与健壮性

### P1-4 reviewer 的落盘指令与工具集不符

`webnovel-write` Step 3 的 prompt 要求 reviewer「保存到 `.webnovel/tmp/review_results.json`」，但 `reviewer.md` 的 tools 只有 Read/Grep/Bash，且其输出格式一节写的是「严格按 JSON 输出（无其他文本）」，只字未提写文件。结果 reviewer 只能用 Bash heredoc 写含中文和嵌套引号的 JSON——在 Windows 上尤其脆弱。两个方案选一：给 reviewer 加 Write 工具并在 reviewer.md 明确落盘步骤；或让主流程接收 JSON 后自己落盘。前者更符合现有 skill 流程。

### P1-5 「只补跑失败的 projection」没有命令支撑

SKILL.md 的失败隔离策略写了「projection 失败→只补跑失败项」，但 `chapter_commit.py` 只有一条全量路径：重建 payload（要求四份 tmp artifacts 仍在）→ 重置全部 projection 为 pending → 重跑所有 writer。建议加 `chapter-commit --resume --chapter N`：读取已持久化的 `chapter_{N}.commit.json`，只对 `failed:*` / `pending` 的 writer 重跑。这同时解决另一个隐患——进程在 apply 中途崩溃时，commit 文件停留在 pending，目前没有干净的恢复入口。落地前需顺手核验各 writer 的幂等性（memory writer 是否会重复追加值得测一下）。

### P1-6 向量索引的双写边界不清

data-agent.md 的边界声明是「不直接写 state/index/summaries/memory」——漏了 vector；而它的 Step E 又要求「场景切片 → RAG 向量索引」直写。同时 commit 链里还有 `VectorProjectionWriter` 负责把事件/实体增量写向量库。两条写入路径并存，违背了「LLM 只产 artifacts、投影统一由 commit 链落地」的设计原则。建议把场景切片也收进 extraction_result，由 VectorProjectionWriter（或一个明确的 post-commit 步骤）统一写入。

### P1-7 大纲履约校验仍是 LLM 自评

诊断报告第 6 条只算半闭环：`fulfillment_result` 由 data-agent 自己判断 covered/missed nodes，模型完全可以「自评全覆盖」。建议在 `chapter_commit` 里加一层廉价的确定性兜底：对 `must_cover_nodes` 做关键词/锚点匹配，匹配不到但 LLM 声称 covered 的节点降级为 warning 写入 commit，供 reviewer 或人工复核。不必追求精确语义匹配，目的是让「漏写关键节点」不再零成本。

## P2：工程卫生与成本

### P2-8 没有测试 CI

`.github/workflows/` 只有发版和版本号两条流水线，均不跑 pytest。建议加一条 test workflow：matrix 覆盖 ubuntu + windows × Python 3.10/3.12，跑 `pytest --no-cov`（覆盖率门槛留给单独 job 或本地）。P0-1 这类问题会在第一次 push 就被拦住。

### P2-9 agent 模型不可配置

四个 agent 全是 `model: inherit`。data-agent 的工作（实体提取、字段映射、摘要）是机械型任务，用 sonnet 级模型足够，每章可省一次主模型调用的成本与延迟。建议在 frontmatter 给 data-agent（或经评测后给 reviewer）指定较低档模型，或至少在文档里说明可改。

### P2-10 state.json + SQLite 双写遗留

`StateManager`（1452 行）内部仍维护 `_sync_to_sqlite` 双写与 pending 快照恢复，这是诊断报告第 7 条的「内层风险」，目前靠 state.json 降级为 projection/read-model 来兜底。建议明确退役计划：当所有消费端（包括 dashboard 与 status_reporter）都改走 memory-contract / index.db 后，把 state.json 降级为纯导出产物，删除双写同步逻辑。

### P2-11 杂项

`scripts/webnovel.py`（36 行入口）与 `data_modules/webnovel.py`（529 行 CLI）重名易混；`status_reporter.py` 1248 行可按 dashboard 数据域拆分；`sitecustomize.py` 注释明写「this Windows machine」，本机 hack 不宜入库，可移到文档说明；`pytest.ini` 用 `-p no:asyncio` 禁用插件自动加载再靠环境侧补偿，换机器即碎（本次沙箱复现需手动 `-p asyncio`），建议改为显式声明 `asyncio_mode` 的标准配置。合同文件名 `chapter_{NNN}`（3 位）与正文 `第{NNNN}章`（4 位）并存不致错但增加认知负担，可在文档里点明。

## 建议实施顺序

第一批（小改动、高收益、互不依赖）：P0-1、P0-2、P0-3、P1-4，外加 P2-8 的 test CI，预计一次 PR 完成。第二批：P1-5 的 `--resume` 与 writer 幂等性测试、P1-6 向量写入收束。第三批：P1-7 履约兜底校验、P2-9 模型配置。P2-10/11 放入长期 backlog。

## 实施状态（2026-06-11 更新）

第一批已完成：P0-1/P0-2/P0-3/P1-4 全部修复，新增 `tests.yml` CI（ubuntu/windows × py3.10/3.12），并将 `pytest.ini` 的 `-p no:asyncio` 改为 `-p asyncio` 显式加载（原配置在干净环境下会让全部 async 测试失败）。

第二批已完成：P1-5 新增 `chapter-commit --resume`（`ChapterCommitService.resume_projections` 只补跑 failed/pending 的 writer，done/skipped 不重复执行，结果回写 commit 文件；SKILL.md 5.4 已同步）。P1-6 场景切片收束——data-agent 不再直写 `rag index-chapter`，改为输出 `extraction_result.scene_chunks`，由 commit 链的 `VectorProjectionWriter` 统一写入（摘要 + 场景 + 事件 + 实体增量），router 在有 scene_chunks/summary 时强制 vector writer。顺带修复一个隐患：event/entity_delta 向量 chunk 此前没有 chunk_id，全部按 `ch{N}_s0` 兜底互相覆盖，现在使用确定性唯一 ID（`ch{N}_evt{i}` / `ch{N}_ed{i}`），重跑/resume 幂等。

两批合计新增 12 个测试，全量约 580 个测试在 Linux + Python 3.10 验证通过。遗留：P1-7、P2-9、P2-10、P2-11。
