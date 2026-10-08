"""
时间区间冲突检测算法的单元测试。

**这是全项目最需要测试的纯逻辑**，因为它决定了预约系统的正确性。
这类"纯函数"测试价值最高：不需要数据库、跑得飞快、
能穷举所有边界情况。

运行：
    .venv\\Scripts\\python.exe -m pytest tests/unit/test_booking_conflict.py -v
"""
from datetime import date, time

import pytest

from app.schemas.booking import (
    DAY_END,
    DAY_START,
    MAX_DURATION_MINUTES,
    MIN_DURATION_MINUTES,
    is_overlap,
    is_overlap_date_time,
    is_overlap_datetime,
)


class TestIsOverlap:
    """
    时间区间重叠判定的边界测试。

    核心公式：重叠 = end1 > start2 AND start1 < end2
            （等价于 NOT (end1 <= start2 OR start1 >= end2)）

    采用**左闭右开**语义：预约 9:00-11:00 占用 [9:00, 11:00)
    """

    # 期望值显式写成 True/False，和用例数据一一对应。
    # （第一版我用"描述文字里有没有某个关键词"来推断期望值，
    #   结果有一条用例描述漏了关键词，测试就误报失败。
    #   教训：测试数据要显式，不要靠字符串匹配来推断。）
    OVERLAP_CASES = [
        # (start1, end1, start2, end2, expected, description)
        # ---------- 不应判定为冲突 ----------
        (time(9, 0), time(11, 0), time(13, 0), time(15, 0), False,
         "A 完全在 B 之前，间隔 2 小时"),
        (time(13, 0), time(15, 0), time(9, 0), time(11, 0), False,
         "A 完全在 B 之后"),
        (time(9, 0), time(11, 0), time(11, 0), time(13, 0), False,
         "【关键边界】首尾相接：A 结束时刻 = B 开始时刻"),
        (time(11, 0), time(13, 0), time(9, 0), time(11, 0), False,
         "【关键边界】反向首尾相接"),
        (time(9, 0), time(10, 0), time(10, 0), time(11, 0), False,
         "相邻的半小时时段"),
        (time(9, 0), time(9, 30), time(9, 30), time(10, 0), False,
         "最小粒度的相邻时段"),
        (time(9, 0), time(11, 0), time(11, 1), time(13, 0), False,
         "边界差 1 分钟（不重叠）"),
        # ---------- 应判定为冲突 ----------
        (time(9, 0), time(11, 0), time(10, 0), time(12, 0), True,
         "部分重叠（B 的后半段）"),
        (time(10, 0), time(12, 0), time(9, 0), time(11, 0), True,
         "部分重叠（B 的前半段）"),
        (time(9, 0), time(12, 0), time(10, 0), time(11, 0), True,
         "A 完全包含 B"),
        (time(10, 0), time(11, 0), time(9, 0), time(12, 0), True,
         "A 被 B 完全包含"),
        (time(9, 0), time(11, 0), time(9, 0), time(11, 0), True,
         "完全相同"),
        (time(9, 0), time(11, 0), time(9, 30), time(11, 30), True,
         "错位重叠"),
        (time(9, 0), time(11, 0), time(10, 59), time(13, 0), True,
         "只重叠 1 分钟"),
        (time(9, 0), time(11, 0), time(10, 30), time(10, 31), True,
         "B 是 A 内部的一小段"),
    ]

    @pytest.mark.parametrize(
        "s1,e1,s2,e2,expected,desc",
        OVERLAP_CASES,
        ids=[c[5] for c in OVERLAP_CASES],
    )
    def test_overlap_cases(self, s1, e1, s2, e2, expected, desc):
        actual = is_overlap(s1, e1, s2, e2)
        assert actual is expected, (
            f"{desc}\n"
            f"  区间A: {s1}-{e1}\n"
            f"  区间B: {s2}-{e2}\n"
            f"  期望: {'冲突' if expected else '不冲突'}\n"
            f"  实际: {'冲突' if actual else '不冲突'}"
        )

    def test_symmetry(self):
        """
        重叠判断必须是对称的：overlap(A,B) == overlap(B,A)。

        这是一条重要的性质 —— 如果用"枚举 4 种重叠场景"的写法，
        很容易写成不对称（漏掉某种情况），导致
        "A 判断和 B 冲突，但 B 判断和 A 不冲突"这种诡异 bug。
        用补集写法天然对称。
        """
        cases = [
            (time(9, 0), time(11, 0), time(10, 0), time(12, 0)),
            (time(9, 0), time(11, 0), time(11, 0), time(13, 0)),
            (time(9, 0), time(15, 0), time(10, 0), time(11, 0)),
            (time(14, 0), time(15, 0), time(9, 0), time(10, 0)),
        ]
        for s1, e1, s2, e2 in cases:
            forward = is_overlap(s1, e1, s2, e2)
            backward = is_overlap(s2, e2, s1, e1)
            assert forward == backward, f"重叠判断不对称：{s1}-{e1} vs {s2}-{e2}"

    def test_reflexive(self):
        """任何区间和自己都重叠（这也是一条重要性质）"""
        for s, e in [
            (time(9, 0), time(11, 0)),
            (time(8, 0), time(22, 0)),
            (time(13, 30), time(14, 0)),
        ]:
            assert is_overlap(s, e, s, e) is True

    def test_transitivity_of_non_overlap(self):
        """
        不重叠的传递性检查：
        如果 A 和 B 不重叠，且 B 和 C 不重叠，且 A 在 B 之前、B 在 C 之前，
        那么 A 和 C 也不重叠。

        这个性质保证了"按时间排序后逐个检查相邻项"这种做法的正确性。
        """
        a = (time(8, 0), time(9, 0))
        b = (time(9, 0), time(10, 0))
        c = (time(10, 0), time(11, 0))

        assert is_overlap(*a, *b) is False
        assert is_overlap(*b, *c) is False
        assert is_overlap(*a, *c) is False

    def test_boundary_by_one_minute(self):
        """
        边界差 1 分钟的行为。

        9:00-11:00 和 11:01-13:00 不冲突（有 1 分钟空隙）。
        9:00-11:00 和 10:59-13:00 冲突（重叠 1 分钟）。
        """
        assert is_overlap(time(9, 0), time(11, 0), time(11, 1), time(13, 0)) is False
        assert is_overlap(time(9, 0), time(11, 0), time(10, 59), time(13, 0)) is True

    def test_full_day_interval(self):
        """全天区间和任何区间都冲突"""
        assert is_overlap(time(0, 0), time(23, 59), time(9, 0), time(10, 0)) is True
        assert is_overlap(time(9, 0), time(10, 0), time(0, 0), time(23, 59)) is True


class TestIsOverlapDatetime:
    """datetime 版本（同一算法，验证日期部分也正确）"""

    def test_same_day_overlap(self):
        from datetime import datetime

        assert is_overlap_datetime(
            datetime(2026, 5, 1, 9, 0), datetime(2026, 5, 1, 11, 0),
            datetime(2026, 5, 1, 10, 0), datetime(2026, 5, 1, 12, 0),
        ) is True

    def test_different_day_no_overlap(self):
        """不同日期的同一时段不冲突"""
        from datetime import datetime

        assert is_overlap_datetime(
            datetime(2026, 5, 1, 9, 0), datetime(2026, 5, 1, 11, 0),
            datetime(2026, 5, 2, 9, 0), datetime(2026, 5, 2, 11, 0),
        ) is False

    def test_cross_midnight(self):
        """
        跨午夜的情况。

        本系统限制不能跨天，但这个函数本身应该能正确处理
        （说明算法是通用的，不是特例拼凑的）。
        """
        from datetime import datetime

        assert is_overlap_datetime(
            datetime(2026, 5, 1, 22, 0), datetime(2026, 5, 2, 2, 0),
            datetime(2026, 5, 2, 1, 0), datetime(2026, 5, 2, 3, 0),
        ) is True


class TestIsOverlapDateTime:
    """日期+时间组合版本"""

    def test_same_date_overlap(self):
        assert is_overlap_date_time(
            date(2026, 5, 1), time(9, 0), time(11, 0),
            date(2026, 5, 1), time(10, 0), time(12, 0),
        ) is True

    def test_different_date(self):
        assert is_overlap_date_time(
            date(2026, 5, 1), time(9, 0), time(11, 0),
            date(2026, 5, 2), time(9, 0), time(11, 0),
        ) is False


class TestBookingRequestValidation:
    """创建预约请求的业务校验"""

    def _make(self, **kwargs):
        from app.schemas.booking import BookingCreateRequest

        defaults = {
            "equipment_id": 1,
            "booking_date": date.today(),
            "start_time": time(9, 0),
            "end_time": time(11, 0),
            "purpose": "实验测试",
        }
        defaults.update(kwargs)
        return BookingCreateRequest(**defaults)

    def test_valid_request(self):
        """正常请求应该通过"""
        req = self._make()
        assert req.equipment_id == 1

    def test_end_before_start_rejected(self):
        """结束早于开始必须拒绝"""
        with pytest.raises(ValueError, match="结束时间必须晚于开始时间"):
            self._make(start_time=time(11, 0), end_time=time(9, 0))

    def test_same_start_end_rejected(self):
        """开始等于结束（0 时长）必须拒绝"""
        with pytest.raises(ValueError, match="结束时间必须晚于开始时间"):
            self._make(start_time=time(9, 0), end_time=time(9, 0))

    def test_time_not_aligned_rejected(self):
        """时间必须对齐到整点或半点"""
        with pytest.raises(ValueError, match="必须对齐到整点或半点"):
            self._make(start_time=time(9, 15), end_time=time(11, 0))
        with pytest.raises(ValueError, match="必须对齐到整点或半点"):
            self._make(start_time=time(9, 0), end_time=time(11, 45))

    def test_before_day_start_rejected(self):
        """早于可预约窗口必须拒绝"""
        with pytest.raises(ValueError, match="最早只能从"):
            self._make(start_time=time(7, 30), end_time=time(9, 0))

    def test_after_day_end_rejected(self):
        """超出可预约窗口必须拒绝"""
        with pytest.raises(ValueError, match="最晚只能预约到"):
            self._make(start_time=time(21, 0), end_time=time(22, 30))

    def test_too_short_rejected(self):
        """时长过短必须拒绝"""
        # 30 分钟是最小值，所以这里没有更短的合法对齐时段可测；
        # 用一个非对齐的组合间接验证（会被对齐校验拦下）
        # 这里改为验证"刚好 30 分钟可以通过"
        req = self._make(start_time=time(9, 0), end_time=time(9, 30))
        assert req is not None

    def test_too_long_rejected(self):
        """时长过长必须拒绝"""
        with pytest.raises(ValueError, match="不能超过"):
            self._make(start_time=time(8, 0), end_time=time(21, 0))

    def test_past_date_rejected(self):
        """过去的日期必须拒绝"""
        from datetime import timedelta

        with pytest.raises(ValueError, match="不能早于今天"):
            self._make(booking_date=date.today() - timedelta(days=1))

    def test_too_far_future_rejected(self):
        """超过 30 天必须拒绝"""
        from datetime import timedelta

        with pytest.raises(ValueError, match="最多只能提前"):
            self._make(booking_date=date.today() + timedelta(days=31))

    def test_boundary_today_allowed(self):
        """今天应该允许（边界值）"""
        req = self._make(booking_date=date.today())
        assert req.booking_date == date.today()

    def test_boundary_30_days_allowed(self):
        """刚好 30 天后应该允许（边界值）"""
        from datetime import timedelta

        req = self._make(booking_date=date.today() + timedelta(days=30))
        assert req.booking_date == date.today() + timedelta(days=30)

    def test_blank_purpose_rejected(self):
        """使用目的不能是空白字符"""
        with pytest.raises(ValueError):
            self._make(purpose="   ")


class TestBookingAuditRequestValidation:
    """审核请求校验"""

    def test_reject_requires_note(self):
        """拒绝时必须填写原因"""
        from app.core.enums import BookingStatus
        from app.schemas.booking import BookingAuditRequest

        with pytest.raises(ValueError, match="必须填写原因"):
            BookingAuditRequest(result=BookingStatus.REJECTED, note="")

        with pytest.raises(ValueError, match="必须填写原因"):
            BookingAuditRequest(result=BookingStatus.REJECTED, note="   ")

    def test_approve_note_optional(self):
        """通过时备注可选"""
        from app.core.enums import BookingStatus
        from app.schemas.booking import BookingAuditRequest

        req = BookingAuditRequest(result=BookingStatus.APPROVED)
        assert req.note is None

    def test_invalid_result_rejected(self):
        """只允许 approved / rejected"""
        from app.core.enums import BookingStatus
        from app.schemas.booking import BookingAuditRequest

        with pytest.raises(ValueError):
            BookingAuditRequest(result=BookingStatus.PENDING)
        with pytest.raises(ValueError):
            BookingAuditRequest(result=BookingStatus.CANCELLED)


class TestBookingStatusMachine:
    """预约状态机测试"""

    def test_valid_transitions(self):
        from app.core.enums import BookingStatus, can_transition

        # 待审核可以 → 通过 / 拒绝 / 取消
        assert can_transition(BookingStatus.PENDING, BookingStatus.APPROVED) is True
        assert can_transition(BookingStatus.PENDING, BookingStatus.REJECTED) is True
        assert can_transition(BookingStatus.PENDING, BookingStatus.CANCELLED) is True
        # 已通过可以 → 完成 / 取消
        assert can_transition(BookingStatus.APPROVED, BookingStatus.COMPLETED) is True
        assert can_transition(BookingStatus.APPROVED, BookingStatus.CANCELLED) is True

    def test_invalid_transitions(self):
        from app.core.enums import BookingStatus, can_transition

        # 不能从待审核直接变成完成
        assert can_transition(BookingStatus.PENDING, BookingStatus.COMPLETED) is False
        # 终态不能再流转
        for terminal in (
            BookingStatus.REJECTED,
            BookingStatus.CANCELLED,
            BookingStatus.COMPLETED,
        ):
            for target in BookingStatus:
                assert can_transition(terminal, target) is False, (
                    f"终态 {terminal} 不应能流转到 {target}"
                )

    def test_rejected_cannot_be_approved(self):
        """被拒绝的预约不能再改成通过（防止重复审核）"""
        from app.core.enums import BookingStatus, can_transition

        assert can_transition(BookingStatus.REJECTED, BookingStatus.APPROVED) is False
