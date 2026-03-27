# codyclaw/main.py

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
import uvicorn

from codyclaw.config import load_config, CodyClawConfig
from codyclaw.db import init_db, load_cron_tasks
from codyclaw.channel.lark_impl import LarkChannelImpl
from codyclaw.channel.dedup import MessageDeduplicator
from codyclaw.gateway.router import MessageRouter
from codyclaw.gateway.dispatcher import AgentDispatcher
from codyclaw.automation.cron import CronScheduler, CronTask
from codyclaw.automation.events import EventBus, EventType, Event
from codyclaw.automation.boot import execute_boot_scripts

logger = logging.getLogger(__name__)


def setup_logging(log_level: str = "info") -> None:
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期管理"""
    config: CodyClawConfig = app.state.config

    # --- 启动 ---
    # 0. 初始化数据库
    init_db(config.db_path)

    # 1. 初始化飞书渠道
    channel = LarkChannelImpl(config.lark)
    app.state.channel = channel

    # 2. 初始化路由
    router = MessageRouter()
    for agent_cfg in config.agents:
        router.register_agent(agent_cfg)
    if config.default_agent:
        router.set_default_agent(config.default_agent)
    app.state.router = router

    # 3. 初始化事件总线（先于 dispatcher，以便传入）
    event_bus = EventBus()
    app.state.event_bus = event_bus

    # 4. 初始化调度器（传入 cody_config、event_bus 和 db_path）
    dispatcher = AgentDispatcher(channel, router, config.cody, event_bus, config.db_path)
    app.state.dispatcher = dispatcher

    # 5. 初始化去重器
    dedup = MessageDeduplicator()
    app.state.dedup = dedup

    # 6. 注册消息处理
    async def handle_message(msg):
        if dedup.is_duplicate(msg.message_id):
            return
        content = msg.content.strip()
        if content == "取消":
            await dispatcher.cancel(msg.sender_id)
            return
        if await dispatcher.try_resolve_by_message(msg.sender_id, content):
            return
        # 使用 create_task 避免阻塞消息接收循环（Agent 执行可能耗时较长）
        asyncio.create_task(dispatcher.dispatch(msg))

    channel.on_message(handle_message)

    # 7. 启动飞书连接
    await channel.start()
    logger.info("Lark channel connected")

    # 8. 执行 BOOT.md
    await execute_boot_scripts(dispatcher, router, event_bus)

    # 9. 启动 Cron 调度器
    cron = CronScheduler(dispatcher, channel, db_path=config.db_path)
    # 先加载 config.yaml 静态任务（不持久化，重启从配置恢复）
    for task in config.cron_tasks:
        cron.add_task(task)
    # 再加载 DB 里 AI 动态创建的任务（跳过与静态任务 ID 冲突的）
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

    logger.info("🚀 CodyClaw Gateway is running")

    yield

    # --- 关闭 ---
    logger.info("Shutting down CodyClaw Gateway...")
    await event_bus.emit(Event(type=EventType.GATEWAY_SHUTDOWN))
    cron.stop()
    await dispatcher.shutdown()
    await channel.stop()


def create_app(config: CodyClawConfig) -> FastAPI:
    app = FastAPI(
        title="CodyClaw Gateway",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.config = config

    # --- 管理 API ---
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/agents")
    async def list_agents(req: Request):
        router: MessageRouter = req.app.state.router
        return {"agents": [
            {"id": a.agent_id, "name": a.name, "workdir": a.workdir}
            for a in router.iter_agents()
        ]}

    @app.get("/api/cron")
    async def list_cron_tasks(req: Request):
        cron: CronScheduler = req.app.state.cron
        tasks = []
        for task in cron.tasks.values():
            job = cron.get_job(task.task_id)
            tasks.append({
                "id": task.task_id,
                "name": task.name,
                "schedule": task.schedule,
                "enabled": task.enabled,
                "next_run": str(job.next_run_time) if job else None,
            })
        return {"tasks": tasks}

    @app.get("/api/sessions")
    async def list_sessions(req: Request):
        """列出所有活跃会话"""
        dispatcher: AgentDispatcher = req.app.state.dispatcher
        return {"sessions": [
            {"key": k, "session_id": v}
            for k, v in dispatcher.get_sessions().items()
        ]}

    return app


def main():
    config = load_config()
    setup_logging(config.gateway.log_level)
    app = create_app(config)
    uvicorn.run(
        app,
        host=config.gateway.host,
        port=config.gateway.port,
        log_level=config.gateway.log_level,
    )


if __name__ == "__main__":
    main()
