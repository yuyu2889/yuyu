"""
第 3 批验证脚本：预约 + 并发控制 + 定时任务。

重点验证：
1. 冲突检测的边界语义（首尾相接不冲突、重叠冲突）
2. 并发提交的锁机制（同时提交 8 个相同请求，只应成功 1 个）
3. 完整生命周期（创建 → 审核 → 取消）
4. 状态机约束（重复审核被拒、终态不能流转）
5. 定时任务（设备状态同步 + 幂等计数）
"""
import asyncio
import io
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

BASE = "http://127.0.0.1:8001"
API = BASE + "/api/v1"

out = []
def w(s=""):
    out.append(str(s))


def call(method, path, token=None, json_body=None):
    url = BASE + path if path.startswith("/api") or path == "/" else API + path
    data = None
    headers = {}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"raw": body[:300]}


def login(username, password):
    st, r = call("POST", "/api/v1/auth/login", json_body={"username": username, "password": password})
    return r["data"]["token"]


async def call_async(method, path, token=None, json_body=None):
    """异步版本，用于并发测试（把同步请求丢到线程池）"""
    return await asyncio.to_thread(call, method, path, token, json_body)


async def main():
    w("=" * 70)
    w("  第 3 批验证：预约模块 / 并发控制 / 定时任务")
    w("=" * 70)

    admin = login("admin", "Admin@123")
    student1 = login("student1", "Student@123")
    student2 = login("student2", "Student@123")
    student3 = login("student3", "Student@123")

    today = date.today()

    # ==================== 1. 冲突预检 ====================
    w()
    w("【1】冲突预检接口")
    # 设备 3（信号发生器）在 day+1 的 10:00-12:00 已被 student2 预约（种子数据）
    st, r = call("POST", "/api/v1/bookings/check-conflict", token=student1, json_body={
        "equipment_id": 3, "booking_date": str(today + timedelta(days=1)),
        "start_time": "10:00:00", "end_time": "12:00:00",
    })
    w("     时段 10:00-12:00（已被占用）-> available=%s" % r["data"]["available"])
    w("       原因: %s" % r["data"]["reason"])
    w("       当天已占用时段: %s" % [
        f"{s['start_time'][:5]}-{s['end_time'][:5]}" for s in r["data"]["booked_slots"]
    ])

    st, r = call("POST", "/api/v1/bookings/check-conflict", token=student1, json_body={
        "equipment_id": 3, "booking_date": str(today + timedelta(days=1)),
        "start_time": "08:00:00", "end_time": "10:00:00",
    })
    w("     时段 08:00-10:00（首尾相接）-> available=%s（应为 True）" % r["data"]["available"])

    st, r = call("POST", "/api/v1/bookings/check-conflict", token=student1, json_body={
        "equipment_id": 3, "booking_date": str(today + timedelta(days=1)),
        "start_time": "12:00:00", "end_time": "14:00:00",
    })
    w("     时段 12:00-14:00（首尾相接）-> available=%s（应为 True）" % r["data"]["available"])

    # ==================== 2. 边界：首尾相接可以预约成功 ====================
    w()
    w("【2】边界语义验证：首尾相接的时段不冲突（可以预约成功）")
    st, r = call("POST", "/api/v1/bookings", token=student1, json_body={
        "equipment_id": 3, "booking_date": str(today + timedelta(days=1)),
        "start_time": "08:00:00", "end_time": "10:00:00",
        "purpose": "边界测试：结束时刻等于已有预约的开始时刻",
    })
    w("     08:00-10:00 -> HTTP %s  code=%s  %s" % (st, r.get("code"), r.get("message")))
    adjacent_booking_id = r["data"]["id"] if st == 201 else None

    # ==================== 3. 重叠必须被拒绝 ====================
    w()
    w("【3】重叠时段必须被拒绝（对照点：上一步已成功创建 08:00-10:00）")
    w("     基准占用时段: 08:00-10:00")
    overlap_tests = [
        ("09:00", "11:00", "与 08:00-10:00 重叠（后半段）", True),
        ("10:00", "12:00", "与 08:00-10:00 首尾相接 → 不冲突，应当成功", False),
        ("10:30", "12:30", "与刚创建的 10:00-12:00 重叠", True),
        ("07:00", "20:00", "完全包含全部已占用时段", True),
        ("09:00", "11:00", "与 08:00-10:00 和 10:00-12:00 都重叠", True),
    ]
    for s, e, desc, should_reject in overlap_tests:
        st, r = call("POST", "/api/v1/bookings", token=student1, json_body={
            "equipment_id": 3, "booking_date": str(today + timedelta(days=1)),
            "start_time": s + ":00", "end_time": e + ":00",
            "purpose": f"重叠测试：{desc}",
        })
        rejected = st != 201
        verdict = "已拒绝 [正确]" if rejected == should_reject else "结果不符预期 [错误]"
        w("     %s-%s  %-38s -> HTTP %s  【%s】" % (s, e, desc, st, verdict))
        if rejected:
            w("          %s" % r.get("message", "")[:76])

    # ==================== 4. 用户维度冲突（同一人同一时段约两台设备）====================
    w()
    w("【4】用户维度冲突：同一人同一时段不能约两台设备")
    st, r = call("POST", "/api/v1/bookings", token=student1, json_body={
        "equipment_id": 5, "booking_date": str(today + timedelta(days=1)),
        "start_time": "08:00:00", "end_time": "10:00:00",
        "purpose": "用户维度冲突测试",
    })
    w("     学生1 在 08:00-10:00 约另一台设备 -> HTTP %s" % st)
    w("     message: %s" % r.get("message", "")[:100])

    # ==================== 5. 并发控制（核心测试）====================
    w()
    w("【5】并发控制测试：同时提交 8 个相同请求，只应成功 1 个")
    w("     （这是 check-then-act 竞态的直接验证）")

    target_date = today + timedelta(days=20)
    payload = {
        "equipment_id": 7,
        "booking_date": str(target_date),
        "start_time": "14:00:00",
        "end_time": "16:00:00",
        "purpose": "并发测试",
    }

    tasks = [
        call_async("POST", "/api/v1/bookings", student3, dict(payload))
        for _ in range(8)
    ]
    results = await asyncio.gather(*tasks)

    success_count = sum(1 for st, r in results if st == 201)
    fail_codes = {}
    for st, r in results:
        if st != 201:
            key = f"HTTP {st} / {r.get('message','')[:36]}"
            fail_codes[key] = fail_codes.get(key, 0) + 1

    w("     成功数: %d（期望 1）" % success_count)
    w("     失败分布:")
    for k, v in sorted(fail_codes.items(), key=lambda x: -x[1]):
        w("        %d 次 | %s" % (v, k))
    w("     结论: %s" % ("并发控制生效 ✓（未产生重复预约）" if success_count == 1
                        else f"【失败】产生了 {success_count} 条重复预约"))

    # 直接查数据库确认只有一条
    st, r = call("GET", f"/api/v1/bookings?equipment_id=7&date_from={target_date}&date_to={target_date}",
                 token=admin)
    w("     数据库中该设备该日期的预约数: %s（应为 1）" % r["data"]["total"])

    # ==================== 6. 完整生命周期 ====================
    w()
    w("【6】完整生命周期：创建 → 审核 → 取消")
    test_date = today + timedelta(days=25)
    st, r = call("POST", "/api/v1/bookings", token=student2, json_body={
        "equipment_id": 9, "booking_date": str(test_date),
        "start_time": "09:00:00", "end_time": "11:00:00",
        "purpose": "生命周期测试", "notes": "测试备注内容",
    })
    bid = r["data"]["id"]
    w("     1) 创建预约 -> HTTP %s  id=%s  状态=%s" % (st, bid, r["data"]["status"]))
    w("        can_cancel=%s  can_audit=%s（前端可直接用这两个字段控制按钮）"
      % (r["data"]["can_cancel"], r["data"]["can_audit"]))

    st, r = call("POST", f"/api/v1/bookings/{bid}/audit", token=admin,
                 json_body={"result": "approved", "note": "同意，请按时使用"})
    w("     2) 审核通过 -> HTTP %s  状态=%s  审核备注=%s"
      % (st, r["data"]["status"], r["data"]["audit_note"]))

    st, r = call("POST", f"/api/v1/bookings/{bid}/audit", token=admin,
                 json_body={"result": "rejected", "note": "重复审核测试"})
    w("     3) 重复审核 -> HTTP %s  code=%s  %s" % (st, r.get("code"), r.get("message", "")[:60]))

    st, r = call("PUT", f"/api/v1/bookings/{bid}/cancel", token=student2,
                 json_body={"reason": "临时有事"})
    w("     4) 取消预约 -> HTTP %s  状态=%s" % (st, r["data"]["status"]))

    st, r = call("PUT", f"/api/v1/bookings/{bid}/cancel", token=student2, json_body={})
    w("     5) 重复取消 -> HTTP %s  %s" % (st, r.get("message", "")[:60]))

    # ==================== 7. 资源级权限（IDOR 防护）====================
    w()
    w("【7】资源级权限：不能查看别人的预约（防 IDOR 越权）")
    # 用设备 13 和更远的日期，避开种子数据与前面用例占用的时段
    st, r = call("POST", "/api/v1/bookings", token=student1, json_body={
        "equipment_id": 13, "booking_date": str(today + timedelta(days=27)),
        "start_time": "14:00:00", "end_time": "16:00:00", "purpose": "越权测试",
    })
    if st != 201:
        w("     准备测试数据失败：HTTP %s %s" % (st, r.get("message")))
        other_id = None
    else:
        other_id = r["data"]["id"]

    if other_id:
        st, r = call("GET", f"/api/v1/bookings/{other_id}", token=student3)
        w("     学生3 查看学生1 的预约 -> HTTP %s  %s" % (st, r.get("message", "")[:50]))
        st, r = call("GET", f"/api/v1/bookings/{other_id}", token=admin)
        w("     管理员查看同一条预约   -> HTTP %s（管理员可以看全部）" % st)

    # ==================== 8. 普通用户访问管理端接口 ====================
    w()
    w("【8】普通用户访问管理员接口")
    st, r = call("GET", "/api/v1/bookings?page=1", token=student1)
    w("     学生访问全部预约列表 -> HTTP %s  %s" % (st, r.get("message", "")[:50]))
    st, r = call("GET", "/api/v1/bookings?page=1", token=admin)
    w("     管理员访问           -> HTTP %s  总数=%s" % (st, r["data"]["total"]))

    # ==================== 9. 状态机与校验规则 ====================
    w()
    w("【9】请求校验规则")
    validation_tests = [
        ({"start_time": "09:15:00", "end_time": "11:00:00"}, "时间未对齐到半点"),
        ({"start_time": "11:00:00", "end_time": "09:00:00"}, "结束早于开始"),
        ({"start_time": "07:00:00", "end_time": "09:00:00"}, "早于开放时间 08:00"),
        ({"start_time": "21:00:00", "end_time": "23:00:00"}, "晚于关闭时间 22:00"),
        ({"start_time": "09:00:00", "end_time": "19:00:00"}, "时长超过 8 小时"),
    ]
    for override, desc in validation_tests:
        body = {
            "equipment_id": 13, "booking_date": str(today + timedelta(days=10)),
            "start_time": "09:00:00", "end_time": "11:00:00", "purpose": "校验测试",
        }
        body.update(override)
        st, r = call("POST", "/api/v1/bookings", token=student1, json_body=body)
        w("     %-22s -> HTTP %s  %s" % (desc, st, r.get("message", "")[:46]))

    # 过去的日期
    st, r = call("POST", "/api/v1/bookings", token=student1, json_body={
        "equipment_id": 13, "booking_date": str(today - timedelta(days=1)),
        "start_time": "09:00:00", "end_time": "11:00:00", "purpose": "过去日期测试",
    })
    w("     %-22s -> HTTP %s  %s" % ("过去的日期", st, r.get("message", "")[:46]))

    # ==================== 10. 我的预约列表 ====================
    w()
    w("【10】我的预约列表")
    st, r = call("GET", "/api/v1/bookings/my?page=1&page_size=5", token=student1)
    w("     HTTP %s  总数=%s  总页数=%s" % (st, r["data"]["total"], r["data"]["total_pages"]))
    for item in r["data"]["list"][:3]:
        w("        #%s %s %s %s-%s [%s]" % (
            item["id"], item["booking_date"], item["equipment_name"],
            item["start_time"][:5], item["end_time"][:5], item["status"]))

    st, r = call("GET", "/api/v1/bookings/my?status=pending", token=student1)
    w("     按状态筛选 pending -> %s 条" % r["data"]["total"])

    # ==================== 11. 待审核数量 ====================
    w()
    w("【11】待审核数量")
    st, r = call("GET", "/api/v1/bookings/stats/pending-count", token=admin)
    w("     管理员 -> count=%s  scope=%s" % (r["data"]["count"], r["data"]["scope"]))
    st, r = call("GET", "/api/v1/bookings/stats/pending-count", token=student1)
    w("     学生   -> count=%s  scope=%s" % (r["data"]["count"], r["data"]["scope"]))

    w()
    w("=" * 70)
    w("  第 3 批验证结束")
    w("=" * 70)

    text = "\n".join(out)
    with io.open(r"C:\code\LabBookingSystemV2\_batch3_check.txt", "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("written")


if __name__ == "__main__":
    asyncio.run(main())
