"""
第 2 批接口验证脚本。

覆盖：认证流程、权限控制、设备查询、缓存、图片上传的类型校验。

用标准库 urllib 实现，不依赖第三方 HTTP 客户端。
"""
import io
import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8001"
API = BASE + "/api/v1"

out = []
def w(s=""):
    out.append(str(s))

def call(method, path, token=None, json_body=None, raw_body=None, content_type=None):
    """通用请求函数"""
    base = BASE if (path.startswith("/api") or path == "/") else API
    full_path = path

    # URL 里的中文/空格等非 ASCII 字符必须做百分号编码，
    # 否则 http.client 在构造请求行时会抛
    # UnicodeEncodeError: 'ascii' codec can't encode characters。
    # 这是"中文参数"最常见的坑，浏览器会自动编码，用代码发请求就得自己处理。
    if "?" in full_path:
        p, q = full_path.split("?", 1)
        # 只对 query 部分编码，并且保留 = 和 & 等分隔符
        from urllib.parse import quote
        q = quote(q, safe="=&")
        full_path = p + "?" + q

    url = base + full_path
    data = None
    headers = {}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        if content_type:
            headers["Content-Type"] = content_type
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
            return e.code, {"raw": body[:200]}


def multipart(field_name, filename, content, boundary):
    """手工构造 multipart/form-data 请求体（标准库没有现成工具）"""
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += content
    body += f"\r\n--{boundary}--\r\n".encode()
    return body


w("=" * 68)
w("  第 2 批接口验证：认证 / 权限 / 设备 / 图片上传")
w("=" * 68)

# ============ 1. 健康检查 ============
st, r = call("GET", "/api/v1/health")
w()
w("【1】健康检查  HTTP %s  状态=%s  数据库=%s  Redis=%s"
  % (st, r["data"]["status"], r["data"]["database"], r["data"]["redis"]))

# ============ 2. 登录 ============
st, r = call("POST", "/api/v1/auth/login", json_body={"username": "admin", "password": "Admin@123"})
if st != 200:
    w("【2】管理员登录失败：%s" % r)
    sys.exit(1)
admin_token = r["data"]["token"]
admin_user = r["data"]["user"]
w()
w("【2】管理员登录  HTTP %s" % st)
w("     用户名   = %s" % admin_user["username"])
w("     姓名     = %s" % admin_user["real_name"])
w("     角色     = %s" % [x["code"] for x in admin_user["roles"]])
w("     is_admin = %s" % admin_user["is_admin"])
w("     有效期   = %s 秒（%.1f 天）" % (r["data"]["expires_in"], r["data"]["expires_in"] / 86400))

st, r = call("POST", "/api/v1/auth/login", json_body={"username": "student1", "password": "Student@123"})
student_token = r["data"]["token"]
w()
w("【3】学生登录    HTTP %s  姓名=%s  is_admin=%s"
  % (st, r["data"]["user"]["real_name"], r["data"]["user"]["is_admin"]))

# ============ 4. 登录失败：密码错误 vs 用户不存在，提示应相同 ============
st1, r1 = call("POST", "/api/v1/auth/login", json_body={"username": "admin", "password": "WrongPass@123"})
st2, r2 = call("POST", "/api/v1/auth/login", json_body={"username": "nobody_xyz", "password": "WrongPass@123"})
w()
w("【4】防用户名枚举验证")
w("     密码错误    -> HTTP %s  message=%s" % (st1, r1["message"]))
w("     用户不存在  -> HTTP %s  message=%s" % (st2, r2["message"]))
w("     两者提示一致 = %s（一致才能防止攻击者枚举出系统里存在哪些用户名）"
  % (r1["message"] == r2["message"]))

# ============ 5. 参数校验错误格式统一 ============
st, r = call("POST", "/api/v1/auth/register", json_body={
    "username": "ab", "password": "123", "real_name": "x",
    "email": "not-an-email", "phone": "12345"
})
w()
w("【5】注册参数校验（验证错误格式是否统一）")
w("     HTTP %s  code=%s" % (st, r["code"]))
w("     message = %s" % r["message"][:150])

# ============ 6. 权限控制 ============
st, r = call("GET", "/api/v1/users/", token=student_token)
w()
w("【6】权限控制验证（学生访问管理员接口）")
w("     HTTP %s  code=%s  message=%s" % (st, r["code"], r["message"]))

st, r = call("GET", "/api/v1/users/")
w("     不带 Token 访问 -> HTTP %s  message=%s" % (st, r["message"]))

st, r = call("GET", "/api/v1/users/", token=admin_token)
w("     管理员访问      -> HTTP %s  code=%s  用户总数=%s"
  % (st, r["code"], r["data"]["total"]))

# ============ 7. 设备分类（缓存） ============
st, r = call("GET", "/api/v1/categories")
w()
w("【7】设备分类（Redis 缓存）HTTP %s  分类数=%s" % (st, len(r["data"])))
for c in r["data"][:4]:
    w("     %s. %s（%s 台设备）" % (c["id"], c["name"], c["equipment_count"]))

# ============ 8. 设备列表（分页 + 筛选） ============
st, r = call("GET", "/api/v1/equipment?page=1&page_size=3")
d = r["data"]
w()
w("【8】设备列表  HTTP %s  总数=%s  总页数=%s  本页=%s 条  还有更多=%s"
  % (st, d["total"], d["total_pages"], len(d["list"]), d["has_more"]))
for it in d["list"]:
    w("     #%s %s | %s | 状态=%s | 图片=%s"
      % (it["id"], it["name"], it["category_name"], it["status"], it["image"]))

st, r = call("GET", "/api/v1/equipment?status=maintenance")
w("     按状态筛选 maintenance -> %s 台" % r["data"]["total"])

st, r = call("GET", "/api/v1/equipment?keyword=示波器")
w("     关键字筛选「示波器」  -> %s 台" % r["data"]["total"])

# ============ 9. 设备详情（浏览量自增 + 缓存） ============
st, r1 = call("GET", "/api/v1/equipment/1")
st, r2 = call("GET", "/api/v1/equipment/1", token=student_token)
w()
w("【9】设备详情（浏览量原子自增）")
w("     第一次访问：浏览量=%s  是否已收藏=%s" % (r1["data"]["browse_count"], r1["data"]["is_collected"]))
w("     登录后访问：浏览量=%s  是否已收藏=%s（登录用户会额外返回收藏状态）"
  % (r2["data"]["browse_count"], r2["data"]["is_collected"]))
w("     图片路径  ：%s（已转换成前端可直接使用的形式）" % r1["data"]["image"])

# ============ 10. 收藏功能 ============
st, r = call("GET", "/api/v1/collections/check?equipment_id=2", token=student_token)
w()
w("【10】收藏功能")
w("     检查收藏(equipment_id=2) -> HTTP %s  is_collected=%s" % (st, r["data"]["is_collected"]))

st, r = call("POST", "/api/v1/collections/2", token=student_token)
w("     添加收藏 -> HTTP %s  message=%s" % (st, r["message"]))

st, r = call("POST", "/api/v1/collections/2", token=student_token)
w("     重复收藏 -> HTTP %s  code=%s  message=%s（应被拒绝）" % (st, r["code"], r["message"]))

st, r = call("GET", "/api/v1/collections", token=student_token)
w("     收藏列表 -> 共 %s 条" % r["data"]["total"])
if r["data"]["list"]:
    w("        第一条：%s  图片=%s" % (r["data"]["list"][0]["name"], r["data"]["list"][0]["image"]))

st, r = call("DELETE", "/api/v1/collections/2", token=student_token)
w("     取消收藏 -> HTTP %s  message=%s" % (st, r["message"]))

st, r = call("DELETE", "/api/v1/collections/2", token=student_token)
w("     重复取消 -> HTTP %s  code=%s  message=%s（应提示不存在）" % (st, r["code"], r["message"]))

# ============ 11. 图片上传：真实图片 ============
def make_jpeg():
    """构造一个最小的合法 JPEG 文件头"""
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 200 + b"\xff\xd9"

boundary = uuid.uuid4().hex
body = multipart("file", "test.jpg", make_jpeg(), boundary)
st, r = call("POST", "/api/v1/equipment/3/image", token=admin_token, raw_body=body,
             content_type=f"multipart/form-data; boundary={boundary}")
w()
w("【11】上传合法 JPEG -> HTTP %s" % st)
if st == 200:
    w("     返回 URL  = %s" % r["data"]["url"])
    w("     存储路径  = %s（数据库里存的是相对路径）" % r["data"]["path"])
    w("     真实类型  = %s" % r["data"]["mime_type"])
    w("     文件大小  = %s 字节" % r["data"]["size"])
    uploaded_path = r["data"]["path"]
else:
    w("     失败：%s" % r)
    uploaded_path = None

# ============ 12. 图片上传：伪造扩展名的可执行文件（关键安全测试） ============
# 这是一个 Windows PE 可执行文件的开头（MZ 头），但文件名伪装成 .jpg
fake_exe = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100
boundary = uuid.uuid4().hex
body = multipart("file", "evil.jpg", fake_exe, boundary)
st, r = call("POST", "/api/v1/equipment/3/image", token=admin_token, raw_body=body,
             content_type=f"multipart/form-data; boundary={boundary}")
w()
w("【12】上传伪装成 .jpg 的可执行文件（安全关键测试）")
w("     HTTP %s  code=%s" % (st, r["code"]))
w("     message = %s" % r["message"])
w("     结论    = %s" % ("已拦截 ✓（读文件头识别出真实类型）" if st != 200 else "未拦截 ✗ 存在安全漏洞！"))

# ============ 13. 图片上传：超大文件 ============
big = b"\xff\xd8\xff" + b"\x00" * (6 * 1024 * 1024)  # 6MB，超过 5MB 限制
boundary = uuid.uuid4().hex
body = multipart("file", "big.jpg", big, boundary)
st, r = call("POST", "/api/v1/equipment/3/image", token=admin_token, raw_body=body,
             content_type=f"multipart/form-data; boundary={boundary}")
w()
w("【13】上传 6MB 文件（限制 5MB）")
w("     HTTP %s  code=%s  message=%s" % (st, r["code"], r["message"]))

# ============ 14. 图片静态访问 ============
if uploaded_path:
    url = BASE + "/static/" + uploaded_path
    try:
        with urllib.request.urlopen(url, timeout=20) as resp:
            content = resp.read()
            w()
            w("【14】访问上传的图片静态资源")
            w("     URL       = %s" % url)
            w("     HTTP %s  Content-Type=%s  大小=%d 字节"
              % (resp.status, resp.headers.get("Content-Type"), len(content)))
            w("     文件头    = %s（应为 ff d8 ff，说明是真实 JPEG）"
              % " ".join("%02x" % b for b in content[:3]))
    except Exception as e:
        w()
        w("【14】静态访问失败：%s" % e)

# ============ 15. 防呆检查：管理员不能禁用自己 ============
st, r = call("PUT", "/api/v1/users/1/status", token=admin_token, json_body={"status": "disabled"})
w()
w("【15】管理员尝试禁用自己（防呆检查）")
w("     HTTP %s  code=%s  message=%s" % (st, r["code"], r["message"]))

# ============ 16. 修改密码后旧 Token 应失效 ============
st, r = call("PUT", "/api/v1/users/me/password", token=student_token,
             json_body={"old_password": "Student@123", "new_password": "Student@456"})
w()
w("【16】修改密码（改完旧 Token 应失效）")
w("     修改密码 -> HTTP %s  message=%s" % (st, r["message"]))

st, r = call("GET", "/api/v1/auth/me", token=student_token)
w("     用旧 Token 访问 -> HTTP %s  message=%s" % (st, r["message"]))
w("     结论 = %s" % ("旧 Token 已失效 ✓（这是 UUID Token 相对 JWT 的核心优势）"
                     if st == 401 else "旧 Token 仍然有效 ✗"))

# 改回来，方便后续测试
st, r = call("POST", "/api/v1/auth/login", json_body={"username": "student1", "password": "Student@456"})
new_token = r["data"]["token"]
st, r = call("PUT", "/api/v1/users/me/password", token=new_token,
             json_body={"old_password": "Student@456", "new_password": "Student@123"})
w("     密码已改回 Student@123 -> HTTP %s" % st)

# ============ 17. 登出 ============
st, r = call("POST", "/api/v1/auth/login", json_body={"username": "student1", "password": "Student@123"})
tmp_token = r["data"]["token"]
st, r = call("POST", "/api/v1/auth/logout", token=tmp_token)
w()
w("【17】登出  HTTP %s  message=%s" % (st, r["message"]))
st, r = call("GET", "/api/v1/auth/me", token=tmp_token)
w("     登出后用原 Token 访问 -> HTTP %s  message=%s" % (st, r["message"]))

w()
w("=" * 68)
w("  验证结束")
w("=" * 68)

text = "\n".join(out)
with io.open(r"C:\code\LabBookingSystemV2\_batch2_check.txt", "w", encoding="utf-8", newline="\n") as f:
    f.write(text)
print("written")
