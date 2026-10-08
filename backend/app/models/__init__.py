"""
ORM 模型包。

分文件组织的理由：
- 一个文件一个聚合根，职责清晰
- 通过 __init__.py 统一导出，其他模块 from app.models import User 即可，
  不需要关心它定义在哪个文件里

注意：所有模型都必须被导入一次，SQLAlchemy 才能完成关系映射
（字符串形式的 relationship("Booking") 需要在 mapper 配置阶段能找到该类）。
所以这里把所有模型都导出，main.py 启动时 import app.models 即可。
"""
from app.models.base import TimestampMixin
from app.models.booking import Booking
from app.models.collection import EquipmentCollection
from app.models.equipment import Equipment, EquipmentCategory, Laboratory
from app.models.user import Role, User, UserRole, UserToken

__all__ = [
    "TimestampMixin",
    "User",
    "Role",
    "UserRole",
    "UserToken",
    "EquipmentCategory",
    "Laboratory",
    "Equipment",
    "Booking",
    "EquipmentCollection",
]
