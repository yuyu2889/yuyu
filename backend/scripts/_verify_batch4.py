"""
第 4 批验证：统计模块。
"""
import io
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8001"
API = BASE + "/api/v1"

out = []
def w(s=""):
    out.append(str(s))


def call(method, path, token=None, body=None):
    url = BASE + path if path.startswith("/api") else API + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def login(u, p):
    st, r = call("POST", "/api/v1/auth/login", body={"username": u, "password": p})
    return r["data"]["token"]


w("=" * 70)
w("  第 4 批验证：统计模块")
w("=" * 70)

admin = login("admin", "Admin@123")
student = login("student1", "Student@123")

# ---------- 1. 首页仪表盘（聚合接口）----------
st, r = call("GET", "/api/v1/statistics/dashboard", token=admin)
d = r["data"]
w()
w("【1】首页仪表盘（管理员）  HTTP %s" % st)
w("     我的待审核预约 : %s" % d["my_pending_bookings"])
w("     我的预约总数   : %s" % d["my_total_bookings"])
w("     我的收藏数     : %s" % d["my_collections"])
w("     可用设备       : %s / 共 %s 台（使用中 %s）"
  % (d["available_equipment"], d["total_equipment"], d["busy_equipment"]))
w("     全平台待审核   : %s（管理员专属）" % d["all_pending_bookings"])
w("     用户总数       : %s（管理员专属）" % d["total_users"])
w("     近7天完成      : %s" % d["weekly_completed"])

st, r = call("GET", "/api/v1/statistics/dashboard", token=student)
d2 = r["data"]
w()
w("【2】首页仪表盘（普通学生）  HTTP %s" % st)
w("     我的待审核预约 : %s" % d2["my_pending_bookings"])
w("     可用设备       : %s" % d2["available_equipment"])
w("     全平台待审核   : %s（应为 0，普通用户看不到全局数据）" % d2["all_pending_bookings"])
w("     用户总数       : %s（应为 0）" % d2["total_users"])
w("     权限隔离: %s" % ("正确 [OK]" if d2["all_pending_bookings"] == 0 and d2["total_users"] == 0
                        else "有泄露 [FAIL]"))

# ---------- 3. 用户统计 ----------
st, r = call("GET", "/api/v1/statistics/users", token=admin)
d = r["data"]
w()
w("【3】用户统计（管理员）  HTTP %s" % st)
w("     用户总数        : %s" % d["total_users"])
w("     近30天活跃      : %s" % d["active_users_30d"])
w("     近30天活跃用户  : %s  ← 基于 last_used_at（真实访问行为）" % d["active_users_30d"])
w("     当前有效会话    : %s" % d["online_sessions"])
w("     禁用用户        : %s" % d["disabled_users"])
w("     近7天新增       : %s" % d["new_users_7d"])
w("     管理员数量      : %s" % d["admin_count"])

st, r = call("GET", "/api/v1/statistics/users", token=student)
w("     学生访问 -> HTTP %s（应 403）" % st)

# ---------- 4. 设备统计与排行 ----------
st, r = call("GET", "/api/v1/statistics/equipment?limit=5", token=admin)
d = r["data"]
w()
w("【4】设备统计与排行（Top 5）  HTTP %s" % st)
w("     总浏览量   : %s" % d["total_browse_count"])
w("     总预约次数 : %s" % d["total_booking_count"])
w("     总收藏数   : %s" % d["total_collect_count"])
w("     设备总数   : %s" % d["total_equipment_count"])
w("     排行:")
for item in d["equipment_ranking"]:
    w("        %-14s 浏览=%-4s 预约=%-3s 收藏=%-3s 有效预约=%s"
      % (item["equipment_name"], item["browse_count"], item["booking_count"],
         item["collect_count"], item["active_booking_count"]))

# 换排序维度
st, r = call("GET", "/api/v1/statistics/equipment?limit=3&order_by=booking_count", token=admin)
w("     按预约次数排序 Top3: %s"
  % [(i["equipment_name"], i["booking_count"]) for i in r["data"]["equipment_ranking"]])

# ---------- 5. 设备状态分布 ----------
st, r = call("GET", "/api/v1/statistics/equipment/status", token=admin)
d = r["data"]
w()
w("【5】设备状态分布（饼图）  HTTP %s  总数=%s" % (st, d["total_equipment_count"]))
for item in d["status_distribution"]:
    bar = "#" * int(item["percentage"] / 4)
    w("     %-8s %3s 台  %5.1f%%  %s"
      % (item["status_name"], item["count"], item["percentage"], bar))
w("     说明：所有状态都会返回（即使为 0），避免饼图缺块")

# ---------- 6. 预约统计 ----------
st, r = call("GET", "/api/v1/statistics/bookings", token=admin)
d = r["data"]
w()
w("【6】预约统计  HTTP %s" % st)
w("     预约总数: %s  今日预约: %s" % (d["total_bookings"], d["today_bookings"]))
w("     待审核=%s  已通过=%s  已完成=%s  已拒绝=%s  已取消=%s"
  % (d["pending_count"], d["approved_count"], d["completed_count"],
     d["rejected_count"], d["cancelled_count"]))
w("     状态分布:")
for item in d["status_distribution"]:
    w("        %-6s %3s 条  %5.1f%%" % (item["status_name"], item["count"], item["percentage"]))

# ---------- 7. 预约趋势 ----------
st, r = call("GET", "/api/v1/statistics/bookings/trend?days=7", token=admin)
d = r["data"]
w()
w("【7】近 7 天预约趋势（折线图）  HTTP %s" % st)
w("     7天合计: %s  日均: %s  单日峰值: %s"
  % (d["total_weekly_booking"], d["avg_daily_booking"], d["max_daily_booking"]))
for item in d["daily_bookings"]:
    bar = "*" * (item["booking_count"] * 2)
    w("        %s (%s)  %2s  %s" % (item["date"], item["label"], item["booking_count"], bar))
w("     说明：缺失日期自动补 0，保证折线连续")

# ---------- 8. 我的数据概览 ----------
st, r = call("GET", "/api/v1/statistics/my/summary", token=student)
w()
w("【8】我的数据概览（普通用户可访问）  HTTP %s" % st)
w("     %s" % r["data"])

w()
w("=" * 70)
w("  第 4 批（后端）验证结束")
w("=" * 70)

with io.open(r"C:\code\LabBookingSystemV2\_batch4_check.txt", "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(out))
print("written")
