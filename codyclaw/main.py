# codyclaw/main.py

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from codyclaw.config import CodyClawConfig, is_configured, load_config
from codyclaw.db import init_db

logger = logging.getLogger(__name__)

_WEB_DIR = str(Path(__file__).parent / "web" / "static")


def setup_logging(log_level: str = "info") -> None:
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Setup mode — 配置未就绪时的轻量启动
# ---------------------------------------------------------------------------

@asynccontextmanager
async def setup_lifespan(app: FastAPI):
    """Setup 模式：仅启动 Web UI 供用户填写配置。"""
    config: CodyClawConfig = app.state.config
    init_db(config.db_path)
    logger.info("CodyClaw started in SETUP mode — open http://localhost:8080 to configure")
    yield
    logger.info("Setup mode shutting down")


def create_setup_app(config: CodyClawConfig, config_path: str) -> FastAPI:
    """创建 setup 模式的 FastAPI 应用（仅配置向导 + 静态文件）。"""
    app = FastAPI(title="CodyClaw Setup", version="0.1.0", lifespan=setup_lifespan)
    app.state.config = config
    app.state.config_path = config_path
    app.state.setup_mode = True

    from codyclaw.web.api import router as web_router
    app.include_router(web_router)

    @app.get("/health")
    async def health():
        return {"status": "setup", "version": "0.1.0", "configured": False}

    app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")

    @app.get("/")
    async def setup_index():
        return FileResponse(Path(_WEB_DIR) / "index.html")

    return app


# ---------------------------------------------------------------------------
# Normal mode — 配置就绪后的完整启动
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期管理（完整模式）"""
    from codyclaw.automation.boot import execute_boot_scripts
    from codyclaw.automation.cron import CronScheduler, CronTask
    from codyclaw.automation.events import Event, EventBus, EventType
    from codyclaw.channel.dedup import MessageDeduplicator
    from codyclaw.channel.lark_impl import LarkChannelImpl
    from codyclaw.gateway.dispatcher import AgentDispatcher
    from codyclaw.gateway.router import MessageRouter

    config: CodyClawConfig = app.state.config

    # --- 启动 ---
    init_db(config.db_path)

    channel = LarkChannelImpl(config.lark)
    app.state.channel = channel

    router = MessageRouter()
    for agent_cfg in config.agents:
        router.register_agent(agent_cfg)
    if config.default_agent:
        router.set_default_agent(config.default_agent)
    app.state.router = router

    event_bus = EventBus()
    app.state.event_bus = event_bus

    # 注册全局事件记录器，保证即使没有 SSE 客户端连接也能缓存历史事件
    from codyclaw.web.api import _record_event
    for _prefix in ("agent", "cron", "gateway", "message", "config"):
        event_bus.on(_prefix, _record_event)

    dispatcher = AgentDispatcher(channel, router, config.cody, event_bus, config.db_path)
    app.state.dispatcher = dispatcher

    dedup = MessageDeduplicator()
    app.state.dedup = dedup

    async def handle_message(msg):
        if dedup.is_duplicate(msg.message_id):
            return
        asyncio.create_task(dispatcher.dispatch(msg))

    channel.on_message(handle_message)
    await channel.start()
    logger.info("Lark channel connected")

    await execute_boot_scripts(dispatcher, router, event_bus)

    cron = CronScheduler(dispatcher, channel, db_path=config.db_path)
    for task in config.cron_tasks:
        cron.add_task(task)
    from codyclaw.db import load_cron_tasks
    db_tasks = load_cron_tasks(config.db_path)
    dynamic_count = 0
    for row in db_tasks:
        if row["task_id"] not in cron.tasks:
            cron.add_task(CronTask(**row))
            dynamic_count += 1
    cron.start()
    dispatcher.set_cron_scheduler(cron)
    app.state.cron = cron
    logger.info(
        f"Cron scheduler started: {len(config.cron_tasks)} static, {dynamic_count} dynamic tasks"
    )

    logger.info("CodyClaw Gateway is running")

    yield

    # --- 关闭 ---
    logger.info("Shutting down CodyClaw Gateway...")
    await event_bus.emit(Event(type=EventType.GATEWAY_SHUTDOWN))
    cron.stop()
    try:
        await asyncio.wait_for(dispatcher.shutdown(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("dispatcher.shutdown() timed out after 5s — forcing exit")
    await channel.stop()


def create_app(config: CodyClawConfig, config_path: str = "") -> FastAPI:
    app = FastAPI(title="CodyClaw Gateway", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.config_path = config_path
    app.state.setup_mode = False

    @app.get("/health")
    async def health(req: Request):
        channel = getattr(req.app.state, "channel", None)
        lark_connected = channel.is_connected if channel else False
        lark_error = getattr(channel, "_last_error", None) if channel else None
        return {
            "status": "ok",
            "version": "0.1.0",
            "configured": True,
            "lark_connected": lark_connected,
            "lark_error": lark_error,
        }

    @app.get("/api/agents")
    async def list_agents(req: Request):
        from codyclaw.gateway.router import MessageRouter
        router: MessageRouter = req.app.state.router
        return {"agents": [
            {"id": a.agent_id, "name": a.name, "workdir": a.workdir}
            for a in router.iter_agents()
        ]}

    @app.get("/api/cron")
    async def list_cron_tasks(req: Request):
        from codyclaw.automation.cron import CronScheduler
        cron: CronScheduler = req.app.state.cron
        tasks = []
        for task in cron.tasks.values():
            job = cron.get_job(task.task_id)
            next_run = None
            if job and job.next_run_time:
                next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M")
            tasks.append({
                "id": task.task_id,
                "name": task.name,
                "agent_id": task.agent_id,
                "schedule": task.schedule,
                "prompt": task.prompt,
                "notify_chat_id": task.notify_chat_id or "",
                "enabled": task.enabled,
                "next_run": next_run,
            })
        return {"tasks": tasks}

    @app.post("/api/cron")
    async def create_cron_task(req: Request):
        import uuid

        from codyclaw.automation.cron import CronScheduler, CronTask
        cron: CronScheduler = req.app.state.cron
        body = await req.json()

        task_id = (body.get("task_id") or "").strip() or f"task-{uuid.uuid4().hex[:8]}"
        name = (body.get("name") or "").strip()
        agent_id = (body.get("agent_id") or "").strip()
        prompt = (body.get("prompt") or "").strip()
        schedule = (body.get("schedule") or "").strip()

        if not name or not agent_id or not prompt or not schedule:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"error": "name, agent_id, prompt, schedule are required"}, status_code=400
            )

        task = CronTask(
            task_id=task_id,
            name=name,
            agent_id=agent_id,
            prompt=prompt,
            schedule=schedule,
            notify_chat_id=(body.get("notify_chat_id") or "") or None,
            enabled=bool(body.get("enabled", True)),
        )
        try:
            cron.add_task(task, persist=True)
        except Exception as e:
            # add_task may have already inserted the task into _tasks / DB before the
            # schedule parsing failed — clean up to avoid a permanently broken state.
            cron.remove_task(task_id)
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": f"Invalid schedule: {e}"}, status_code=400)
        return {"status": "ok", "task_id": task_id}

    @app.put("/api/cron/{task_id}")
    async def update_cron_task(task_id: str, req: Request):
        from codyclaw.automation.cron import CronScheduler
        cron: CronScheduler = req.app.state.cron
        body = await req.json()

        updates = {}
        for field in ("name", "agent_id", "prompt", "schedule", "notify_chat_id", "enabled"):
            if field in body:
                updates[field] = body[field]

        try:
            ok = cron.update_task(task_id, **updates)
        except Exception as e:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": f"Invalid schedule: {e}"}, status_code=400)
        if not ok:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": f"Task '{task_id}' not found"}, status_code=404)
        return {"status": "ok"}

    @app.get("/api/cron/{task_id}/runs")
    async def get_cron_runs(task_id: str, req: Request):
        from codyclaw.db import load_cron_runs
        db_path = req.app.state.config.db_path
        if not db_path:
            return {"runs": []}
        runs = load_cron_runs(db_path, task_id)
        return {"runs": runs}

    @app.post("/api/cron/{task_id}/run")
    async def run_cron_task_now(task_id: str, req: Request):
        import asyncio

        from codyclaw.automation.cron import CronScheduler
        cron: CronScheduler = req.app.state.cron
        task = cron.tasks.get(task_id)
        if not task:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": f"Task '{task_id}' not found"}, status_code=404)
        asyncio.create_task(cron._execute_task(task))
        return {"status": "ok", "message": f"Task '{task.name}' triggered."}

    @app.delete("/api/cron/{task_id}")
    async def delete_cron_task_endpoint(task_id: str, req: Request):
        from codyclaw.automation.cron import CronScheduler
        cron: CronScheduler = req.app.state.cron
        removed = cron.remove_task(task_id)
        if not removed:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": f"Task '{task_id}' not found"}, status_code=404)
        return {"status": "ok"}

    @app.get("/api/sessions")
    async def list_sessions(req: Request):
        from codyclaw.gateway.dispatcher import AgentDispatcher
        dispatcher: AgentDispatcher = req.app.state.dispatcher
        return {"sessions": [
            {"key": k, "session_id": v}
            for k, v in dispatcher.get_sessions().items()
        ]}

    from codyclaw.web.api import router as web_router
    app.include_router(web_router)

    app.mount("/static", StaticFiles(directory=_WEB_DIR), name="static")

    @app.get("/")
    async def console_index():
        return FileResponse(Path(_WEB_DIR) / "index.html")

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    config, config_path = load_config()
    setup_logging(config.gateway.log_level)

    if is_configured(config):
        app = create_app(config, config_path=config_path)
    else:
        logger.warning("No valid configuration found — starting in setup mode")
        app = create_setup_app(config, config_path=config_path)

    uvicorn.run(
        app,
        host=config.gateway.host,
        port=config.gateway.port,
        log_level=config.gateway.log_level,
    )


if __name__ == "__main__":
    main()
