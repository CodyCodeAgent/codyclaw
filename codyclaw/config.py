# codyclaw/config.py

import dataclasses
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from codyclaw.automation.cron import CronTask
from codyclaw.gateway.router import AgentConfig


@dataclass
class LarkConfig:
    app_id: str = ""
    app_secret: str = ""
    encrypt_key: str = ""
    verification_token: str = ""
    bot_open_id: str = ""


@dataclass
class GatewayConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"


@dataclass
class CodyClawConfig:
    lark: LarkConfig = field(default_factory=LarkConfig)
    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    agents: list[AgentConfig] = field(default_factory=list)
    default_agent: Optional[str] = None
    cron_tasks: list[CronTask] = field(default_factory=list)
    cody: dict = field(default_factory=dict)
    db_path: str = ""   # 留空则自动填充为 ~/.codyclaw/codyclaw.db


def _filter_fields(cls, data: dict) -> dict:
    """过滤掉 dataclass 中不存在的字段，防止 YAML 中多余的键导致 TypeError。"""
    known = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in known}


def _resolve_env_vars(value: str) -> str:
    """解析 ${ENV_VAR} 引用"""
    def replacer(match):
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))
    return re.sub(r'\$\{(\w+)\}', replacer, value)


def load_config(path: Optional[str] = None) -> tuple[CodyClawConfig, str]:
    """加载配置文件，返回 (config, config_file_path)。"""
    if path is None:
        candidates = [
            Path.home() / ".codyclaw" / "config.yaml",
        ]
        for p in candidates:
            if p.exists():
                path = str(p)
                break

    if not path:
        raise FileNotFoundError("No config.yaml found")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # 递归解析环境变量
    raw = _deep_resolve(raw)

    config = CodyClawConfig()

    # 解析各部分
    if "lark" in raw:
        config.lark = LarkConfig(**_filter_fields(LarkConfig, raw["lark"]))

    if "gateway" in raw:
        config.gateway = GatewayConfig(**_filter_fields(GatewayConfig, raw["gateway"]))

    if "agents" in raw:
        config.agents = [AgentConfig(**_filter_fields(AgentConfig, a)) for a in raw["agents"]]

    config.default_agent = raw.get("default_agent")

    if "cron_tasks" in raw:
        config.cron_tasks = [CronTask(**_filter_fields(CronTask, t)) for t in raw["cron_tasks"]]

    if "cody" in raw:
        config.cody = raw["cody"]

    if "db_path" in raw:
        config.db_path = raw["db_path"]

    if not config.db_path:
        config.db_path = str(Path.home() / ".codyclaw" / "codyclaw.db")

    return config, path


def _deep_resolve(obj):
    """递归解析所有字符串中的环境变量"""
    if isinstance(obj, str):
        return _resolve_env_vars(obj)
    elif isinstance(obj, dict):
        return {k: _deep_resolve(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_deep_resolve(v) for v in obj]
    return obj
