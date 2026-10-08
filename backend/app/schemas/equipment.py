"""
设备、分类、实验室的请求/响应模型。

设计要点（面试可以讲）：
1. 所有响应模型里的 image 字段都经过 to_static_url() 转换，
   前端拿到就能直接用，不需要任何拼接或猜测。
   这是通过 field_validator 实现的 —— 好处是"只要用了这个响应模型，
   转换就一定发生"，不会漏。

2. 创建/更新请求分离，且更新请求全字段可选。
   原项目用一个模型同时处理创建和更新，导致创建时"忘了传某个字段"
   不会被校验出来。

3. 价格用 Decimal 而不是 float。
   浮点数有精度问题（0.1 + 0.2 != 0.3），金额字段必须用 Decimal。
   这是金融/电商类系统的铁律，面试常问。
"""
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import Field, field_validator

from app.core.enums import EquipmentStatus
from app.schemas.common import ORMModel
from app.utils.url import to_static_url


# ====================== 分类 ======================

class CategoryResponse(ORMModel):
    """设备分类响应"""

    id: int
    name: str
    description: Optional[str] = None
    sort_order: int = 0
    equipment_count: int = Field(0, description="该分类下的设备数量")


class CategoryCreateRequest(ORMModel):
    name: str = Field(..., min_length=1, max_length=50, description="分类名称")
    description: Optional[str] = Field(None, max_length=200, description="分类描述")
    sort_order: int = Field(0, ge=0, le=9999, description="排序值")


# ====================== 实验室 ======================

class LaboratoryResponse(ORMModel):
    """实验室响应"""

    id: int
    name: str
    location: str
    description: Optional[str] = None
    capacity: Optional[int] = None
    equipment_count: int = Field(0, description="该实验室的设备数量")


class LaboratoryCreateRequest(ORMModel):
    name: str = Field(..., min_length=1, max_length=50)
    location: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=200)
    capacity: Optional[int] = Field(None, ge=1, le=500, description="可容纳人数")


# ====================== 设备 ======================

class EquipmentCreateRequest(ORMModel):
    """新增设备"""

    name: str = Field(..., min_length=1, max_length=100, description="设备名称")
    model: str = Field(..., min_length=1, max_length=100, description="设备型号")
    serial_number: str = Field(..., min_length=1, max_length=100, description="序列号（唯一）")
    category_id: Optional[int] = Field(None, description="分类ID")
    lab_id: Optional[int] = Field(None, description="实验室ID")
    status: EquipmentStatus = Field(EquipmentStatus.AVAILABLE, description="设备状态")
    purchase_date: Optional[date] = Field(None, description="采购日期")
    price: Optional[Decimal] = Field(None, ge=0, le=Decimal("99999999.99"), description="价格")
    description: Optional[str] = Field(None, max_length=2000, description="设备描述")


class EquipmentUpdateRequest(ORMModel):
    """
    更新设备（全部字段可选，支持部分更新）。

    注意 status 字段：管理员可以手动把设备设为 maintenance（维护中），
    这是唯一允许人工干预状态的场景。其余状态流转由定时任务根据预约自动完成。
    """

    name: Optional[str] = Field(None, min_length=1, max_length=100)
    model: Optional[str] = Field(None, min_length=1, max_length=100)
    serial_number: Optional[str] = Field(None, min_length=1, max_length=100)
    category_id: Optional[int] = None
    lab_id: Optional[int] = None
    status: Optional[EquipmentStatus] = None
    purchase_date: Optional[date] = None
    price: Optional[Decimal] = Field(None, ge=0, le=Decimal("99999999.99"))
    description: Optional[str] = Field(None, max_length=2000)


class EquipmentBrief(ORMModel):
    """设备简要信息（收藏列表、预约列表里嵌套用）"""

    id: int
    name: str
    model: str
    serial_number: str
    status: EquipmentStatus
    image: Optional[str] = None

    @field_validator("image", mode="before")
    @classmethod
    def convert_image(cls, v):
        return to_static_url(v)


class EquipmentResponse(ORMModel):
    """
    设备完整信息。

    image 字段经过转换后是可直接访问的路径（如 /static/uploads/equipment/...），
    前端直接赋给 <img src> 即可。
    """

    id: int
    name: str
    model: str
    serial_number: str
    status: EquipmentStatus
    purchase_date: Optional[date] = None
    price: Optional[Decimal] = None
    description: Optional[str] = None
    image: Optional[str] = Field(None, description="图片访问路径，前端可直接使用")
    browse_count: int = 0
    booking_count: int = 0
    created_at: Optional[datetime] = None

    # 分类与实验室的扁平化信息（避免前端再发请求）
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    lab_id: Optional[int] = None
    lab_name: Optional[str] = None
    lab_location: Optional[str] = None

    # 统计信息（可选，部分接口返回）
    collection_count: int = Field(0, description="被收藏次数")
    active_booking_count: int = Field(0, description="当前有效预约数")

    @field_validator("image", mode="before")
    @classmethod
    def convert_image(cls, v):
        return to_static_url(v)


class EquipmentDetailResponse(EquipmentResponse):
    """
    设备详情（在列表字段基础上增加描述）。

    为什么详情单独一个模型？
    因为列表接口不返回 description（Text 字段，可能很长），
    只有详情页才需要。这样列表响应体积能小很多。
    """

    description: Optional[str] = Field(None, description="设备描述")
    # 该设备未来 7 天的已预约时段（详情页显示"哪些时段已被占用"）
    booked_slots: List[dict] = Field(default_factory=list, description="已预约时段")


class EquipmentStatusUpdateRequest(ORMModel):
    """单独修改设备状态（管理员维护场景）"""

    status: EquipmentStatus = Field(..., description="目标状态")
    reason: Optional[str] = Field(None, max_length=200, description="变更原因")
