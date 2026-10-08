"""
Pydantic Schema 公共部分。

Schemas 层（DTO，数据传输对象）的职责：
- 定义「接口收什么、返什么」，与数据库模型解耦
- 请求模型负责参数校验（类型、长度、范围、格式）
- 响应模型负责字段裁剪（绝不把密码哈希返回给前端！）

为什么必须和 ORM 模型分开？
1. 安全：User 模型有 password 字段，如果直接返回 ORM 对象，
   密码哈希就泄露了。用响应模型明确列出允许暴露的字段，
   从机制上杜绝（而不是靠"我记得别返回密码"）。
2. 灵活：数据库字段改名不影响接口契约；接口可以组合多个表的数据。
3. 校验：数据库只管类型，Pydantic 能管"长度 2-20 字符""必须是合法邮箱"。
"""
from datetime import datetime
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """
    所有「响应模型」的基类。

    from_attributes=True 让 Pydantic 能从 ORM 对象（而不是 dict）构造模型，
    也就是可以用 UserResponse.model_validate(user_orm_object)。
    Python 3.12 下这是 Pydantic V2 的推荐写法（替代 V1 的 orm_mode/from_orm）。
    """

    model_config = ConfigDict(from_attributes=True)


class PageParams(BaseModel):
    """
    分页请求参数。

    用依赖注入的方式复用：
        params: PageParams = Depends()
    这样每个列表接口不用重复声明 page/page_size，也不会出现
    原项目那种「有的接口叫 page_size、有的叫 pageSize」的混乱。
    """

    page: int = Field(1, ge=1, le=10000, description="页码，从 1 开始")
    page_size: int = Field(10, ge=1, le=100, description="每页条数，1-100")

    @property
    def offset(self) -> int:
        """计算 SQL 的 OFFSET 值"""
        return (self.page - 1) * self.page_size


class IdResponse(BaseModel):
    """只返回一个新建资源 ID 的通用响应"""

    id: int = Field(..., description="资源ID")


class MessageResponse(BaseModel):
    """只返回一句提示的通用响应"""

    message: str = Field(..., description="提示信息")


class UploadedFileResponse(BaseModel):
    """文件上传成功后返回给前端的数据"""

    url: str = Field(..., description="可直接访问的完整 URL")
    path: str = Field(..., description="相对路径（数据库存储用）")
    size: int = Field(..., description="文件大小（字节）")
    mime_type: str = Field(..., description="真实 MIME 类型")


class HealthResponse(BaseModel):
    """健康检查响应"""

    status: str = Field(..., description="总体状态：ok / degraded")
    app_name: str
    version: str
    environment: str
    database: bool = Field(..., description="数据库是否可连接")
    redis: bool = Field(..., description="Redis 是否可连接")
    server_time: datetime = Field(..., description="服务器 UTC 时间")
