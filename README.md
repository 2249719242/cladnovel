# Webnovel Writer

[![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Compatible-purple.svg)](https://claude.ai/claude-code)

<a href="https://trendshift.io/repositories/22487" target="_blank"><img src="https://trendshift.io/api/badge/repositories/22487" alt="lingfengQAQ%2Fwebnovel-writer | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

> **关于本仓库**：本仓库是 [lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer)（GPL v3）的 fork。
> 在上游基础上新增能力：**审查教训自更新**、**确定性反 AI 扫描**、**小卷（故事弧）规划层**、
> **人机分工改造**（剧情人主导 + 章纲批审 + 任务书确认门）、**作者风格画像**（`/webnovel-style` + 确定性风格指纹）（详见 [docs/guides/](docs/guides/)）。
> 本仓库同样遵循 GPL v3；原作者版权与许可见 [LICENSE](LICENSE)，致谢见文末。

## 这是什么？

`Webnovel Writer` 是一个基于 Claude Code 的长篇网文创作系统。

它的目标很简单：**让 AI 在写长篇小说时不乱编、不忘事**。

系统会自动管理角色设定、剧情伏笔、世界观规则，让你可以安心连载几百章而不用担心前后矛盾。

📖 详细文档在 `docs/` 目录：

- [架构与模块](docs/architecture/overview.md) — 系统怎么工作的
- [命令详解](docs/guides/commands.md) — 所有可用命令
- [RAG 与配置](docs/guides/rag-and-config.md) — 检索和环境变量配置
- [题材模板](docs/guides/genres.md) — 37 个内置网文题材
- [运维与恢复](docs/operations/operations.md) — 项目结构与日常运维
- [插件发版](docs/operations/plugin-release.md) — 发版流程
- [文档导航](docs/README.md) — 所有文档索引

## 快速开始

### 1) 安装插件

通过 Claude Code 官方 Marketplace 安装：

```bash
claude plugin marketplace add 2249719242/cladnovel --scope user
claude plugin install webnovel-writer@webnovel-writer-marketplace --scope user
```

> 如果只想在当前项目生效，把 `--scope user` 改成 `--scope project`。

安装/更新后在 Claude Code 会话里运行 `/reload-plugins`（或重启 Claude Code）使插件生效。

#### 更新插件

第三方 marketplace 默认**不会**自动更新。手动更新两步：

```bash
# 1. 刷新 marketplace 目录（拉取最新版本信息）
/plugin marketplace update webnovel-writer-marketplace

# 2. 重新加载插件
/reload-plugins
```

若版本没有跟上，可以重装一次：

```bash
/plugin uninstall webnovel-writer@webnovel-writer-marketplace
/plugin install webnovel-writer@webnovel-writer-marketplace
```

也可以开启自动更新：`/plugin` → **Marketplaces** → 选中本 marketplace → **Enable auto-update**，之后每次启动 Claude Code 会自动拉新并提示 `/reload-plugins`。

> 插件更新不会动你的书项目数据（`.webnovel/`、`.story-system/`、大纲、正文都在你自己的项目目录里）。但 Python 依赖可能有新增，更新后建议重跑一次第 2 步。

### 2) 安装 Python 依赖

```bash
python -m pip install -r https://raw.githubusercontent.com/2249719242/cladnovel/HEAD/requirements.txt
```

### 3) 初始化小说项目

在 Claude Code 中输入：

```bash
/webnovel-init
```

系统会引导你填写书名、题材、主角等信息，然后在当前工作区下创建项目目录。

### 4) 配置 RAG（必做）

进入书项目根目录，把配置模板复制为 `.env` 并填写 API Key：

```bash
cp .env.example .env
```

最小配置：

```bash
EMBED_BASE_URL=https://api-inference.modelscope.cn/v1
EMBED_MODEL=Qwen/Qwen3-Embedding-8B
EMBED_API_KEY=your_embed_api_key

RERANK_BASE_URL=https://api.jina.ai/v1
RERANK_MODEL=jina-reranker-v3
RERANK_API_KEY=your_rerank_api_key
```

### 5) 开始写作

```bash
/webnovel-plan 1      # 规划第 1 卷大纲（方向你拍板，章纲分批生成、每批等你确认）
/webnovel-write 1     # 写第 1 章（任务书先给你确认再起草；加 --auto 跳过确认全自动）
/webnovel-review 1-5  # 审查第 1-5 章
/webnovel-style       # 可选：用目标作者的样本文本建风格画像，写章自动模仿其文笔
```

**人机分工**（v6.1 起的默认协作方式）：剧情方向、关键反转、每章情节约束由你主导——规划时 AI 只提案不拍板，章纲按批暂停人审，写章前任务书需你确认（写章指令里可直接给本章情节/场景要点，优先级最高）；设定完善、一致性校验、状态管理则交给 AI 全力发挥。

### 6) 可视化面板（可选）

```bash
/webnovel-dashboard
```

只读面板，可以浏览项目状态、实体图谱、章节内容和追读力数据。前端已随插件预构建，不需要本地 `npm build`。

## Story System 主链（Phase 5）

当前默认链路已经切到：

1. 写前读取 `.story-system/MASTER_SETTING.json`、`volumes/`、`chapters/`、`reviews/`
2. 写后提交 accepted `CHAPTER_COMMIT`
3. 由 commit projection writers 更新 `.webnovel/state.json`、`index.db`、`summaries/`、`memory_scratchpad.json`

这意味着：

- `.story-system/` 是主链真源
- `.webnovel/*` 是投影 / read-model
- `references/genre-profiles.md` 只在合同缺失时作为 fallback
- `preflight --format json` 和 dashboard 会直接暴露 `story_runtime` 健康状态

### 7) Agent 模型设置（可选）

所有内置 Agent 默认继承当前会话模型：

```yaml
model: inherit
```

如需单独指定，编辑对应 `agents/*.md` 的 frontmatter：

```yaml
---
model: sonnet  # 可选：inherit / sonnet / opus / haiku
---
```

## 更新简介

| 版本 | 主要变化 |
|------|----------|
| **main（待发版）** | 人机分工改造（方向决策门 + 章纲批审制 + 任务书确认门 + 人工简报）；`/webnovel-style` 作者风格画像 + 确定性风格指纹；`chapter-commit --resume` 投影补跑；场景切片收束至 commit 投影链；修复 Python 3.10 兼容、event_id 校验崩溃、genre-profiles 死路径；新增测试 CI |
| **v6.0.0 (当前)** | Story System 全链路上线（合同种子 + 运行时合同 + 章节提交 + 事件审计），补齐集成测试 |
| **v5.5.5** | 长期记忆闭环：写前注入 + 写后沉淀，新增 `memory` 运维命令 |
| **v5.5.4** | 写作链提示词强约束，统一中文化审查和报告文案 |
| **v5.5.3** | 统一 `preflight` 预检命令，修复 Windows 终端编码问题 |
| **v5.5.2** | 大纲章节名同步到正文文件名 |
| **v5.5.1** | 修复卷级大纲上下文提取，补齐 Dashboard 和 Learn 命令文档 |
| **v5.5.0** | 新增只读可视化 Dashboard，支持实时刷新 |
| **v5.4.4** | 接入 Plugin Marketplace 安装机制 |
| **v5.4.3** | 增强 RAG 智能上下文（`auto/graph_hybrid` 回退 BM25） |
| **v5.3** | 引入追读力系统（Hook / Cool-point / 微兑现 / 债务追踪） |

## 开源协议

本项目使用 `GPL v3` 协议，详见 [LICENSE](LICENSE)。

## Star 历史

[![Star History Chart](https://api.star-history.com/svg?repos=lingfengQAQ/webnovel-writer&type=Date)](https://star-history.com/#lingfengQAQ/webnovel-writer&Date)

## 致谢

本项目使用 **Claude Code + Gemini CLI + Codex** 配合 Vibe Coding 方式开发。  
灵感来源：[Linux.do 帖子](https://linux.do/t/topic/1397944/49)

感谢 `oh-story-claudecode` 提供拆文流程参考。

## 贡献

欢迎提交 Issue 和 PR：

```bash
git checkout -b feature/your-feature
git commit -m "feat: add your feature"
git push origin feature/your-feature
```
