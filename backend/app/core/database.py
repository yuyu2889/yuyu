"""
数据库连接与会话管理。

设计要点（面试可以讲）：
1. 用 SQLAlchemy 2.0 的异步 API（create_async_engine + AsyncSession），
   配合 aiomysql 驱动。异步的意义在于：数据库 IO 等待期间事件循环可以去
   处理其他请求，单进程能扛的并发量比同步模型高一个数量级。

2. Base 上定义了三个所有表都有的审计字段（id / created_at / updated_at），
   避免每张表重复写。用 Mapped[...] 类型注解是 SQLAlchemy 2.0 的推荐写法，
   IDE 和 mypy 都能正确推断类型。

3. get_db 依赖用 async generator 实现「每请求一个会话，请求结束必归还」。
   注意这里没有用 try/except 吞掉异常——异常要正常抛出去，
   由全局异常处理器统一转换成响应，这样才不会丢失错误上下文。
"""
from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import DateTime, Integer, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings


class Base(DeclarativeBase):
    """
    所有 ORM 模型的基类。

    这里集中定义三张表都需要的审计字段，子类直接继承即可。
    """

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="主键ID")

    # 创建时间：由数据库的 CURRENT_TIMESTAMP 填充（server_default），
    # 而不是由 Python 填充。好处是即使直接用 SQL 插数据，时间也是对的。
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )

    # 更新时间：数据库层 ON UPDATE CURRENT_TIMESTAMP，任何更新都会自动刷新
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )

    def to_dict(self) -> dict:
        """转成字典（不含关系属性），调试和日志用"""
        return {
            c.name: getattr(self, c.name)
            for c in self.__table__.columns
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id}>"


# ====================== 引擎与会话工厂 ======================

engine = create_async_engine(
    settings.database.url,
    echo=settings.database.echo,
    # 连接池配置说明：
    # pool_size     常驻连接数，Linux 默认 5、Windows 也是 5
    # max_overflow  高峰期额外临时创建的连接数，用完即销毁
    # pool_recycle  连接存活时间上限（秒）。MySQL 默认 8 小时会主动断开空闲连接
    #               （wait_timeout），如果代码还握着这个已断开的连接就会报
    #               "MySQL server has gone away"。所以要在它之前主动回收。
    # pool_pre_ping 从池里取连接前先 ping 一下，失效的就丢弃重建。
    #               这是解决 "server has gone away" 最省心的办法，
    #               代价是每次取连接多一次往返（约 0.1ms，可接受）。
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    # expire_on_commit=False 很重要：
    # 默认 True 时，commit 后所有对象属性都会过期，之后再访问会触发新的
    # SELECT（在异步环境里这个隐式 IO 会直接抛 MissingGreenlet 错误）。
    # 设为 False 后，commit 后对象仍可直接读取，避免这类坑。
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI 依赖：为每个请求提供一个数据库会话。

    使用方式：
        async def some_route(db: AsyncSession = Depends(get_db)):
            ...

    会话在请求结束时自动关闭并归还连接池。
    如果处理过程中抛异常，事务会被自动回滚（由 async with 保证）。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            # 显式回滚，确保未提交的改动不会污染连接池里的连接
            await session.rollback()
            raise


async def check_database_connection() -> bool:
    """
    数据库连通性检查。应用启动时调用，连不上就快速失败，
    而不是等第一个请求进来才发现连不上。
    """
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def dispose_engine() -> None:
    """应用关闭时释放连接池"""
    await engine.dispose()
