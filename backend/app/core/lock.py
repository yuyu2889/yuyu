"""
Redis 分布式锁。

设计要点（面试可以讲，这是并发控制的核心工具）：

**为什么需要它？**
看一个典型的竞态（race condition）场景 —— 预约冲突检测：

    时刻   请求A                          请求B
    t1     查"该时段有没有冲突" → 没有
    t2                                    查"该时段有没有冲突" → 没有
    t3     插入预约成功
    t4                                    插入预约成功
    ────────────────────────────────────────────────
    结果：同一设备同一时段被预约了两次！

这种"先检查后执行"（check-then-act）的模式，只要检查和执行之间
存在时间窗口，并发下就必然出问题。这叫 **TOCTOU 竞态**
（Time-Of-Check to Time-Of-Use）。

**三种解法对比**：
1. 数据库悲观锁（SELECT ... FOR UPDATE）
   优点：可靠，不依赖外部组件
   缺点：持锁期间占用数据库连接，高并发下连接池容易被打满
   适合：事务短、并发量中等的场景

2. Redis 分布式锁
   优点：不占用数据库连接；天然支持超时自动释放；性能高
   缺点：依赖 Redis 可用性；需要考虑锁续期、误删等问题
   适合：并发量高、或锁要跨越多个数据库操作的场景

3. 数据库唯一约束
   优点：最简单、最可靠（数据库保证）
   缺点：只能防"完全相同"的冲突，防不住"时间段重叠"
   适合：作为最后一道兜底防线

**本项目的选择：三层防护全上**
  第 1 层 Redis 分布式锁  → 串行化并发请求（主要手段）
  第 2 层 事务内冲突检测  → 业务正确性（区间重叠算法）
  第 3 层 数据库唯一约束  → 兜底（防住极端情况）

**实现细节上的三个坑（面试可以追问）**：

坑1：加锁和设置过期时间必须是一个原子操作
     ❌ SETNX lock 1  →  然后 EXPIRE lock 10
        如果进程在两条命令之间崩溃，锁就永远不会释放（死锁）
     ✅ SET lock <token> NX EX 10
        用一条命令同时完成"不存在才设置"和"设置过期时间"

坑2：释放锁必须校验持有者
     ❌ DEL lock
        如果 A 的锁已超时自动释放，B 拿到了锁，此时 A 执行 DEL
        就把 B 的锁删掉了 → B 以为自己还持锁，实际没有 → 并发失控
     ✅ 用 Lua 脚本"比较 token 相同才删除"，保证原子性

坑3：锁的过期时间要大于业务执行时间
     如果业务执行超过了锁的过期时间，锁自动释放，其他请求就能进来，
     同样会出现并发问题。这时需要"锁续期"（看门狗机制）。
     本项目业务耗时短（几次数据库查询），10 秒足够。
     生产环境如果业务可能耗时较长（比如调用外部 API），
     应该用一个后台线程定期给锁续期（Redisson 的看门狗就是这么做的）。
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from app.core.cache import redis_client
from app.core.exceptions import AppException, ConflictError
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)

# 释放锁的 Lua 脚本。
# 为什么必须用 Lua？因为"比较 token"和"删除 key"必须是原子操作。
# 如果分成两步（先 GET 比较，再 DEL），两步之间锁可能刚好超时被释放
# 并被别人获取，那我们的 DEL 就会误删别人的锁。
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


class RedisLock:
    """
    Redis 分布式锁（非阻塞 + 可选等待重试）。

    使用方式一（推荐，自动释放）：
        async with RedisLock("booking:user:1", timeout=10):
            # 临界区
            ...

    使用方式二（手动控制）：
        lock = RedisLock("key")
        if await lock.acquire():
            try:
                ...
            finally:
                await lock.release()
    """

    def __init__(
        self,
        key: str,
        timeout: int = 10,
        wait_timeout: float = 3.0,
        retry_interval: float = 0.1,
    ) -> None:
        """
        :param key:           锁的键名（建议格式：业务:资源类型:资源ID）
        :param timeout:       锁的自动过期时间（秒），防止死锁
        :param wait_timeout:  获取锁的最大等待时间（秒），0 表示不等待直接失败
        :param retry_interval: 重试间隔（秒）
        """
        self.key = f"lock:{key}"
        self.timeout = timeout
        self.wait_timeout = wait_timeout
        self.retry_interval = retry_interval
        # 每个锁实例有唯一的 token，用于"只释放自己的锁"
        self.token = uuid.uuid4().hex
        self._acquired = False

    async def acquire(self) -> bool:
        """
        尝试获取锁。成功返回 True，等待超时返回 False。

        注意实现细节：用 set(key, token, nx=True, ex=timeout) 一条命令完成
        "不存在才设置"和"设置过期时间"，保证原子性。
        """
        deadline = asyncio.get_event_loop().time() + self.wait_timeout

        while True:
            try:
                # nx=True：只有 key 不存在时才设置（相当于 SETNX）
                # ex=timeout：同时设置过期时间
                # 这两者放在一次调用里，是原子的
                acquired = await redis_client.set(
                    self.key, self.token, nx=True, ex=self.timeout
                )
                if acquired:
                    self._acquired = True
                    logger.debug("获取锁成功 | key=%s", self.key)
                    return True
            except Exception as exc:
                # Redis 不可用时的降级策略。
                # 这里的选择是"放弃加锁，让业务继续"（fail-open）还是
                # "拒绝请求"（fail-close）？
                # 本项目选 fail-open：因为第 2 层的事务内检测和第 3 层的
                # 唯一约束仍然生效，即使没锁住也不会产生脏数据。
                # 如果是支付类场景，则必须 fail-close（宁可拒绝也不能冒险）。
                logger.error("Redis 锁异常，降级为无锁执行 | key=%s | %s", self.key, exc)
                return True

            # 还没到等待上限就继续重试
            if asyncio.get_event_loop().time() >= deadline:
                logger.warning("获取锁超时 | key=%s | 等待=%.1fs", self.key, self.wait_timeout)
                return False

            await asyncio.sleep(self.retry_interval)

    async def release(self) -> bool:
        """
        释放锁。

        用 Lua 脚本保证"校验持有者 + 删除"的原子性，避免误删他人的锁。
        """
        if not self._acquired:
            return False

        try:
            result = await redis_client.eval(_RELEASE_SCRIPT, 1, self.key, self.token)
            self._acquired = False
            if result:
                logger.debug("释放锁成功 | key=%s", self.key)
                return True
            # result == 0 说明锁已经不属于自己了（超时被自动释放，或被别人持有）
            logger.warning("锁已不属于当前持有者，可能已超时 | key=%s", self.key)
            return False
        except Exception as exc:
            logger.error("释放锁异常 | key=%s | %s", self.key, exc)
            return False

    @property
    def acquired(self) -> bool:
        return self._acquired


@asynccontextmanager
async def distributed_lock(
    key: str,
    timeout: int = 10,
    wait_timeout: float = 3.0,
    error_message: Optional[str] = None,
) -> AsyncGenerator[None, None]:
    """
    分布式锁的上下文管理器（最常用的形式）。

    使用示例：
        async with distributed_lock(f"booking:user:{user_id}", timeout=10):
            # 这里面的代码在同一个用户维度是串行的
            await check_conflict()
            await insert_booking()
            await commit()

    获取锁失败时抛业务异常（提示用户稍后重试），
    而不是让请求排队等待 —— 因为前端等待超过几秒的体验很差，
    不如直接告诉用户"操作太频繁"。
    """
    lock = RedisLock(key, timeout=timeout, wait_timeout=wait_timeout)

    if not await lock.acquire():
        raise ConflictError(
            ErrorCode.CONFLICT,
            error_message or "操作正在处理中，请稍后重试",
        )

    try:
        yield
    finally:
        # 无论业务成功还是抛异常，都必须释放锁。
        # 这就是用 contextmanager 的价值 —— 不可能忘记释放。
        await lock.release()


# ====================== 业务锁的键名规范 ======================

class LockKey:
    """
    锁键名集中定义。

    设计要点：**锁的粒度决定了并发能力**。
    - 锁整个预约表 → 所有用户互相阻塞，并发能力极差
    - 锁"某个用户" → 同一用户串行，不同用户互不影响 ✅
    - 锁"某台设备" → 同一设备串行，不同设备互不影响
    - 同时锁用户和设备 → 最严格，但可能死锁（两个请求各持一把等另一把）

    本项目策略：**只锁用户维度**。
    理由：需要防的核心竞态是"同一用户同时提交两个重叠时段的预约"
    （这是最常见的攻击/误操作场景，比如用户狂点提交按钮）。
    不同用户争抢同一台设备的场景，由"事务内检测 + 唯一约束"兜底，
    且冲突时会给用户明确的失败提示，体验可以接受。
    这样锁的粒度最小，并发能力最好。
    """

    @staticmethod
    def booking_by_user(user_id: int) -> str:
        return f"booking:user:{user_id}"

    @staticmethod
    def booking_audit(booking_id: int) -> str:
        return f"booking:audit:{booking_id}"

    @staticmethod
    def equipment_sync() -> str:
        """定时任务的锁，防止多实例同时执行"""
        return "job:equipment_sync"

    @staticmethod
    def token_cleanup() -> str:
        return "job:token_cleanup"
