"""
URL 与路径工具。

设计要点（面试可以讲）：

**这是 V2 相对原项目最值得讲的一个改进。**

原项目的问题：数据库里 image 字段存的是 `equipment/1.jpg`（相对路径），
但前端在三个不同的地方用了三种不同的方式去拼完整地址：
  1. helpers.js 里拼 `${API_BASE}/static/${image}`
  2. Admin.vue 里用正则从 image 里抠出扩展名，重组成 `${API_BASE}/static/equipment/${id}.${ext}`
  3. EquipmentManagement.vue 直接把 image 当 src 用（结果 404）
同一个字段，三种解析规则，其中一个还得靠猜。

V2 的原则：**字段自描述 + 单一转换点**
- 数据库只存相对路径（`uploads/equipment/seed/xxx.jpg`），与存储实现绑定
- 后端在返回给前端时**统一转换成完整 URL**
- 前端只需要判断一种情况：为空 → 显示占位图

这样前端不需要知道任何文件名规则，后端换存储方案（本地 → OSS）
对前端也完全透明。
"""
from typing import Optional

# 静态资源 URL 前缀，与 main.py 里 StaticFiles 的挂载点一致
STATIC_URL_PREFIX = "/static/"


def to_static_url(relative_path: Optional[str]) -> Optional[str]:
    """
    把数据库里存的相对路径转换成前端可直接使用的完整路径。

    处理三种情况：
      1. 空值            → 返回 None（前端显示占位图）
      2. 已经是完整 URL  → 原样返回（兼容历史上存过外链的数据）
      3. 相对路径        → 加上 /static/ 前缀

    注意：这里返回的是**以 / 开头的绝对路径**（如 /static/uploads/...），
    而不是带域名的完整 URL。为什么？
    因为前端和后端可能不在同一台机器上（开发时后端跑在 8000/8001，
    前端跑在 5173）。让前端自己拼域名更灵活，
    而且能避免"后端硬编码了 127.0.0.1，部署到服务器就失效"的问题。
    """
    if not relative_path:
        return None

    path = relative_path.strip()
    if not path:
        return None

    # 外部链接原样返回（兼容历史数据）
    if path.startswith(("http://", "https://", "//")):
        return path

    # 已经是 /static/ 开头的，说明已经转换过了，避免重复拼接
    if path.startswith(STATIC_URL_PREFIX):
        return path

    # 去掉可能存在的开头斜杠，避免出现 //static//uploads 这种双斜杠
    return STATIC_URL_PREFIX + path.lstrip("/")
