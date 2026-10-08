"""
模型公共部分。

把「每张表都有」的字段和逻辑抽成 Mixin，避免在 8 个模型里重复写。

面试可以讲：这是「组合优于继承」的一个实践。
如果用继承（class User(BaseModel)），所有表被迫共享同一套基类；
用 Mixin 则可以按需组合 —— 比如将来某张「日志表」只需要有创建时间、
不需要有更新时间，就只混入 CreatedAtMixin 即可。
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, text
from sqlalchemy.orm import Mapped, mapped_column


class IdMixin:
    """自增主键"""

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="主键ID"
    )


class TimestampMixin:
    """
    审计时间字段。

    为什么用 server_default 而不是 Python 端 default？
    - server_default 由数据库填充，即使有人直接用 SQL 插入数据，时间也正确
    - 多条记录写入时时间戳由数据库统一生成，不会有微小偏差
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        nullable=False,
        comment="更新时间",
    )
