"""
设备路由：分类、实验室、设备、图片上传、收藏。

设计要点（面试可以讲）：
1. **公开接口 vs 需登录接口的划分**：
   - 分类/实验室/设备列表/设备详情 → 公开（游客也能看，符合"先逛后登录"的产品逻辑）
   - 设备增删改、图片上传 → 管理员
   - 收藏 → 需登录
   这个划分要在路由定义上一眼可见，所以用 CurrentUser / require_admin 明确标注。

2. **可选登录的使用**：设备详情用 OptionalUser，
   未登录也能看，登录了额外返回"你是否收藏了这台设备"。
   这样前端只需要发一次请求，不用先判断登录态再决定要不要再查收藏。

3. **图片上传用 multipart/form-data**，
   并且立刻走 FileStorageService 的完整校验（大小 + 真实类型 + 路径安全）。
"""
import logging

from fastapi import APIRouter, Depends, File, Query, UploadFile, status

from app.api.deps import (
    CurrentUser,
    DbSession,
    OptionalUser,
    PageParamsDep,
    require_admin,
)
from app.core.response import PageData, Response, success
from app.schemas.common import MessageResponse, UploadedFileResponse
from app.schemas.equipment import (
    CategoryCreateRequest,
    CategoryResponse,
    EquipmentCreateRequest,
    EquipmentResponse,
    EquipmentUpdateRequest,
    LaboratoryCreateRequest,
    LaboratoryResponse,
)
from app.services.equipment_service import CollectionService, EquipmentService

logger = logging.getLogger(__name__)
router = APIRouter(tags=["设备"])


# ====================== 设备分类 ======================

@router.get(
    "/categories",
    response_model=Response[list[CategoryResponse]],
    summary="设备分类列表（公开）",
    description="该接口结果会被 Redis 缓存 2 小时，因为分类数据极少变动。",
)
async def list_categories(db: DbSession) -> Response[list[CategoryResponse]]:
    service = EquipmentService(db)
    data = await service.list_categories()
    return success(data=[CategoryResponse(**item) for item in data], message="获取分类列表成功")


@router.post(
    "/categories",
    response_model=Response[CategoryResponse],
    status_code=status.HTTP_201_CREATED,
    summary="新增设备分类（管理员）",
    dependencies=[Depends(require_admin)],
)
async def create_category(
    data: CategoryCreateRequest,
    db: DbSession,
) -> Response[CategoryResponse]:
    service = EquipmentService(db)
    result = await service.create_category(data)
    return success(data=CategoryResponse(**result), message="分类创建成功")


@router.delete(
    "/categories/{category_id}",
    response_model=Response[MessageResponse],
    summary="删除设备分类（管理员）",
    description="分类下还有设备时不允许删除，会返回明确的提示。",
    dependencies=[Depends(require_admin)],
)
async def delete_category(category_id: int, db: DbSession) -> Response[MessageResponse]:
    service = EquipmentService(db)
    await service.delete_category(category_id)
    return success(data=MessageResponse(message="分类已删除"), message="分类删除成功")


# ====================== 实验室 ======================

@router.get(
    "/laboratories",
    response_model=Response[list[LaboratoryResponse]],
    summary="实验室列表（公开）",
)
async def list_laboratories(db: DbSession) -> Response[list[LaboratoryResponse]]:
    service = EquipmentService(db)
    data = await service.list_laboratories()
    return success(data=[LaboratoryResponse(**item) for item in data], message="获取实验室列表成功")


@router.post(
    "/laboratories",
    response_model=Response[LaboratoryResponse],
    status_code=status.HTTP_201_CREATED,
    summary="新增实验室（管理员）",
    dependencies=[Depends(require_admin)],
)
async def create_laboratory(
    data: LaboratoryCreateRequest,
    db: DbSession,
) -> Response[LaboratoryResponse]:
    service = EquipmentService(db)
    result = await service.create_laboratory(data)
    return success(data=LaboratoryResponse(**result), message="实验室创建成功")


# ====================== 设备 ======================

@router.get(
    "/equipment",
    response_model=Response[PageData[EquipmentResponse]],
    summary="设备列表（公开）",
    description=(
        "支持关键字、分类、实验室、状态筛选和排序。\n\n"
        "**这个接口故意不做缓存**：列表的筛选条件组合太多，缓存命中率极低，"
        "只会白白占用 Redis 内存。只有真正高频重复读取的数据才值得缓存。"
    ),
)
async def list_equipments(
    db: DbSession,
    page_params: PageParamsDep,
    keyword: str = Query(None, description="关键字：匹配设备名/型号/序列号"),
    category_id: int = Query(None, description="分类ID"),
    lab_id: int = Query(None, description="实验室ID"),
    status_filter: str = Query(None, alias="status", description="状态：available/busy/maintenance"),
    order_by: str = Query(
        "id",
        pattern="^(id|id_asc|browse_count|booking_count|name|created_at)$",
        description="排序：id/id_asc/browse_count/booking_count/name/created_at",
    ),
) -> Response[PageData[EquipmentResponse]]:
    service = EquipmentService(db)
    page = await service.list_equipments(
        page_params=page_params,
        keyword=keyword,
        category_id=category_id,
        lab_id=lab_id,
        status=status_filter,
        order_by=order_by,
    )
    return success(data=page, message="获取设备列表成功")


@router.get(
    "/equipment/available",
    response_model=Response[list[dict]],
    summary="所有可用设备（公开）",
    description="不分页，用于预约页的设备下拉选择。只返回 status=available 的设备。",
)
async def list_available_equipments(db: DbSession) -> Response[list[dict]]:
    service = EquipmentService(db)
    data = await service.list_available()
    # 转换图片路径，前端可直接使用
    from app.utils.url import to_static_url

    for item in data:
        item["image"] = to_static_url(item.get("image"))
    return success(data=data, message="获取可用设备成功")


@router.get(
    "/equipment/{equipment_id}",
    response_model=Response[dict],
    summary="设备详情（公开，可选登录）",
    description=(
        "每次访问会让设备浏览量 +1（数据库端原子自增，并发安全）。\n\n"
        "**缓存策略**：稳定字段（名称/型号/描述/图片）进 Redis 缓存 30 分钟，"
        "易变字段（状态/预约次数）每次实时查库。\n\n"
        "登录状态下会额外返回是否已收藏。"
    ),
)
async def get_equipment_detail(
    equipment_id: int,
    db: DbSession,
    current_user: OptionalUser = None,
) -> Response[dict]:
    service = EquipmentService(db)
    detail = await service.get_detail(equipment_id, count_view=True)

    # 转换图片路径
    from app.utils.url import to_static_url

    detail["image"] = to_static_url(detail.get("image"))

    # 登录用户额外返回收藏状态（省得前端再发一次请求）
    if current_user is not None:
        collection_service = CollectionService(db)
        detail["is_collected"] = await collection_service.check(current_user.id, equipment_id)
    else:
        detail["is_collected"] = False

    return success(data=detail, message="获取设备详情成功")


@router.post(
    "/equipment",
    response_model=Response[EquipmentResponse],
    status_code=status.HTTP_201_CREATED,
    summary="新增设备（管理员）",
    dependencies=[Depends(require_admin)],
)
async def create_equipment(
    data: EquipmentCreateRequest,
    db: DbSession,
) -> Response[EquipmentResponse]:
    service = EquipmentService(db)
    result = await service.create_equipment(data)
    return success(data=result, message="设备创建成功")


@router.put(
    "/equipment/{equipment_id}",
    response_model=Response[EquipmentResponse],
    summary="更新设备（管理员）",
    description="不允许手动把设备状态改成 busy，该状态由系统根据预约自动流转。",
    dependencies=[Depends(require_admin)],
)
async def update_equipment(
    equipment_id: int,
    data: EquipmentUpdateRequest,
    db: DbSession,
) -> Response[EquipmentResponse]:
    service = EquipmentService(db)
    result = await service.update_equipment(equipment_id, data)
    return success(data=result, message="设备更新成功")


@router.delete(
    "/equipment/{equipment_id}",
    response_model=Response[MessageResponse],
    summary="删除设备（管理员）",
    description="设备还有未完成的预约时不允许删除，防止产生孤儿预约记录。",
    dependencies=[Depends(require_admin)],
)
async def delete_equipment(equipment_id: int, db: DbSession) -> Response[MessageResponse]:
    service = EquipmentService(db)
    await service.delete_equipment(equipment_id)
    return success(data=MessageResponse(message="设备已删除"), message="设备删除成功")


# ====================== 设备图片 ======================

@router.post(
    "/equipment/{equipment_id}/image",
    response_model=Response[UploadedFileResponse],
    summary="上传设备图片（管理员）",
    description=(
        "**安全校验**：\n"
        "1. 文件大小限制（默认 5MB，可在 .env 配置）\n"
        "2. **读取文件头判断真实类型**，不信任文件扩展名 —— "
        "把 evil.exe 改名成 evil.jpg 会被拒绝\n"
        "3. 文件名用 UUID 重新生成，彻底避免路径穿越攻击\n"
        "4. 上传成功后自动删除旧图片，不会残留垃圾文件"
    ),
    dependencies=[Depends(require_admin)],
)
async def upload_equipment_image(
    equipment_id: int,
    db: DbSession,
    file: UploadFile = File(..., description="图片文件，支持 JPG/PNG/GIF/WebP，最大 5MB"),
) -> Response[UploadedFileResponse]:
    service = EquipmentService(db)
    result = await service.upload_image(equipment_id, file)
    return success(data=result, message="图片上传成功")


@router.delete(
    "/equipment/{equipment_id}/image",
    response_model=Response[MessageResponse],
    summary="删除设备图片（管理员）",
    dependencies=[Depends(require_admin)],
)
async def delete_equipment_image(
    equipment_id: int,
    db: DbSession,
) -> Response[MessageResponse]:
    service = EquipmentService(db)
    await service.delete_image(equipment_id)
    return success(data=MessageResponse(message="图片已删除"), message="图片删除成功")


# ====================== 设备收藏 ======================

collection_router = APIRouter(prefix="/collections", tags=["设备收藏"])


@collection_router.get(
    "/check",
    response_model=Response[dict],
    summary="检查是否已收藏",
)
async def check_collection(
    equipment_id: int = Query(..., description="设备ID"),
    current_user: CurrentUser = None,
    db: DbSession = None,
) -> Response[dict]:
    """
    检查收藏状态。

    注意：原项目这里把 Query 的别名拼错成 `alise`，
    导致参数名变成 equipmentId，前端传 equipment_id 直接 422。
    V2 用标准写法，参数名就是 equipment_id。
    """
    service = CollectionService(db)
    is_collected = await service.check(current_user.id, equipment_id)
    return success(data={"is_collected": is_collected, "equipment_id": equipment_id})


@collection_router.post(
    "/{equipment_id}",
    response_model=Response[dict],
    status_code=status.HTTP_201_CREATED,
    summary="添加收藏",
)
async def add_collection(
    equipment_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> Response[dict]:
    service = CollectionService(db)
    result = await service.add(current_user.id, equipment_id)
    return success(data=result, message="收藏成功")


@collection_router.delete(
    "/{equipment_id}",
    response_model=Response[MessageResponse],
    summary="取消收藏",
)
async def remove_collection(
    equipment_id: int,
    current_user: CurrentUser,
    db: DbSession,
) -> Response[MessageResponse]:
    service = CollectionService(db)
    await service.remove(current_user.id, equipment_id)
    return success(data=MessageResponse(message="已取消收藏"), message="取消收藏成功")


@collection_router.get(
    "",
    response_model=Response[PageData[dict]],
    summary="我的收藏列表",
)
async def list_collections(
    current_user: CurrentUser,
    db: DbSession,
    page_params: PageParamsDep,
) -> Response[PageData[dict]]:
    service = CollectionService(db)
    page = await service.list_collections(current_user.id, page_params)
    return success(data=page, message="获取收藏列表成功")


@collection_router.delete(
    "",
    response_model=Response[dict],
    summary="清空我的收藏",
)
async def clear_collections(
    current_user: CurrentUser,
    db: DbSession,
) -> Response[dict]:
    service = CollectionService(db)
    count = await service.clear(current_user.id)
    return success(data={"deleted": count}, message=f"已清空 {count} 条收藏")


router.include_router(collection_router)
