"""
Redis 客户端与缓存工具。

设计要点（面试可以讲）：
1. 缓存策略用的是 cache-aside（旁路缓存）：
   读：先查缓存 → 命中直接返回 → 未命中查数据库 → 写回缓存
   写：更新数据库 → 删除缓存（注意是删除，不是更新）
   为什么是"删除"而不是"更新"缓存？
   因为更新缓存容易出现并发写导致的数据不一致（两个请求各写各的），
   而"删除"让下次读取自然回填，出错概率更低。这也是业界主流做法。

2. 所有缓存操作都做了「降级容错」：
   Redis 挂了不应该导致整个业务不可用，而只是失去加速效果。
   所以每个函数都 try/except 并返回安全默认值。
   这一点很关键——很多项目因为缓存故障把主业务一起拖死。

3. Key 命名规范：{业务}:{对象}:{标识}，例如 equipment:detail:12。
   统一前缀的好处是可以用 SCAN 按模式批量清理，也方便在 redis-cli 里排查。

4. 批量删除用 SCAN 而不是 KEYS。
   KEYS 会阻塞 Redis 单线程（数据量大时可能卡住几百毫秒甚至几秒），
   SCAN 是游标式渐进遍历，不会长时间阻塞。
"""
import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

# ====================== 客户端 ======================

redis_client: aioredis.Redis = aioredis.Redis(
    host=settings.redis.host,
    port=settings.redis.port,
    db=settings.redis.db,
    password=settings.redis.password or None,
    # 自动把 bytes 解码成 str，省去到处 .decode()
    decode_responses=True,
    # 连接超时与命令超时，避免 Redis 卡住时把请求也拖住
    socket_connect_timeout=3,
    socket_timeout=3,
    health_check_interval=30,
)


# ====================== Key 命名规范 ======================

class CacheKey:
    """
    所有缓存 Key 集中管理。

    好处：
    - 不会出现同一个数据在 A 处写 "equipment_categories"、
      在 B 处删 "equipment:categories" 这种拼错的 bug
    - 要改命名规则只改这里
    """

    # 设备分类列表，数据稳定 → TTL 长
    EQUIPMENT_CATEGORIES = "equipment:categories"

    # 设备详情，会被浏览量/状态影响 → TTL 中等
    @staticmethod
    def equipment_detail(equipment_id: int) -> str:
        return f"equipment:detail:{equipment_id}"

    # 实验室列表
    LABORATORIES = "lab:list"

    # 统计概览，变化不频繁
    STATISTICS_OVERVIEW = "statistics:overview"

    # 批量删除用的模式
    EQUIPMENT_DETAIL_PATTERN = "equipment:detail:*"


# TTL 配置（秒）：数据越稳定，缓存越久
TTL_CATEGORIES = 7200    # 2 小时
TTL_EQUIPMENT_DETAIL = 1800  # 30 分钟
TTL_LABORATORIES = 7200
TTL_STATISTICS = 600     # 10 分钟


# ====================== 基础读写 ======================

async def get_json(key: str) -> Optional[Any]:
    """读取并反序列化 JSON 缓存。任何异常都降级为 None（视为未命中）。"""
    try:
        raw = await redis_client.get(key)
        return json.loads(raw) if raw else None
    except Exception as exc:
        logger.warning("读取缓存失败，降级为查数据库 | key=%s | %s", key, exc)
        return None


async def set_json(key: str, value: Any, ttl: int = 3600) -> bool:
    """
    写入 JSON 缓存。

    用 ensure_ascii=False 让中文以原字符存储（而不是 \\uXXXX 转义），
    这样在 redis-cli 里 get 出来能直接读懂，方便排查。
    """
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        await redis_client.setex(key, ttl, payload)
        return True
    except Exception as exc:
        logger.warning("写入缓存失败（不影响业务） | key=%s | %s", key, exc)
        return False


async def delete(*keys: str) -> int:
    """删除一个或多个 Key"""
    if not keys:
        return 0
    try:
        return await redis_client.delete(*keys)
    except Exception as exc:
        logger.warning("删除缓存失败 | keys=%s | %s", keys, exc)
        return 0


async def delete_by_pattern(pattern: str) -> int:
    """
    按模式批量删除（用 SCAN 渐进遍历，避免 KEYS 阻塞 Redis）。

    :return: 删除的 Key 数量
    """
    deleted = 0
    try:
        cursor = 0
        while True:
            cursor, keys = await redis_client.scan(cursor=cursor, match=pattern, count=100)
            if keys:
                deleted += await redis_client.delete(*keys)
            if cursor == 0:
                break
        if deleted:
            logger.info("按模式清理缓存 | pattern=%s | 删除 %d 个", pattern, deleted)
    except Exception as exc:
        logger.warning("按模式删除缓存失败 | pattern=%s | %s", pattern, exc)
    return deleted


async def ping() -> bool:
    """连通性检查，应用启动时调用"""
    try:
        await redis_client.ping()
        return True
    except Exception as exc:
        logger.warning("Redis 连接失败：%s（缓存功能将降级，业务仍可用）", exc)
        return False


async def close() -> None:
    """关闭连接，应用退出时调用"""
    try:
        await redis_client.aclose()
    except Exception:
        pass


# ====================== 业务级缓存操作 ======================

async def clear_equipment_cache(equipment_id: int | None = None) -> None:
    """
    清除设备相关缓存。

    调用时机（写操作后）：
    - 管理员新增/修改/删除设备
    - 上传设备图片
    - 定时任务改变设备状态

    equipment_id 为 None 时清全部设备详情缓存。
    """
    if equipment_id is not None:
        await delete(CacheKey.equipment_detail(equipment_id))
    else:
        await delete_by_pattern(CacheKey.EQUIPMENT_DETAIL_PATTERN)


async def clear_category_cache() -> None:
    """清除设备分类缓存（分类增删改后调用）"""
    await delete(CacheKey.EQUIPMENT_CATEGORIES)
