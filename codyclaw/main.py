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

    dispatcher = AgentDispatcher(channel, router, config.cody, event_bus, config.db_path)
    app.state.dispatcher = dispatcher

    dedup = MessageDeduplicator()
    app.state.dedup = dedup

    async def handle_message(msg):
        if dedup.is_duplicate(msg.message_id):
            return
        content = msg.content.strip()
        if content == "取消":
            await dispatcher.cancel(msg.sender_id)
            return
        if await dispatcher.try_resolve_by_message(msg.sender_id, content):
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
    await dispatcher.shutdown()
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
                "schedule": task.schedule,
                "enabled": task.enabled,
                "next_run": next_run,
            })
        return {"tasks": tasks}

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
