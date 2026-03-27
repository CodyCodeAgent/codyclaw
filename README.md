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

```
飞书消息 → Gateway（FastAPI）→ Cody SDK（AsyncCodyClient）→ LLM → 工具执行 → 飞书回复
```

## 快速开始

### 1. 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. 创建飞书自建应用

1. 前往 [飞书开放平台](https://open.feishu.cn/) 创建企业自建应用
2. 开启「机器人」能力，连接方式选择 **WebSocket**（长连接，无需公网 IP）
3. 配置权限：
   - `im:message`（收发消息）
   - `im:resource`（上传下载文件）
   - `contact:user.base:readonly`（获取用户名，可选）
4. 记录 App ID、App Secret、机器人的 open_id

### 3. 配置

配置文件默认位置为 `~/.codyclaw/config.yaml`：

```bash
mkdir -p ~/.codyclaw
cp config.yaml.template ~/.codyclaw/config.yaml
# 编辑，填入飞书应用信息和 Agent 配置
```

关键配置项：

```yaml
lark:
  app_id: "cli_xxx"       # 飞书 App ID
  app_secret: "xxx"       # 飞书 App Secret
  bot_open_id: "ou_xxx"   # 机器人自身的 open_id

agents:
  - agent_id: "assistant"
    name: "通用助手"
    workdir: "/path/to/workspace"   # Agent 工作目录
    model: "claude-sonnet-4-20250514"
    trigger_mode: "all"             # p2p 直接响应

cody:
  model_api_key: "${ANTHROPIC_API_KEY}"  # 支持 ${ENV_VAR} 引用
```

### 4. 启动

```bash
export ANTHROPIC_API_KEY="sk-ant-xxx"
codyclaw
# 或: python -m codyclaw.main
```

启动后数据目录自动初始化：

```
~/.codyclaw/
  config.yaml
  codyclaw.db          # App 数据库（AI 动态创建的定时任务等）
  agents/
    {agent_id}/
      cody.db          # Cody 内部数据（会话、记忆）
```

## 使用说明

| 场景 | 操作 |
|------|------|
| 单聊 | 直接发消息给机器人 |
| 群聊 | @机器人 + 内容 |
| 取消任务 | 发送「取消」 |
| 审批操作 | 回复「允许」/「拒绝」/「全部允许」 |
| 创建定时任务 | 告诉 Agent「每天 9 点帮我做 xxx」 |

> **Human-in-the-loop**：当 Agent 需要执行危险操作时，会发送审批消息，用户回复文字即可完成审批，无需依赖公网回调。

## Skills

内置技能包，Agent 可直接调用：

| Skill            | 说明                         |
|------------------|------------------------------|
| `feishu-notify`  | 主动向指定飞书会话发送消息   |
| `cron-manager`   | 动态创建、查看、删除定时任务 |

Skills 目录：`codyclaw/skills/`，每个 skill 为一个 `SKILL.md` 文件，遵循 [Agent Skills Open Standard](https://agentskills.io)。

## 项目结构

```
codyclaw/
├── main.py              # Gateway 主入口（FastAPI + 生命周期管理）
├── config.py            # 配置加载（YAML + ${ENV_VAR} 展开）
├── db.py                # SQLite 初始化与 CRUD
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
└── skills/              # Skill 包
    ├── feishu-notify/
    └── cron-manager/
```

## 管理 API

服务启动后可通过 HTTP 查看运行状态：

| 端点 | 说明 |
|------|------|
| `GET /health` | 健康检查 |
| `GET /api/agents` | 已注册的 Agent 列表 |
| `GET /api/cron` | 定时任务列表及下次执行时间 |
| `GET /api/sessions` | 活跃会话列表 |
| `GET /api/dashboard` | 仪表盘概览数据 |
| `GET /api/skills` | 可用的 Skill 列表 |
| `GET /api/config` | 当前配置（敏感字段掩码） |

## Web 控制台

启动后访问 `http://localhost:8080/` 即可打开 Web 管理控制台：

| 页面 | 功能 |
|------|------|
| Dashboard | 概览面板（Agent、会话、Cron 状态） |
| Chat | 实时对话 — 选择 Agent，SSE 流式响应 |
| Agents | 查看所有 Agent 配置 |
| Skills | 浏览 SKILL.md 内容 |
| Cron Tasks | 定时任务列表 |
| Sessions | 活跃会话列表 |
| Config | 查看当前配置 |
| Events | 实时事件流 |

## 生产部署

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

### Docker

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install .
COPY codyclaw/ codyclaw/
EXPOSE 8080
CMD ["codyclaw"]
```

## 开发

```bash
# 运行测试
pytest tests/ -v

# 代码检查
ruff check codyclaw/
```

详细技术设计见 [docs/TECH_DESIGN.md](docs/TECH_DESIGN.md)。

## 贡献

欢迎参与贡献！请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解开发流程和规范。

## License

[Apache License 2.0](LICENSE)
