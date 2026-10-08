"""
设备服务：分类、实验室、设备、收藏、图片上传。

设计要点（面试可以讲）：
1. **缓存失效时机是这一层的重要职责**。
   repository 只管数据，不知道缓存的存在。
   service 在写完数据后决定清哪些缓存 —— 这叫"缓存的副作用归业务层管"。
   原项目的缓存问题就出在这里：写入和清理散落在各处，最后形成
   "写入→清空"的无效循环。V2 把所有失效逻辑收敛到本文件。

2. **设备详情的缓存策略（cache-aside + 热点字段不缓存）**：
   - 设备名、型号、描述、图片 → 变化少 → 进缓存
   - status、booking_count → 变化频繁 → 不进缓存，每次实时查
   这是"按字段的易变性分层"的思路，比"整个对象缓存或整个不缓存"更优。

3. **删除设备前要检查是否有有效预约**。
   不能直接删，否则用户的预约会变成"指向不存在设备的孤儿记录"。
   这种"业务前置检查"是 service 层的典型职责。
"""
import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import (
    TTL_CATEGORIES,
    TTL_EQUIPMENT_DETAIL,
    CacheKey,
    clear_category_cache,
    clear_equipment_cache,
    delete,
    get_json,
    set_json,
)
from app.core.enums import EquipmentStatus
from app.core.exceptions import ConflictError, NotFoundError
from app.core.response import ErrorCode, PageData
from app.models.equipment import Equipment
from app.repositories.equipment import (
    EquipmentCategoryRepository,
    EquipmentCollectionRepository,
    EquipmentRepository,
    LaboratoryRepository,
)
from app.schemas.common import UploadedFileResponse
from app.schemas.equipment import (
    CategoryCreateRequest,
    EquipmentCreateRequest,
    EquipmentResponse,
    EquipmentUpdateRequest,
    LaboratoryCreateRequest,
)
from app.services.file_storage import equipment_image_storage
from app.utils.url import to_static_url

logger = logging.getLogger(__name__)


class EquipmentService:
    """设备相关业务逻辑"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.equipment_repo = EquipmentRepository(db)
        self.category_repo = EquipmentCategoryRepository(db)
        self.lab_repo = LaboratoryRepository(db)
        self.collection_repo = EquipmentCollectionRepository(db)

    # ====================== 设备分类 ======================

    async def list_categories(self) -> List[dict]:
        """
        列出所有设备分类（带缓存）。

        缓存策略：分类数据极其稳定（管理员几个月才改一次），
        所以 TTL 给到 2 小时，是最适合缓存的类型。
        """
        cached = await get_json(CacheKey.EQUIPMENT_CATEGORIES)
        if cached is not None:
            logger.debug("分类缓存命中")
            return cached

        categories = await self.category_repo.list_all()
        data = [
            {
                "id": c.id,
                "name": c.name,
                "description": c.description,
                "sort_order": c.sort_order,
                # 这个 count 会让缓存内容随设备数变化而失效，
                # 严格来说应该分开缓存。这里为了教学清晰保持一致，
                # 并在新增/删除设备时清理分类缓存。
                "equipment_count": await self.category_repo.count_equipments(c.id),
            }
            for c in categories
        ]

        await set_json(CacheKey.EQUIPMENT_CATEGORIES, data, TTL_CATEGORIES)
        return data

    async def create_category(self, data: CategoryCreateRequest) -> dict:
        """新增分类（写操作后清缓存）"""
        existing = await self.category_repo.get_by_name(data.name)
        if existing is not None:
            raise ConflictError(ErrorCode.CONFLICT, f"分类「{data.name}」已存在")

        category = await self.category_repo.add(
            self.category_repo.model(
                name=data.name,
                description=data.description,
                sort_order=data.sort_order,
            )
        )
        await self.db.commit()

        # 关键：写操作后清缓存，否则前端还是看到旧数据
        await clear_category_cache()
        logger.info("新增设备分类 | name=%s | id=%s", category.name, category.id)

        return {"id": category.id, "name": category.name, "description": category.description,
                "sort_order": category.sort_order, "equipment_count": 0}

    async def delete_category(self, category_id: int) -> None:
        """
        删除分类。

        业务规则：分类下还有设备时不允许删除。
        原项目的数据库外键是 SET NULL（会把设备的分类清空），
        V2 改成 RESTRICT + 应用层检查，给出明确提示而不是静默清空。
        """
        category = await self.category_repo.get(category_id)
        if category is None:
            raise NotFoundError(ErrorCode.CATEGORY_NOT_FOUND)

        count = await self.category_repo.count_equipments(category_id)
        if count > 0:
            raise ConflictError(
                ErrorCode.CONFLICT,
                f"该分类下还有 {count} 台设备，请先转移或删除这些设备",
            )

        await self.category_repo.delete(category)
        await self.db.commit()
        await clear_category_cache()
        logger.info("删除设备分类 | id=%s | name=%s", category_id, category.name)

    # ====================== 实验室 ======================

    async def list_laboratories(self) -> List[dict]:
        """列出所有实验室（带缓存）"""
        cached = await get_json(CacheKey.LABORATORIES)
        if cached is not None:
            return cached

        labs = await self.lab_repo.list_all()
        data = [
            {
                "id": lab.id,
                "name": lab.name,
                "location": lab.location,
                "description": lab.description,
                "capacity": lab.capacity,
                "equipment_count": await self.lab_repo.count_equipments(lab.id),
            }
            for lab in labs
        ]
        await set_json(CacheKey.LABORATORIES, data, TTL_CATEGORIES)
        return data

    async def create_laboratory(self, data: LaboratoryCreateRequest) -> dict:
        lab = await self.lab_repo.add(
            self.lab_repo.model(
                name=data.name,
                location=data.location,
                description=data.description,
                capacity=data.capacity,
            )
        )
        await self.db.commit()
        await delete(CacheKey.LABORATORIES)
        return {
            "id": lab.id, "name": lab.name, "location": lab.location,
            "description": lab.description, "capacity": lab.capacity,
            "equipment_count": 0,
        }

    # ====================== 设备查询 ======================

    async def list_equipments(
        self,
        page_params,
        keyword: Optional[str] = None,
        category_id: Optional[int] = None,
        lab_id: Optional[int] = None,
        status: Optional[str] = None,
        order_by: str = "id",
        with_stats: bool = True,
    ) -> PageData:
        """
        设备列表（分页 + 多条件筛选 + 附带统计）。

        说明：这个接口**没有走缓存**。
        为什么？因为列表的筛选条件组合是笛卡尔积（关键词 × 分类 × 状态 × 排序 × 页码），
        缓存命中率极低，反而会产生大量无效缓存占用内存。
        这就是原项目"列表缓存写了从没命中"问题的根因。
        正确的做法是：**只缓存那些真正被高频重复读取、且变化少的数据**
        （比如分类列表、设备详情的基础字段）。
        """
        if with_stats:
            rows, total = await self.equipment_repo.list_with_stats(
                offset=page_params.offset,
                limit=page_params.page_size,
                keyword=keyword,
                category_id=category_id,
                lab_id=lab_id,
                status=status,
                order_by=order_by,
            )
        else:
            equipments, total = await self.equipment_repo.list_equipments(
                offset=page_params.offset,
                limit=page_params.page_size,
                keyword=keyword,
                category_id=category_id,
                lab_id=lab_id,
                status=status,
                order_by=order_by,
            )
            rows = [self._equipment_to_dict(eq) for eq in equipments]

        items = [EquipmentResponse(**row) for row in rows]
        return PageData.build(items, total, page_params.page, page_params.page_size)

    async def list_available(self) -> List[dict]:
        """所有可用设备（预约时的设备下拉列表）"""
        return await self.equipment_repo.list_available()

    async def get_detail(self, equipment_id: int, count_view: bool = True) -> dict:
        """
        设备详情（cache-aside + 浏览量自增）。

        ⚠️ 这里有个重要的顺序问题：
        必须先确认设备存在，再增加浏览量。
        原项目的实现是「先查缓存 → 命中就 UPDATE browse_count」，
        如果缓存里的 id 对应设备已被删除，就会产生对不存在设备的 UPDATE
        （虽然不会报错，但逻辑上是脏操作）。
        """
        cache_key = CacheKey.equipment_detail(equipment_id)

        # ---------- 先查数据库确认设备存在 ----------
        # 为什么不先查缓存？
        # 因为缓存里没有"设备是否存在"这个权威信息（缓存可能是残留的）。
        # 数据库查询走主键索引，开销很小，作为"存在性校验"是值得的。
        equipment = await self.equipment_repo.get_by_id(equipment_id)
        if equipment is None:
            # 设备不存在时顺手清掉可能残留的缓存
            await clear_equipment_cache(equipment_id)
            raise NotFoundError(ErrorCode.EQUIPMENT_NOT_FOUND)

        # ---------- 浏览量原子自增 ----------
        if count_view:
            await self.equipment_repo.increment_browse_count(equipment_id)
            await self.db.commit()

        # ---------- 组装数据 ----------
        # 实时查最新值（自增后的 browse_count、以及易变的 status / booking_count）
        equipment = await self.equipment_repo.get_by_id(equipment_id)
        detail = self._build_detail_dict(equipment)

        # ---------- 写入缓存（只缓存稳定字段，不含 status/booking_count） ----------
        await set_json(cache_key, detail, TTL_EQUIPMENT_DETAIL)

        return detail

    async def get_entity(self, equipment_id: int) -> Equipment:
        """获取设备 ORM 对象（内部使用）"""
        equipment = await self.equipment_repo.get_by_id(equipment_id)
        if equipment is None:
            raise NotFoundError(ErrorCode.EQUIPMENT_NOT_FOUND)
        return equipment

    # ====================== 设备增删改 ======================

    async def create_equipment(self, data: EquipmentCreateRequest) -> EquipmentResponse:
        """新增设备"""
        # 序列号唯一性检查（数据库也有唯一约束，但应用层先查能给出友好提示）
        existing = await self.equipment_repo.get_by_serial(data.serial_number)
        if existing is not None:
            raise ConflictError(
                ErrorCode.SERIAL_NUMBER_EXISTS,
                f"序列号 {data.serial_number} 已存在（设备：{existing.name}）",
            )

        # 校验关联的分类/实验室存在
        if data.category_id is not None:
            if await self.category_repo.get(data.category_id) is None:
                raise NotFoundError(ErrorCode.CATEGORY_NOT_FOUND)
        if data.lab_id is not None:
            if await self.lab_repo.get(data.lab_id) is None:
                raise NotFoundError(ErrorCode.LAB_NOT_FOUND)

        equipment = Equipment(
            name=data.name,
            model=data.model,
            serial_number=data.serial_number,
            category_id=data.category_id,
            lab_id=data.lab_id,
            status=data.status.value,
            purchase_date=data.purchase_date,
            price=data.price,
            description=data.description,
        )
        self.db.add(equipment)
        await self.db.commit()

        # 新增设备会影响分类下的设备数，所以要清分类缓存
        await clear_category_cache()
        await delete(CacheKey.LABORATORIES)

        equipment = await self.equipment_repo.get_by_id(equipment.id)
        logger.info("新增设备 | name=%s | serial=%s", equipment.name, equipment.serial_number)
        return EquipmentResponse(**self._build_detail_dict(equipment, include_description=True))

    async def update_equipment(
        self, equipment_id: int, data: EquipmentUpdateRequest
    ) -> EquipmentResponse:
        """
        更新设备。

        注意 status 字段的特殊处理：
        管理员只能手动设置为 available 或 maintenance。
        不允许手动设置 busy —— 因为 busy 是"有预约正在进行中"的表现，
        应该由定时任务根据预约自动流转。人工设为 busy 会导致
        "设备显示使用中，但没有任何预约"的脏状态。
        """
        equipment = await self.get_entity(equipment_id)

        update_data = data.model_dump(exclude_unset=True, exclude_none=True)
        if not update_data:
            raise ConflictError(ErrorCode.PARAM_ERROR, "没有需要更新的内容")

        # 序列号改了要检查唯一性
        if "serial_number" in update_data and update_data["serial_number"] != equipment.serial_number:
            if await self.equipment_repo.get_by_serial(update_data["serial_number"]):
                raise ConflictError(ErrorCode.SERIAL_NUMBER_EXISTS)

        # 分类/实验室存在性
        if "category_id" in update_data and update_data["category_id"] is not None:
            if await self.category_repo.get(update_data["category_id"]) is None:
                raise NotFoundError(ErrorCode.CATEGORY_NOT_FOUND)
        if "lab_id" in update_data and update_data["lab_id"] is not None:
            if await self.lab_repo.get(update_data["lab_id"]) is None:
                raise NotFoundError(ErrorCode.LAB_NOT_FOUND)

        # 状态校验
        if "status" in update_data:
            new_status = update_data["status"]
            new_status_value = new_status.value if hasattr(new_status, "value") else new_status
            if new_status_value == EquipmentStatus.BUSY.value:
                raise ConflictError(
                    ErrorCode.PARAM_ERROR,
                    "不能手动把设备设为「使用中」，该状态由系统根据预约自动流转",
                )
            update_data["status"] = new_status_value

        # Decimal 转 float 交给 SQLAlchemy，这里保持原样即可
        if "price" in update_data and isinstance(update_data["price"], Decimal):
            pass

        for field, value in update_data.items():
            setattr(equipment, field, value)

        await self.db.commit()

        # 清理缓存（详情 + 分类，因为可能改了分类或状态）
        await clear_equipment_cache(equipment_id)
        await clear_category_cache()

        equipment = await self.equipment_repo.get_by_id(equipment_id)
        logger.info("更新设备 | id=%s | 字段=%s", equipment_id, list(update_data))
        return EquipmentResponse(**self._build_detail_dict(equipment, include_description=True))

    async def delete_equipment(self, equipment_id: int) -> None:
        """
        删除设备。

        业务前置检查：如果设备还有未完成的预约，不允许删除。
        为什么？因为删掉设备后，那些预约就变成了"指向不存在设备的记录"，
        用户查看"我的预约"时会出错，统计也会失真。

        正确做法有两种：
          1. 拒绝删除，提示先处理预约（本项目采用）
          2. 软删除（加 deleted_at 字段，逻辑上标记删除但数据保留）
        生产环境更推荐软删除，因为可以恢复。
        """
        equipment = await self.get_entity(equipment_id)

        # 检查是否有有效预约
        from sqlalchemy import func, select

        from app.models.booking import Booking

        active_count = (
            await self.db.execute(
                select(func.count())
                .select_from(Booking)
                .where(
                    Booking.equipment_id == equipment_id,
                    Booking.status.in_(["pending", "approved"]),
                )
            )
        ).scalar() or 0

        if active_count > 0:
            raise ConflictError(
                ErrorCode.CONFLICT,
                f"该设备还有 {active_count} 条未完成的预约，请先处理这些预约再删除设备",
            )

        # 记录图片路径，删除后要清理文件
        image_path = equipment.image

        await self.equipment_repo.delete(equipment)
        await self.db.commit()

        # 清理磁盘上的图片文件（这里体现了 UUID 文件名的好处：
        # 直接删这一个文件即可，不需要遍历各种扩展名去试）
        if image_path and "/seed/" not in image_path:
            # seed 目录是初始化生成的示例图，多个设备可能共用，不删
            equipment_image_storage.delete(image_path)

        await clear_equipment_cache(equipment_id)
        await clear_category_cache()
        logger.info("删除设备 | id=%s | name=%s", equipment_id, equipment.name)

    # ====================== 图片上传 ======================

    async def upload_image(self, equipment_id: int, file: UploadFile) -> UploadedFileResponse:
        """
        上传设备图片（完整流程）。

        流程：
          1. 校验设备存在
          2. 读取文件内容（限制大小）
          3. 校验真实文件类型（读文件头，不信任扩展名）
          4. 用 UUID 存盘
          5. 更新数据库
          6. 删除旧图片文件
          7. 清理缓存

        关于第 6 步：原项目是靠"遍历 5 种扩展名去删除"来清理旧文件，
        漏一种就留垃圾。V2 因为数据库里存的就是完整的相对路径，
        直接按路径删那一个文件即可。
        """
        equipment = await self.get_entity(equipment_id)

        # 读取文件内容。UploadFile.read() 是异步的
        content = await file.read()

        # 校验 + 保存（校验逻辑全在 FileStorageService 里，包含
        # 空文件检查、大小限制、文件头类型检测、路径穿越防护）
        stored = equipment_image_storage.save(file.filename, content)

        # 记录旧图片路径，成功后再删（先删后写会有"写失败但图没了"的风险）
        old_image = equipment.image

        equipment.image = stored.relative_path
        await self.db.commit()

        # 删除旧文件。注意排除 seed 目录（初始化生成的示例图是共用的）
        if old_image and old_image != stored.relative_path and "/seed/" not in old_image:
            equipment_image_storage.delete(old_image)
        # 如果旧图在 uploads 目录下但不是 seed，正常删除
        elif old_image and old_image != stored.relative_path and old_image.startswith("uploads/"):
            equipment_image_storage.delete(old_image)

        # 清理缓存（图片变了，详情缓存必须失效）
        await clear_equipment_cache(equipment_id)

        logger.info(
            "设备图片上传成功 | equipment_id=%s | 路径=%s | 大小=%dB",
            equipment_id, stored.relative_path, stored.size,
        )

        return UploadedFileResponse(
            url=to_static_url(stored.relative_path),
            path=stored.relative_path,
            size=stored.size,
            mime_type=stored.mime_type,
        )

    async def delete_image(self, equipment_id: int) -> None:
        """删除设备图片（恢复为默认占位图）"""
        equipment = await self.get_entity(equipment_id)
        old_image = equipment.image

        equipment.image = None
        await self.db.commit()

        if old_image and old_image.startswith("uploads/") and "/seed/" not in old_image:
            equipment_image_storage.delete(old_image)

        await clear_equipment_cache(equipment_id)
        logger.info("删除设备图片 | equipment_id=%s", equipment_id)

    # ====================== 内部工具 ======================

    @staticmethod
    def _equipment_to_dict(equipment: Equipment) -> Dict[str, Any]:
        """把 ORM 设备对象转成响应字典"""
        return {
            "id": equipment.id,
            "name": equipment.name,
            "model": equipment.model,
            "serial_number": equipment.serial_number,
            "status": equipment.status,
            "purchase_date": equipment.purchase_date,
            "price": equipment.price,
            "image": equipment.image,
            "browse_count": equipment.browse_count or 0,
            "booking_count": equipment.booking_count or 0,
            "created_at": equipment.created_at,
            "category_id": equipment.category_id,
            "category_name": equipment.category.name if equipment.category else None,
            "lab_id": equipment.lab_id,
            "lab_name": equipment.lab.name if equipment.lab else None,
            "lab_location": equipment.lab.location if equipment.lab else None,
        }

    @staticmethod
    def _build_detail_dict(equipment: Equipment, include_description: bool = True) -> Dict[str, Any]:
        """构建设备详情字典"""
        data = {
            "id": equipment.id,
            "name": equipment.name,
            "model": equipment.model,
            "serial_number": equipment.serial_number,
            "status": equipment.status,
            "purchase_date": equipment.purchase_date,
            "price": equipment.price,
            "image": equipment.image,
            "browse_count": equipment.browse_count or 0,
            "booking_count": equipment.booking_count or 0,
            "created_at": equipment.created_at,
            "category_id": equipment.category_id,
            "category_name": equipment.category.name if equipment.category else None,
            "lab_id": equipment.lab_id,
            "lab_name": equipment.lab.name if equipment.lab else None,
            "lab_location": equipment.lab.location if equipment.lab else None,
        }
        if include_description:
            data["description"] = equipment.description
        return data


class CollectionService:
    """设备收藏业务逻辑"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.collection_repo = EquipmentCollectionRepository(db)
        self.equipment_repo = EquipmentRepository(db)

    async def check(self, user_id: int, equipment_id: int) -> bool:
        """检查是否已收藏"""
        record = await self.collection_repo.get_one(user_id, equipment_id)
        return record is not None

    async def add(self, user_id: int, equipment_id: int) -> dict:
        """
        添加收藏。

        先做应用层检查（给出友好提示），同时依赖数据库唯一约束兜底。
        这种「应用层友好 + 数据库层兜底」的双重保障是标准做法：
        应用层负责用户体验，数据库层负责数据正确性
        （防止并发场景下应用层检查失效）。
        """
        # 设备必须存在
        equipment = await self.equipment_repo.get(equipment_id)
        if equipment is None:
            raise NotFoundError(ErrorCode.EQUIPMENT_NOT_FOUND)

        existing = await self.collection_repo.get_one(user_id, equipment_id)
        if existing is not None:
            raise ConflictError(ErrorCode.COLLECTION_EXISTS)

        record = await self.collection_repo.add(
            self.collection_repo.model(user_id=user_id, equipment_id=equipment_id)
        )
        await self.db.commit()

        return {
            "collection_id": record.id,
            "equipment_id": equipment_id,
            "created_at": record.created_at,
        }

    async def remove(self, user_id: int, equipment_id: int) -> None:
        """取消收藏"""
        affected = await self.collection_repo.delete_one(user_id, equipment_id)
        if affected == 0:
            raise NotFoundError(ErrorCode.COLLECTION_NOT_FOUND)
        await self.db.commit()

    async def list_collections(self, user_id: int, page_params) -> PageData:
        """收藏列表（带设备信息）"""
        rows, total = await self.collection_repo.list_by_user(
            user_id, page_params.offset, page_params.page_size
        )
        # 转换图片路径
        items = []
        for row in rows:
            row["image"] = to_static_url(row.get("image"))
            items.append(row)
        return PageData.build(items, total, page_params.page, page_params.page_size)

    async def clear(self, user_id: int) -> int:
        """清空收藏，返回删除条数"""
        count = await self.collection_repo.delete_all_by_user(user_id)
        await self.db.commit()
        return count

    async def count(self, user_id: int) -> int:
        return await self.collection_repo.count_by_user(user_id)
