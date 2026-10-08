"""
业务枚举常量。

设计要点（面试可以讲）：
1. 用 Python 的 str + Enum，既能在代码里做类型安全比较（UserStatus.ACTIVE），
   又能直接序列化成字符串（"active"）进数据库和 API 响应。
2. 数据库层也定义成 ENUM，做到「代码枚举」与「数据库枚举」两边一致，
   避免出现数据库里存了 3、代码里只认 "completed" 这种脏数据。
3. 状态机（预约状态流转）集中在这里定义，而不是散落在各个路由里写 if。
   这样"哪些状态能转到哪些状态"只有一个地方说了算，改起来不会漏。
"""
from enum import Enum


class StrEnum(str, Enum):
    """继承 str 的枚举基类，方便直接当字符串用"""

    def __str__(self) -> str:
        return self.value


# ====================== 用户相关 ======================

class UserStatus(StrEnum):
    """用户状态"""

    DISABLED = "disabled"  # 被管理员禁用，不能登录
    ACTIVE = "active"      # 正常


class RoleCode(StrEnum):
    """角色编码（用编码而不是自增 id，避免依赖具体的主键值）"""

    STUDENT = "student"    # 学生：普通用户，只能预约和查看自己的数据
    TEACHER = "teacher"    # 教师：可以预约，可见范围更广（预留）
    ADMIN = "admin"        # 管理员：可审核预约、管理设备与用户


# ====================== 设备相关 ======================

class EquipmentStatus(StrEnum):
    """设备状态"""

    AVAILABLE = "available"      # 可用，可被预约
    BUSY = "busy"                # 使用中（由定时任务根据预约自动流转）
    MAINTENANCE = "maintenance"  # 维护中，不可预约，且不会被定时任务改成可用


# ====================== 预约相关 ======================

class BookingStatus(StrEnum):
    """预约状态"""

    PENDING = "pending"      # 待审核
    APPROVED = "approved"    # 已通过
    REJECTED = "rejected"    # 已拒绝
    CANCELLED = "cancelled"  # 已取消
    COMPLETED = "completed"  # 已完成（由定时任务在预约结束后自动流转）


# 预约状态机：value 是「从该状态可以流转到的状态集合」
# 这个表是整个系统里唯一一处定义"状态怎么变"的地方。
BOOKING_STATUS_TRANSITIONS: dict[BookingStatus, set[BookingStatus]] = {
    BookingStatus.PENDING: {
        BookingStatus.APPROVED,   # 管理员审核通过
        BookingStatus.REJECTED,   # 管理员审核拒绝
        BookingStatus.CANCELLED,  # 用户主动取消
    },
    BookingStatus.APPROVED: {
        BookingStatus.COMPLETED,  # 定时任务：预约时间已过
        BookingStatus.CANCELLED,  # 用户主动取消
    },
    # 以下三个是终态，不允许再流转
    BookingStatus.REJECTED: set(),
    BookingStatus.CANCELLED: set(),
    BookingStatus.COMPLETED: set(),
}

# 占用设备时间段的"有效状态"：
# 只有这两个状态的预约才会真正占住设备，冲突检测时也只查这两个状态。
# 为什么不含 completed？因为已完成的预约其时间段已经过去了，
# 不会与"未来的新预约"产生冲突（新预约的日期必然 >= 今天）。
BOOKING_ACTIVE_STATUSES = (BookingStatus.PENDING, BookingStatus.APPROVED)

# 上面常量的「字符串形式」，专供 SQL 查询使用（数据库里存的是字符串）。
#
# 为什么要在这个模块里统一定义？
# 因为它曾经被散落在三处使用：
#   - repositories/booking.py      用了本地定义的 ACTIVE_STATUS_VALUES
#   - repositories/equipment.py    硬编码 ["pending", "approved"]
#   - services/statistics_service.py  写 BookingStatus.PENDING.value, .APPROVED.value
# 一旦业务变化（比如新增一个也算"占用"的状态），必须记得改三处，
# 漏掉任何一处就会出现「设备列表显示的预约数」和「统计页的预约数」对不上
# —— 这是典型的静默数据不一致，而且很难被发现。
# 现在统一从这里导入，「哪些状态算有效」只有这一个地方说了算。
BOOKING_ACTIVE_STATUS_VALUES: list[str] = [s.value for s in BOOKING_ACTIVE_STATUSES]

# 超时未审核的处理方式：
# 定时任务会把「预约日期已过、但仍处于待审核」的预约自动取消。
# 为什么需要这个？因为定时任务原本只处理 approved 状态的预约（把过期的标记为已完成），
# pending 的预约永远不会流转 —— 一条几个月前的待审核预约会永远算作"当前有效预约"，
# 让这个统计数字虚高。
BOOKING_TIMEOUT_CANCEL_REASON = "超过预约日期仍未审核，系统自动取消"


def can_transition(from_status: BookingStatus, to_status: BookingStatus) -> bool:
    """
    判断预约状态能否从 from_status 流转到 to_status。

    使用示例：
        if not can_transition(booking.status, BookingStatus.APPROVED):
            raise BusinessError(f"当前状态 {booking.status} 不允许通过审核")
    """
    return to_status in BOOKING_STATUS_TRANSITIONS.get(from_status, set())
