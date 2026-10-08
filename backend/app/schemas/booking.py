"""
预约相关的请求/响应模型，以及时间区间冲突检测算法。

**这是整个项目技术含量最高的一块，面试重点。**
"""
from datetime import date, datetime, time, timedelta
from typing import Optional

from pydantic import Field, field_validator, model_validator

from app.core.enums import BookingStatus
from app.schemas.common import ORMModel
from app.utils.url import to_static_url


# ==============================================================================
#  时间区间冲突检测算法（核心）
# ==============================================================================

def is_overlap(
    start1: time, end1: time, start2: time, end2: time
) -> bool:
    """
    判断两个时间区间是否重叠。

    ┌─────────────────────────────────────────────────────────────────┐
    │  这是本函数要解决的核心问题：给定两个时间段，判断它们有没有交集  │
    └─────────────────────────────────────────────────────────────────┘

    【思路一：枚举所有重叠场景（笨办法，容易漏）】
    两个区间重叠有 4 种情况：
        1. A 完全在 B 之前的一部分   A: |----|        B:      |----|
        2. A 完全在 B 之后的一部分   A:      |----|    B: |----|
        3. A 包含 B                  A: |--------|    B:   |--|
        4. A 被 B 包含               A:   |--|        B: |--------|
    即使穷举了这 4 种，还要小心"边界相接算不算重叠"（9:00-11:00 和 11:00-13:00）。
    代码会变成一堆 or 和 and，很容易漏一种，也很难验证正确性。

    【思路二：用补集（本项目采用，一行搞定）】
    不重叠只有两种情况：
        1. A 在 B 之前结束：  end1 <= start2
        2. A 在 B 之后开始：  start1 >= end2
    所以：重叠 = NOT (end1 <= start2 OR start1 >= end2)
                = end1 > start2 AND start1 < end2

    为什么这个解法更好？
    - 只有一行，不可能漏场景
    - "不重叠"的两种情况在数轴上一目了然，容易验证
    - 边界语义天然正确：end1 == start2 时（首尾相接），
      end1 > start2 为 False → 判定为不重叠 ✓
      这正是我们要的语义：9:00-11:00 和 11:00-13:00 不冲突

    【边界语义说明】
    本系统采用**左闭右开**语义：预约 9:00-11:00 表示占用 [9:00, 11:00)。
    所以结束时间等于开始时间不算冲突。
    如果业务要求"结束时刻也要留出清理时间"，就需要在比较时加缓冲，
    比如改成 end1 + buffer > start2。

    :return: True 表示重叠（有冲突）
    """
    return end1 > start2 and start1 < end2


def is_overlap_datetime(
    start1: datetime, end1: datetime, start2: datetime, end2: datetime
) -> bool:
    """datetime 版本的重叠判断（算法完全相同）"""
    return end1 > start2 and start1 < end2


def is_overlap_date_time(
    date1: date, start1: time, end1: time,
    date2: date, start2: time, end2: time,
) -> bool:
    """
    （日期 + 时间）版本的重叠判断。

    先把日期和时间拼成完整的 datetime，再比较。
    这样能正确处理跨日的情况（虽然本系统限制同一天内预约）。
    """
    s1 = datetime.combine(date1, start1)
    e1 = datetime.combine(date1, end1)
    s2 = datetime.combine(date2, start2)
    e2 = datetime.combine(date2, end2)
    return is_overlap_datetime(s1, e1, s2, e2)


# ==============================================================================
#  请求模型
# ==============================================================================

# 业务规则：预约时间必须对齐到 30 分钟，且不能跨天
ALLOWED_MINUTE_VALUES = (0, 30)
# 每天可预约的时间窗口
DAY_START = time(8, 0)
DAY_END = time(22, 0)
# 单次预约最短/最长时长（分钟）
MIN_DURATION_MINUTES = 30
MAX_DURATION_MINUTES = 8 * 60


class BookingCreateRequest(ORMModel):
    """
    创建预约。

    校验分四层（从便宜到昂贵）：
      1. Pydantic 类型层面：字段类型、长度、必填
      2. 本模型内的业务校验：时间格式、时长范围、日期范围
      3. service 层业务校验：设备是否存在可用、是否冲突（需要查库）
      4. 数据库约束：唯一约束兜底

    这个"分层校验"的思路很重要：能在便宜的地方拦住就不要留到后面。
    字段级别的错误不需要查数据库就能发现。
    """

    equipment_id: int = Field(..., gt=0, description="设备ID")
    booking_date: date = Field(..., description="预约日期（格式 YYYY-MM-DD）")
    start_time: time = Field(..., description="开始时间（格式 HH:MM:SS）")
    end_time: time = Field(..., description="结束时间（格式 HH:MM:SS）")
    purpose: str = Field(..., min_length=2, max_length=500, description="使用目的")
    notes: Optional[str] = Field(None, max_length=500, description="备注")

    @field_validator("start_time", "end_time")
    @classmethod
    def check_time_alignment(cls, v: time) -> time:
        """
        时间必须对齐到 30 分钟。

        为什么要有这个约束？
        因为系统的冲突检测是"通用算法"，但如果允许任意分钟
        （比如 9:07 到 9:53），排班表会变得非常难管理，
        也会出现大量"看似不冲突但实际很难用"的碎时段。
        约束到 30 分钟粒度是很多预约系统的常规做法。
        """
        if v.minute not in ALLOWED_MINUTE_VALUES or v.second != 0:
            raise ValueError("时间必须对齐到整点或半点（如 09:00、09:30）")
        return v

    @model_validator(mode="after")
    def check_time_range(self) -> "BookingCreateRequest":
        """
        时间范围的整体校验。

        注意：这类"跨字段校验"必须用 model_validator(mode="after")，
        不能用 field_validator —— 因为 field_validator 执行时
        只能看到自己那个字段的值，拿不到其他字段。
        这是 Pydantic V2 里一个常见的使用误区。
        """
        # 1. 结束必须晚于开始
        if self.end_time <= self.start_time:
            raise ValueError("结束时间必须晚于开始时间")

        # 2. 必须在允许的时间窗口内
        if self.start_time < DAY_START:
            raise ValueError(f"最早只能从 {DAY_START.strftime('%H:%M')} 开始预约")
        if self.end_time > DAY_END:
            raise ValueError(f"最晚只能预约到 {DAY_END.strftime('%H:%M')}")

        # 3. 时长范围
        start_minutes = self.start_time.hour * 60 + self.start_time.minute
        end_minutes = self.end_time.hour * 60 + self.end_time.minute
        duration = end_minutes - start_minutes

        if duration < MIN_DURATION_MINUTES:
            raise ValueError(f"单次预约不能少于 {MIN_DURATION_MINUTES} 分钟")
        if duration > MAX_DURATION_MINUTES:
            raise ValueError(
                f"单次预约不能超过 {MAX_DURATION_MINUTES // 60} 小时"
                "（如需更长时间请分多次预约）"
            )

        # 4. 日期范围：不能早于今天，最多提前 30 天
        today = date.today()
        if self.booking_date < today:
            raise ValueError("预约日期不能早于今天")
        if self.booking_date > today + timedelta(days=30):
            raise ValueError("最多只能提前 30 天预约")

        return self

    @field_validator("purpose")
    @classmethod
    def strip_purpose(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("使用目的不能为空")
        return v


class BookingAuditRequest(ORMModel):
    """管理员审核预约"""

    result: BookingStatus = Field(..., description="审核结果：approved 或 rejected")
    note: Optional[str] = Field(None, max_length=500, description="审核备注（拒绝时建议填写）")

    @field_validator("result")
    @classmethod
    def check_result(cls, v: BookingStatus) -> BookingStatus:
        if v not in (BookingStatus.APPROVED, BookingStatus.REJECTED):
            raise ValueError("审核结果只能是 approved（通过）或 rejected（拒绝）")
        return v

    @model_validator(mode="after")
    def check_reject_note(self) -> "BookingAuditRequest":
        """拒绝时要求填写原因（否则用户不知道为什么被拒，体验很糟）"""
        if self.result == BookingStatus.REJECTED and not (self.note or "").strip():
            raise ValueError("拒绝预约时必须填写原因")
        return self


class BookingCancelRequest(ORMModel):
    """取消预约"""

    reason: Optional[str] = Field(None, max_length=200, description="取消原因")


# ==============================================================================
#  响应模型
# ==============================================================================

class BookingUserBrief(ORMModel):
    """预约人简要信息"""

    id: int
    username: str
    real_name: str


class BookingEquipmentBrief(ORMModel):
    """预约的设备简要信息"""

    id: int
    name: str
    model: str
    image: Optional[str] = None

    @field_validator("image", mode="before")
    @classmethod
    def convert_image(cls, v):
        return to_static_url(v)


class BookingResponse(ORMModel):
    """
    预约完整信息。

    注意这里把 user 和 equipment 的信息**扁平化 + 嵌套**都提供了：
    - 扁平的 user_name / equipment_name：列表页直接显示，不用层层取值
    - 嵌套的完整对象：详情页需要更多字段

    这样做看起来有点冗余，但能显著减少前端代码的取值层级
    （原项目前端有大量 `row.equipment?.name || row.equipment_name` 这种兜底代码）。
    """

    id: int
    status: BookingStatus

    # 时间
    booking_date: date
    start_time: time
    end_time: time

    # 内容
    purpose: str
    notes: Optional[str] = None

    # 审核信息
    audit_note: Optional[str] = None
    audited_at: Optional[datetime] = None
    auditor_name: Optional[str] = Field(None, description="审核人姓名")

    # 扁平字段（方便列表直接显示）
    user_id: int
    user_name: Optional[str] = None
    equipment_id: int
    equipment_name: Optional[str] = None
    equipment_image: Optional[str] = None

    # 嵌套对象（方便详情页使用）
    equipment: Optional[BookingEquipmentBrief] = None
    user: Optional[BookingUserBrief] = None

    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # 派生字段：方便前端直接判断该显示什么按钮，不用自己写状态判断逻辑
    can_cancel: bool = Field(False, description="当前用户是否可以取消")
    can_audit: bool = Field(False, description="当前用户是否可以审核")

    @field_validator("equipment_image", mode="before")
    @classmethod
    def convert_image(cls, v):
        return to_static_url(v)


class BookingConflictCheckRequest(ORMModel):
    """
    冲突预检请求。

    用途：前端在用户选完时间段、还没提交时就问一次"这个时段能用吗"，
    可以提前给出反馈，避免用户填完一堆信息才被拒绝。
    这种"预检接口"是很实用的体验优化。
    """

    equipment_id: int = Field(..., gt=0)
    booking_date: date
    start_time: time
    end_time: time


class BookingConflictCheckResponse(ORMModel):
    """冲突预检结果"""

    available: bool = Field(..., description="该时段是否可用")
    reason: Optional[str] = Field(None, description="不可用的原因")
    conflicts: list[dict] = Field(
        default_factory=list, description="冲突的预约（时间已占用，仅返回必要的脱敏信息）"
    )
    # 同一天的已占用时段（方便前端画时间轴）
    booked_slots: list[dict] = Field(default_factory=list, description="当天已占用的时段")
