"""
内存令牌桶限流器。

设计要点（面试可以讲）：
1. 为什么需要限流？
   登录接口不限流就可以被暴力破解密码；创建预约不限流可以被脚本刷爆数据库。
   限流是服务端自我保护的第一道防线。

2. 这里实现的是「令牌桶（Token Bucket）」算法：
   - 桶有固定容量 capacity
   - 以 refill_rate 个/秒 的速度往桶里加令牌，加满为止
   - 每个请求消耗一个令牌，桶空了就拒绝
   相比"固定窗口计数"，令牌桶允许一定程度的突发流量，更贴近真实场景。

3. 为什么放在内存里？局限是什么？（这一点一定要能讲）
   内存实现只在「单进程」下正确。如果部署了多个 worker（比如 uvicorn --workers 4），
   每个进程各有自己的桶，实际放行量是配置值的 4 倍。
   生产环境应当用 Redis 实现（INCR + EXPIRE 或 Redis 的 Lua 脚本保证原子性），
   或者交给 Nginx / API 网关去做。
   本项目是单进程开发环境，用内存实现足够，但必须知道它的边界。

4. 这里是同步实现、用 threading.Lock 保护。
   因为 FastAPI 的路由函数是 async，但如果只是做字典读写这种极快的操作，
   用同步锁可以接受（不会阻塞事件循环超过微秒级）。
   如果限流逻辑里要做 IO（比如查 Redis），就必须用 async。
"""
import threading
import time
from dataclasses import dataclass, field

from app.core.exceptions import AppException
from app.core.response import ErrorCode


@dataclass
class _Bucket:
    """单个桶的状态"""

    tokens: float          # 当前可用令牌数
    last_refill: float     # 上次补充令牌的时间戳


@dataclass
class RateLimiter:
    """
    令牌桶限流器。

    使用示例：
        limiter = RateLimiter(capacity=5, refill_rate=0.2)  # 容量5，每秒补0.2个（即每5秒1个）

        @router.post("/login")
        async def login(request: Request, ...):
            limiter.check(request.client.host)   # 超限会直接抛 429
    """

    capacity: int          # 桶容量（允许的突发请求数上限）
    refill_rate: float     # 每秒补充的令牌数
    _buckets: dict[str, _Bucket] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _refill(self, bucket: _Bucket, now: float) -> None:
        """按经过的时间往桶里补令牌，不超过容量上限"""
        elapsed = now - bucket.last_refill
        if elapsed > 0:
            bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_rate)
            bucket.last_refill = now

    def check(self, key: str) -> None:
        """
        检查是否允许通过。超限时抛出异常。

        :param key: 限流维度，通常是 "接口名:客户端IP"
        :raises AppException: 429 请求过于频繁
        """
        # 限流总开关（配置项 RATE_LIMIT_ENABLED）。
        # 为什么要有这个开关？
        # 测试和并发压测时需要关掉限流，否则得到的是"被限流拒绝"的结果，
        # 而不是"业务逻辑正确拒绝"的结果 —— 那种"假通过"最危险。
        # 生产环境必须保持开启。
        from app.core.config import settings as _settings

        if not _settings.rate_limit_enabled:
            return

        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.capacity), last_refill=now)
                self._buckets[key] = bucket
            else:
                self._refill(bucket, now)

            if bucket.tokens < 1.0:
                # 计算需要等待多久才有下一个令牌，写进提示里更友好
                wait_seconds = (1.0 - bucket.tokens) / self.refill_rate
                raise AppException(
                    ErrorCode.RATE_LIMITED,
                    f"操作过于频繁，请 {wait_seconds:.0f} 秒后再试",
                    http_status=429,
                )
            bucket.tokens -= 1.0

    def reset(self, key: str | None = None) -> None:
        """清空限流状态。测试里用来避免用例之间互相影响。"""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


# ====================== 预定义的限流器 ======================

# 登录：容量 10（允许连点几次），每 6 秒补 1 个 → 长期平均每分钟 10 次
login_limiter = RateLimiter(capacity=10, refill_rate=1 / 6)

# 创建预约：容量 5，每 12 秒补 1 个 → 防止脚本刷预约
booking_create_limiter = RateLimiter(capacity=5, refill_rate=1 / 12)

# 注册：容量 3，每 60 秒补 1 个 → 防止批量注册垃圾账号
register_limiter = RateLimiter(capacity=3, refill_rate=1 / 60)

# 文件上传：容量 10，每 6 秒补 1 个
upload_limiter = RateLimiter(capacity=10, refill_rate=1 / 6)


def get_client_ip(request) -> str:
    """
    获取客户端真实 IP。

    注意点：如果前面有 Nginx 反向代理，request.client.host 拿到的是代理的 IP，
    真实 IP 在 X-Forwarded-For 头里（格式：客户端IP, 代理1, 代理2）。
    所以要先读这个头，取第一段。
    但这里有个安全陷阱：X-Forwarded-For 是客户端可以伪造的，
    如果服务直接暴露在公网且没经过可信代理，攻击者可以伪造 IP 绕过限流。
    正确做法是只在「来自可信代理」时才信任这个头。
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
