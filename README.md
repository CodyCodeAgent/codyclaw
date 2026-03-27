# CodyClaw

[![CI](https://github.com/CodyCodeAgent/codyclaw/actions/workflows/ci.yml/badge.svg)](https://github.com/CodyCodeAgent/codyclaw/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)

> 基于 Cody Agent Framework 构建的飞书驱动持久化 AI Agent 系统

## 什么是 CodyClaw？

CodyClaw 是一个企业级飞书 AI 工作台，让你直接在飞书里控制 AI Agent：
- 发消息给机器人 → Agent 执行任务 → 实时流式回复
- 群聊 @机器人 触发 Agent
- Cron 定时任务主动推送结果（支持 AI 动态创建定时任务）
- 危险操作需要消息回复确认（human-in-the-loop）
- Web 控制台：实时聊天、配置管理、事件监控

```
飞书消息 → Gateway（FastAPI）→ Cody SDK（AsyncCodyClient）→ LLM → 工具执行 → 飞书回复
```

## 快速开始

### 1. 安装

```bash
pip install -e .
# 或 Docker:
docker compose up -d
```

### 2. 启动

```bash
codyclaw
```

首次启动时没有配置文件，CodyClaw 会自动进入 **Setup 模式**：

1. 打开浏览器访问 `http://localhost:8080`
2. 按向导填写飞书应用凭证、API Key、模型选择
3. 点击 **Save & Start** → 自动保存配置并重启
4. 重启后 Dashboard 显示 **Feishu Connected** 即可使用

> 无需手动创建配置文件。向导会引导你完成所有步骤。

### 3. 飞书应用准备

1. 前往 [飞书开放平台](https://open.feishu.cn/) 创建企业自建应用
2. 开启「机器人」能力，连接方式选择 **WebSocket**（长连接，无需公网 IP）
3. 配置权限：
   - `im:message`（收发消息）
   - `im:resource`（上传下载文件）
   - `contact:user.base:readonly`（获取用户名，可选）
4. 记录 App ID、App Secret、机器人的 open_id

## 使用说明

| 场景 | 操作 |
|------|------|
| 单聊 | 直接发消息给机器人 |
| 群聊 | @机器人 + 内容 |
| 取消任务 | 发送「取消」 |
| 审批操作 | 回复「允许」/「拒绝」/「全部允许」 |
| 创建定时任务 | 告诉 Agent「每天 9 点帮我做 xxx」 |

> **Human-in-the-loop**：当 Agent 需要执行危险操作时，会发送审批消息，用户回复文字即可完成审批，无需依赖公网回调。

## Web 控制台

启动后访问 `http://localhost:8080/` 打开 Web 管理控制台：

| 页面 | 功能 |
|------|------|
| Dashboard | 概览面板 — 飞书连接状态、Agent 数、会话数、Cron 状态 |
| Chat | 实时对话 — 选择 Agent，SSE 流式响应，欢迎引导 |
| Agents | 查看所有 Agent 配置（模型、工作目录） |
| Skills | 浏览 SKILL.md 内容 |
| Cron Tasks | 定时任务列表、调度表达式、下次执行时间 |
| Sessions | 活跃会话列表 |
| Config | 快捷编辑（API Key、模型、端口）+ 完整配置只读视图 |
| Events | 实时事件流（Agent 运行、Cron 执行等） |

## Skills

内置技能包，Agent 可直接调用：

| Skill            | 说明                         |
|------------------|------------------------------|
| `feishu-notify`  | 主动向指定飞书会话发送消息   |
| `cron-manager`   | 动态创建、查看、删除定时任务 |

Skills 目录：`codyclaw/skills/`，每个 skill 为一个 `SKILL.md` 文件。

## 管理 API

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查（含飞书连接状态） |
| `GET /api/dashboard` | 仪表盘概览数据 |
| `GET /api/agents` | Agent 列表 |
| `GET /api/cron` | 定时任务列表 |
| `GET /api/sessions` | 活跃会话列表 |
| `GET /api/skills` | Skill 列表及内容 |
| `GET /api/config` | 当前配置（敏感字段掩码） |
| `PUT /api/config` | 更新配置 |
| `PUT /api/config/quick` | 快捷更新 API Key / Model |
| `POST /api/chat/send` | Web 聊天（SSE 流式响应） |
| `GET /api/chat/history` | 聊天历史 |
| `GET /api/events/stream` | 实时事件流（SSE） |
| `GET /api/setup/status` | 配置状态检查 |
| `POST /api/setup/save` | 保存初始配置 |
| `POST /api/setup/test-lark` | 测试飞书凭证 |

## 项目结构

```
codyclaw/
├── main.py              # Gateway 主入口（FastAPI + 生命周期 + setup mode）
├── config.py            # 配置加载（YAML + ${ENV_VAR} 展开 + setup 检测）
├── db.py                # SQLite（cron_tasks + chat_messages）
├── channel/             # 飞书渠道适配层
│   ├── base.py          # 抽象接口
│   ├── lark_impl.py     # lark-oapi SDK 实现（WebSocket）
│   ├── cards.py         # 交互卡片模板
│   └── dedup.py         # 消息去重
├── gateway/             # 调度层
│   ├── router.py        # 消息路由（用户/群 → Agent）
│   ├── dispatcher.py    # 执行调度（流式输出 + human-in-the-loop）
│   ├── session_strategy.py  # 会话生命周期管理
│   └── tools.py         # Custom Tools（cron 管理等）
├── automation/          # 自动化引擎
│   ├── cron.py          # 定时任务（APScheduler 3.x）
│   ├── events.py        # 事件总线
│   └── boot.py          # 启动脚本（BOOT.md）
├── web/                 # Web 控制台
│   ├── api.py           # 控制台 API（setup、chat、config、events）
│   └── static/          # 前端 SPA（HTML + CSS + JS）
└── skills/              # Skill 包
    ├── feishu-notify/
    └── cron-manager/
```

## 生产部署

### Docker（推荐）

```bash
docker compose up -d
```

仓库自带 `Dockerfile` 和 `docker-compose.yml`，开箱即用。

### systemd

```ini
[Unit]
Description=CodyClaw AI Agent Gateway
After=network.target

[Service]
Type=simple
User=codyclaw
EnvironmentFile=/opt/codyclaw/.env
ExecStart=/opt/codyclaw/.venv/bin/codyclaw
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## 开发

```bash
make dev      # 安装开发依赖
make check    # 运行 lint + tests
make lint     # 仅 lint
make test     # 仅 tests
```

或手动执行：
```bash
ruff check codyclaw/
pytest tests/ -v
```

详细技术设计见 [docs/TECH_DESIGN.md](docs/TECH_DESIGN.md)。

## 贡献

欢迎参与贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解开发流程和规范。

## License

[Apache License 2.0](LICENSE)
