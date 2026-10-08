"""
健康检查路由。

设计要点（面试可以讲）：
1. 「存活检查」和「就绪检查」是两个不同的概念，不要混：
   - /health/live  进程活着吗？（不查依赖，Kubernetes 用它决定要不要重启容器）
   - /health/ready 能对外提供服务吗？（要查数据库、Redis，
                    负载均衡用它决定要不要把流量打进来）
   如果只有一个接口而且里面查了数据库，那么数据库抖动时
   K8s 会把所有容器都重启一遍 —— 不但没用，还会引发雪崩。

2. 健康检查不要暴露敏感信息（连接串、密码、版本号以外的细节）。
   很多项目在这里直接返回配置，是典型的信息泄露。

3. 这里查数据库用的是轻量的 SELECT 1，不是查业务表 ——
   健康检查必须快（毫秒级），否则会拖慢负载均衡的探测。
"""
from datetime import datetime

from fastapi import APIRouter

from app.core.cache import ping as redis_ping
from app.core.config import settings
from app.core.database import check_database_connection
from app.core.response import Response, success
from app.schemas.common import HealthResponse

router = APIRouter(prefix="/health", tags=["健康检查"])

APP_VERSION = "2.0.0"


@router.get(
    "/live",
    response_model=Response[dict],
    summary="存活检查",
    description="只判断进程是否存活，不检查任何外部依赖。用于容器编排的重启决策。",
)
async def liveness() -> Response[dict]:
    return success(data={"alive": True})


@router.get(
    "/ready",
    response_model=Response[dict],
    summary="就绪检查",
    description="检查数据库和 Redis 是否可用。用于负载均衡判断是否可以把流量打进来。",
)
async def readiness() -> Response[dict]:
    db_ok = await check_database_connection()
    redis_ok = await redis_ping()

    # Redis 挂了不影响核心业务（缓存会降级为直查数据库），
    # 所以只在响应里如实反映，不判定为"未就绪"。
    # 但数据库挂了就真的没法服务，必须标记为未就绪。
    ready = db_ok
    return success(
        data={
            "ready": ready,
            "database": db_ok,
            "redis": redis_ok,
        },
        message="服务就绪" if ready else "数据库不可用，服务未就绪",
    )


@router.get(
    "",
    response_model=Response[HealthResponse],
    summary="健康概览",
    description="返回应用信息和各依赖组件的状态，用于人工排查。",
)
async def health_overview() -> Response[HealthResponse]:
    db_ok = await check_database_connection()
    redis_ok = await redis_ping()

    payload = HealthResponse(
        # 只有数据库不可用才算 degraded；Redis 不可用只是失去缓存加速
        status="ok" if db_ok else "degraded",
        app_name=settings.app_name,
        version=APP_VERSION,
        environment=settings.app_env,
        database=db_ok,
        redis=redis_ok,
        server_time=datetime.utcnow(),
    )
    return success(data=payload, message="服务运行正常" if db_ok else "数据库连接异常")
