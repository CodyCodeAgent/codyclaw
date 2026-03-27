# Contributing to CodyClaw

感谢你对 CodyClaw 的关注！以下是参与贡献的指南。

## 开发环境搭建

```bash
git clone https://github.com/CodyCodeAgent/codyclaw.git
cd codyclaw
python -m venv .venv
source .venv/bin/activate
make dev       # 或: pip install -e ".[dev]"
```

## 常用命令

```bash
make dev       # 安装开发依赖
make lint      # 运行 ruff linter
make test      # 运行 pytest
make check     # lint + tests（CI 等价）
make run       # 启动 CodyClaw
make docker    # 构建 Docker 镜像
make clean     # 清理构建产物
make help      # 查看所有命令
```

## 开发工作流

1. **Fork** 本仓库并创建你的 feature 分支：
   ```bash
   git checkout -b feature/my-feature
   ```

2. **编写代码**，遵循项目风格（见下方规范）

3. **运行测试和 lint**：
   ```bash
   make check    # 或手动执行：
   ruff check codyclaw/
   pytest tests/ -v
   ```

4. **提交**，使用清晰的 commit message：
   ```
   feat: add support for custom trigger keywords
   fix: resolve session leak on interaction timeout
   docs: update config.yaml.template with base_url examples
   ```

5. **推送并创建 Pull Request**

## 代码规范

- **Python 3.10+**，使用 type hints
- **Ruff** 规则：`E`, `F`, `I`（isort），行宽 100
- **异步优先**：所有 I/O 操作使用 `async/await`
- **命名**：
  - 模块和变量：`snake_case`
  - 类：`PascalCase`
  - 私有属性：`_leading_underscore`
  - 常量：`UPPER_SNAKE_CASE`

## 项目结构

新增功能请遵循五层架构：

| 层 | 目录 | 职责 |
|----|------|------|
| Channel | `codyclaw/channel/` | 飞书渠道适配 |
| Gateway | `codyclaw/gateway/` | 消息路由与 Agent 调度 |
| Automation | `codyclaw/automation/` | 定时任务、事件总线 |
| Web | `codyclaw/web/` | Web 控制台（API + 前端 SPA） |
| Skills | `codyclaw/skills/` | Agent 技能包 |

### Web 前端

前端为纯 HTML/CSS/JS SPA（无构建工具），位于 `codyclaw/web/static/`：
- `index.html`：页面结构
- `style.css`：样式（CSS 变量主题）
- `app.js`：交互逻辑（fetch API + SSE）

修改前端后无需编译，刷新页面即可。

## 添加新 Skill

1. 在 `codyclaw/skills/` 下创建目录（如 `my-skill/`）
2. 编写 `SKILL.md`，说明 Skill 用途和参数
3. 如果需要 custom tool，在 `codyclaw/gateway/tools.py` 中添加

## 测试

- 测试文件放在 `tests/` 目录
- 使用 `pytest` + `pytest-asyncio`
- 时间相关测试使用 `monkeypatch` mock `time.time()`
- 确保新功能有对应的测试覆盖

## Issue & PR

- **Bug Report**：使用 Issue 模板，提供复现步骤
- **Feature Request**：描述使用场景和期望行为
- **PR**：关联对应 Issue，描述改动内容和测试情况

## License

贡献代码即表示你同意以 [Apache License 2.0](LICENSE) 授权你的贡献。
