"""
API 路由汇总。

把所有子路由的注册集中在这里，main.py 只需要 include 一次。
好处：要加新模块只改这个文件，main.py 不用动。
"""
from fastapi import APIRouter

from app.api.routes import health

# 总路由：所有业务接口都挂在 API_PREFIX 下（在 main.py 里设置）
api_router = APIRouter()

api_router.include_router(health.router)

# 后续批次会在这里追加：
# api_router.include_router(auth.router)          # 认证
# api_router.include_router(users.router)         # 用户
# api_router.include_router(equipment.router)     # 设备
# api_router.include_router(bookings.router)      # 预约
# api_router.include_router(statistics.router)    # 统计
# api_router.include_router(upload.router)        # 文件上传
