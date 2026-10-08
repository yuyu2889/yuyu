"""
API 路由汇总。

把所有子路由的注册集中在这里，main.py 只需要 include 一次。
好处：要加新模块只改这个文件，main.py 不用动。
"""
from fastapi import APIRouter

from app.api.routes import auth, booking, equipment, health, users

# 总路由：所有业务接口都挂在 API_PREFIX 下（在 main.py 里设置）
api_router = APIRouter()

# ---------- 健康检查 ----------
api_router.include_router(health.router)

# ---------- 认证与用户 ----------
api_router.include_router(auth.router)
api_router.include_router(users.router)

# ---------- 设备相关 ----------
api_router.include_router(equipment.router)

# ---------- 预约 ----------
api_router.include_router(booking.router)

# 后续批次会在这里追加：
# api_router.include_router(statistics.router)  # 第 4 批：统计
