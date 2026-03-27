# codyclaw/web/api.py
#
# Web 控制台 API：配置管理、聊天、技能查看、历史记录等。

import json
import logging
import time
import uuid
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from codyclaw.automation.events import Event

if TYPE_CHECKING:
    from codyclaw.automation.cron import CronScheduler
    from codyclaw.gateway.dispatcher import AgentDispatcher
    from codyclaw.gateway.router import MessageRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

@router.get("/setup/status")
async def setup_status(req: Request):
    """返回当前配置状态，前端据此决定是否显示 setup 向导。"""
    from codyclaw.config import is_configured
    config = req.app.state.config
    setup_mode = getattr(req.app.state, "setup_mode", False)
    return {
        "setup_mode": setup_mode,
        "configured": is_configured(config),
        "config_path": getattr(req.app.state, "config_path", ""),
    }


@router.post("/setup/save")
async def setup_save(req: Request):
    """保存初始配置，写入 config.yaml。保存后需要重启生效。"""
    from codyclaw.config import save_config_yaml

    body = await req.json()
    config_path = getattr(req.app.state, "config_path", "")
    if not config_path:
        return JSONResponse({"error": "config_path not set"}, status_code=500)

    # 从表单构建配置
    config_data = {
        "lark": {
            "app_id": body.get("lark_app_id", "").strip(),
            "app_secret": body.get("lark_app_secret", "").strip(),
            "bot_open_id": body.get("lark_bot_open_id", "").strip(),
        },
        "gateway": {
            "host": body.get("gateway_host", "0.0.0.0").strip(),
            "port": int(body.get("gateway_port", 8080)),
            "log_level": body.get("gateway_log_level", "info").strip(),
        },
        "agents": [
            {
                "agent_id": body.get("agent_id", "assistant").strip() or "assistant",
                "name": body.get("agent_name", "Assistant").strip() or "Assistant",
                "workdir": body.get("agent_workdir", "/tmp").strip() or "/tmp",
                "model": body.get("agent_model", "claude-sonnet-4-20250514").strip(),
                "trigger_mode": "all",
            },
        ],
        "default_agent": body.get("agent_id", "assistant").strip() or "assistant",
        "cody": {},
    }

    # API Key — 支持 Anthropic 直连或第三方 base_url
    api_key = body.get("api_key", "").strip()
    base_url = body.get("base_url", "").strip()
    if api_key:
        config_data["cody"]["model_api_key"] = api_key
    if base_url:
        config_data["cody"]["base_url"] = base_url

    # 验证必填项
    lark = config_data["lark"]
    if not lark["app_id"] or not lark["app_secret"]:
        return JSONResponse(
            {"error": "Lark App ID and App Secret are required"}, status_code=400
        )

    try:
        save_config_yaml(config_path, config_data)
    except Exception as e:
        return JSONResponse({"error": f"Failed to save config: {e}"}, status_code=500)

    return {
        "status": "ok",
        "message": "Configuration saved! Please restart CodyClaw to apply.",
        "config_path": config_path,
    }

# ---------------------------------------------------------------------------
# Chat history (in-memory ring buffer, persisted to DB on write)
# ---------------------------------------------------------------------------

_MAX_HISTORY = 500
_chat_history: deque[dict] = deque(maxlen=_MAX_HISTORY)


def _add_chat_message(
    agent_id: str,
    session_key: str,
    role: str,
    content: str,
    db_path: str = "",
) -> dict:
    """添加一条聊天消息到历史记录并持久化。"""
    msg = {
        "id": uuid.uuid4().hex[:12],
        "agent_id": agent_id,
        "session_key": session_key,
        "role": role,
        "content": content,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _chat_history.append(msg)

    if db_path:
        try:
            from codyclaw.db import save_chat_message
            save_chat_message(db_path, msg)
        except Exception as e:
            logger.debug(f"Failed to persist chat message: {e}")
    return msg


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

_SKILLS_DIR = Path(__file__).parent.parent / "skills"


@router.get("/skills")
async def list_skills():
    """列出所有可用的 Skill 及其 SKILL.md 内容。"""
    skills = []
    if _SKILLS_DIR.exists():
        for skill_dir in sorted(_SKILLS_DIR.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            content = ""
            if skill_md.exists():
                content = skill_md.read_text(encoding="utf-8")
            skills.append({
                "name": skill_dir.name,
                "content": content,
            })
    return {"skills": skills}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = {"app_secret", "api_key", "model_api_key", "encrypt_key"}


def _mask_sensitive(obj, depth: int = 0):
    """递归掩码敏感字段值（只在叶子节点掩码）。"""
    if depth > 10:
        return obj
    if isinstance(obj, dict):
        return {
            k: ("***" if k in _SENSITIVE_KEYS and isinstance(v, str) and v else
                _mask_sensitive(v, depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_mask_sensitive(v, depth + 1) for v in obj]
    return obj


@router.get("/config")
async def get_config(req: Request):
    """获取当前配置（敏感字段掩码）。"""
    config_path = getattr(req.app.state, "config_path", None)

    raw = {}
    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    return {
        "config": _mask_sensitive(raw),
        "config_path": config_path or "~/.codyclaw/config.yaml",
    }


@router.put("/config")
async def update_config(req: Request):
    """更新配置文件（仅修改非敏感字段）。"""
    config_path = getattr(req.app.state, "config_path", None)
    if not config_path or not Path(config_path).exists():
        return JSONResponse({"error": "Config file not found"}, status_code=404)

    body = await req.json()
    updates = body.get("updates", {})
    if not updates:
        return JSONResponse({"error": "No updates provided"}, status_code=400)

    with open(config_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    # 合并更新（浅层合并，不允许修改敏感字段）
    for key, value in updates.items():
        if key in _SENSITIVE_KEYS:
            continue
        if isinstance(value, dict) and isinstance(raw.get(key), dict):
            # 合并 dict（跳过敏感子键）
            for k, v in value.items():
                if k not in _SENSITIVE_KEYS:
                    raw[key][k] = v
        else:
            raw[key] = value

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(raw, f, allow_unicode=True, default_flow_style=False)

    return {
        "status": "ok",
        "message": "Config updated. Restart to apply changes.",
        "warning": "YAML comments in the original file have been removed by this operation.",
    }


# ---------------------------------------------------------------------------
# Chat (SSE streaming)
# ---------------------------------------------------------------------------

@router.post("/chat/send")
async def chat_send(req: Request):
    """通过 SSE 向 Agent 发送消息并流式返回结果。"""
    from cody.sdk.types import (
        DoneChunk,
        InteractionRequestChunk,
        TextDeltaChunk,
        ToolCallChunk,
    )

    body = await req.json()
    agent_id = body.get("agent_id", "")
    message = body.get("message", "").strip()
    session_key = body.get("session_key", "")

    if not message:
        return JSONResponse({"error": "message is required"}, status_code=400)

    dispatcher: "AgentDispatcher" = req.app.state.dispatcher
    config = req.app.state.config
    db_path = config.db_path

    agent_config = dispatcher.get_agent(agent_id)
    if not agent_config:
        # 回退到默认 Agent
        default_id = config.default_agent
        if default_id:
            agent_config = dispatcher.get_agent(default_id)
    if not agent_config:
        return JSONResponse({"error": f"Agent '{agent_id}' not found"}, status_code=404)

    if not session_key:
        session_key = f"web:{agent_config.agent_id}:{uuid.uuid4().hex[:8]}"

    # 保存用户消息
    _add_chat_message(agent_config.agent_id, session_key, "user", message, db_path)

    client = await dispatcher.get_or_create_client(agent_config)
    session_id = dispatcher.get_session(session_key)

    async def event_stream():
        accumulated = ""
        new_session_id = None
        try:
            async for chunk in client.stream(message, session_id=session_id):
                if isinstance(chunk, TextDeltaChunk):
                    accumulated += chunk.content
                    yield f"data: {json.dumps({'type': 'text', 'content': chunk.content})}\n\n"
                elif isinstance(chunk, ToolCallChunk):
                    tool_info = f"\n`[Tool: {chunk.tool_name}]`\n"
                    accumulated += tool_info
                    yield f"data: {json.dumps({'type': 'tool', 'name': chunk.tool_name})}\n\n"
                elif isinstance(chunk, InteractionRequestChunk):
                    # Web 控制台自动批准操作（Web 用户视为管理员）
                    await client.submit_interaction(chunk.request_id, "approve")
                    yield f"data: {json.dumps({'type': 'approval', 'content': chunk.content})}\n\n"

                elif isinstance(chunk, DoneChunk):
                    if chunk.session_id:
                        new_session_id = chunk.session_id
                        dispatcher.set_session(session_key, chunk.session_id)

            # 保存助手回复
            if accumulated:
                _add_chat_message(
                    agent_config.agent_id, session_key, "assistant", accumulated, db_path
                )

            done_payload = {
                "type": "done",
                "session_key": session_key,
                "session_id": new_session_id or "",
            }
            yield f"data: {json.dumps(done_payload)}\n\n"

        except Exception as e:
            logger.exception(f"Web chat error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/history")
async def chat_history(
    req: Request,
    agent_id: str = "",
    session_key: str = "",
    limit: int = 50,
):
    """获取聊天历史记录。优先返回内存数据，回退到 DB。"""
    messages = list(_chat_history)
    if agent_id:
        messages = [m for m in messages if m["agent_id"] == agent_id]
    if session_key:
        messages = [m for m in messages if m["session_key"] == session_key]

    # 内存无数据时回退到 DB 查询
    if not messages:
        db_path = req.app.state.config.db_path
        if db_path:
            from codyclaw.db import load_chat_messages
            messages = load_chat_messages(
                db_path, agent_id=agent_id, session_key=session_key, limit=limit
            )

    return {"messages": messages[-limit:]}


# ---------------------------------------------------------------------------
# Events (SSE live feed)
# ---------------------------------------------------------------------------

@router.get("/events/stream")
async def events_stream(req: Request):
    """SSE 实时事件流（Agent 运行状态、Cron 执行等）。"""
    import asyncio

    event_bus = req.app.state.event_bus
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    async def handler(event: Event):
        try:
            queue.put_nowait({
                "type": str(event.type.value),
                "data": event.data,
                "source": event.source,
                "time": time.strftime("%H:%M:%S"),
            })
        except asyncio.QueueFull:
            pass  # 丢弃旧事件

    # 订阅所有事件前缀
    for prefix in ("agent", "cron", "gateway", "message", "config"):
        event_bus.on(prefix, handler)

    async def stream():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            for prefix in ("agent", "cron", "gateway", "message", "config"):
                event_bus.off(prefix, handler)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------

@router.get("/dashboard")
async def dashboard(req: Request):
    """仪表盘概览数据。"""
    dispatcher: "AgentDispatcher" = req.app.state.dispatcher
    router_inst: "MessageRouter" = req.app.state.router
    cron: "CronScheduler" = req.app.state.cron

    agents = list(router_inst.iter_agents())
    active_sessions = dispatcher.get_sessions()
    active_runs = dispatcher.active_run_count

    cron_tasks = []
    for task in cron.tasks.values():
        job = cron.get_job(task.task_id)
        cron_tasks.append({
            "id": task.task_id,
            "name": task.name,
            "enabled": task.enabled,
            "next_run": (
                job.next_run_time.strftime("%Y-%m-%d %H:%M")
                if job and job.next_run_time else None
            ),
        })

    return {
        "agents": [
            {"id": a.agent_id, "name": a.name, "model": a.model, "workdir": a.workdir}
            for a in agents
        ],
        "sessions": {
            "count": len(active_sessions),
            "items": [{"key": k, "session_id": v} for k, v in active_sessions.items()],
        },
        "active_runs": active_runs,
        "cron_tasks": cron_tasks,
        "chat_messages_count": len(_chat_history),
    }
