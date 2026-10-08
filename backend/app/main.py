"""
应用入口。

启动方式（在 backend 目录下）：
    .venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001

为什么生产环境不要用 --reload？
  reload 模式会启动一个监视进程 + 一个工作进程。如果你的应用里有定时任务
  （比如本项目每 60 秒同步设备状态的 APScheduler），
  在 reload 模式下定时任务会被加载两次，导致：
    - 同一份数据被处理两遍
    - 日志重复
    - 严重时产生重复的业务数据
  原项目的注释里也提到了这个坑，V2 在这里明确说明。

启动顺序（lifespan）：
  1. 初始化日志（必须最先，否则后续的报错看不到）
  2. 检查数据库连通性，连不上就直接启动失败（快速失败原则）
  3. 检查 Redis，连不上只告警不阻断（缓存是可选依赖，降级即可）
  4. 启动定时任务
  关闭时反向清理。
"""
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ---------- 控制台编码修复（必须在任何 print/log 之前执行） ----------
# Windows 中文控制台默认 GBK，日志里若有 emoji 或特殊符号会抛
# UnicodeEncodeError 并导致启动失败。这里提前把输出流切成 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        try:
            _reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

from app.api.router import api_router  # noqa: E402
from app.core.cache import close as close_redis  # noqa: E402
from app.core.cache import ping as redis_ping  # noqa: E402
from app.core.config import BASE_DIR, settings  # noqa: E402
from app.core.database import check_database_connection, dispose_engine  # noqa: E402
from app.core.exceptions import register_exception_handlers  # noqa: E402
from app.core.logging_conf import setup_logging  # noqa: E402
from app.core.scheduler import start_scheduler, stop_scheduler  # noqa: E402

logger = logging.getLogger(__name__)

APP_VERSION = "2.0.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理。

    yield 之前是启动逻辑，之后是关闭逻辑。
    """
    # ========== 启动 ==========
    setup_logging()
    logger.info("=" * 70)
    logger.info("  %s  v%s", settings.app_name, APP_VERSION)
    logger.info("  运行环境: %s | 调试模式: %s", settings.app_env, settings.debug)
    logger.info("=" * 70)

    # 1. 数据库：这是硬依赖，连不上就直接失败退出（快速失败）
    if await check_database_connection():
        db = settings.database
        logger.info("[OK] 数据库连接成功 -> %s:%s/%s", db.host, db.port, db.name)
    else:
        logger.critical("[FATAL] 数据库连接失败，应用无法启动")
        logger.critical("        请检查：MySQL 服务是否启动、backend/.env 中的数据库配置是否正确")
        raise RuntimeError("数据库连接失败")

    # 2. Redis：软依赖，连不上只告警（缓存降级，业务仍可用）
    if await redis_ping():
        logger.info("[OK] Redis 连接成功 -> %s:%s", settings.redis.host, settings.redis.port)
    else:
        logger.warning("[WARN] Redis 连接失败，缓存功能将降级（业务仍可正常运行）")
        logger.warning("       如需启用缓存，请启动 Redis 服务")

    # 3. 确保上传目录存在（StaticFiles 挂载时目录必须已存在）
    upload_dir = BASE_DIR / "app" / "static" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    # 4. 启动定时任务
    #    ⚠️ 注意：如果你用 uvicorn --reload 启动，lifespan 会被执行两次，
    #    定时任务就会注册两次，同一份数据被处理两遍。
    #    启动命令里不要加 --reload（见本文件顶部的说明）。
    start_scheduler()

    logger.info("[OK] 应用启动完成，接口文档: http://127.0.0.1:8001/docs")

    yield

    # ========== 关闭 ==========
    logger.info("正在关闭应用...")
    stop_scheduler()
    await close_redis()
    await dispose_engine()
    logger.info("[OK] 应用已关闭，资源已释放")


def create_app() -> FastAPI:
    """
    工厂函数：创建并配置 FastAPI 实例。

    为什么用工厂函数而不是模块级 app = FastAPI()？
    因为测试里需要创建一个「配置不同」的实例（比如关掉文档、
    替换依赖），工厂模式让这件事很容易。这也是 FastAPI 官方推荐的写法。
    """
    app = FastAPI(
        title=settings.app_name,
        description=(
            "高校实验室设备预约管理系统 —— 后端 API\n\n"
            "**分层架构**：router（HTTP） → service（业务） → repository（数据）\n\n"
            "**统一响应格式**：`{code, message, data}`，业务码见 docs 说明"
        ),
        version=APP_VERSION,
        lifespan=lifespan,
        # 生产环境关闭 API 文档，避免暴露接口结构
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )

    # ---------- 1. 异常处理器（要让所有异常都走统一格式） ----------
    register_exception_handlers(app)

    # ---------- 2. CORS ----------
    # 安全提醒：allow_origins=["*"] 与 allow_credentials=True 是非法组合，
    # 浏览器会直接拒绝。而且通配源 + 携带凭证本身就是严重的安全问题。
    # 这里从 .env 读取明确的前端地址列表。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Total-Count"],  # 允许前端读取自定义响应头
        max_age=600,  # 预检请求缓存 10 分钟，减少 OPTIONS 请求
    )

    # ---------- 3. 静态文件（图片访问） ----------
    # 挂载后，数据库里存的 "uploads/equipment/2026/10/xxx.jpg"
    # 就可以通过 http://host:8001/static/uploads/equipment/2026/10/xxx.jpg 访问
    static_dir = BASE_DIR / "app" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # ---------- 4. 业务路由 ----------
    app.include_router(api_router, prefix=settings.api_prefix)

    # ---------- 5. 根路径 ----------
    @app.get("/", tags=["根"], summary="服务信息")
    async def root() -> dict:
        return {
            "code": 0,
            "message": f"{settings.app_name} 后端服务运行中",
            "data": {
                "version": APP_VERSION,
                "api_prefix": settings.api_prefix,
                "docs": "/docs" if not settings.is_production else None,
                "health": f"{settings.api_prefix}/health",
            },
        }

    return app


app = create_app()
