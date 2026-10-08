"""
图片文件存储服务 —— 这是 V2 相对原项目改动最大的模块之一。

原项目的问题（真实存在的 bug，你可以对照着看）：
1. 只检查文件名的扩展名，不看文件内容 → 把 evil.exe 改名成 evil.jpg 就能上传
2. 文件名用「设备ID.扩展名」→ 换扩展名后会残留旧文件（你目录里的 6.png/8.png）
3. 前端必须「猜」文件名规则才能拼出图片地址（Admin.vue 里那段正则匹配）
4. 删除时靠「遍历 5 种扩展名去试」→ 漏一种就留垃圾文件

V2 的解法：
1. 读文件头（magic bytes）判断真实类型，扩展名由真实类型推导 → 防伪造
2. 文件名用 UUID → 永不冲突、永不覆盖，并发上传也安全
3. 按年月分目录 → 单目录文件数不会无限膨胀（文件系统查找会变慢）
4. 数据库里只存相对路径，前端拿到后统一拼接，不做任何规则推断
5. 删除文件只需「读出路径 → 删那一个文件」，不需要猜

这套设计的一个额外好处：以后要迁移到对象存储（OSS/MinIO），
只需要换掉这个文件的实现，业务代码一行都不用改
——这就是「把变化点封装起来」的价值。
"""
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import BASE_DIR, settings
from app.core.exceptions import FileUploadError
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)

# 上传文件的根目录：backend/app/static/uploads/
UPLOAD_ROOT = BASE_DIR / "app" / "static" / "uploads"

# 对外访问的 URL 前缀（由 main.py 里的 StaticFiles 挂载提供）
STATIC_URL_PREFIX = "/static/uploads"

# 文件头（magic bytes）→ MIME 类型 映射表
# 说明：只看文件的前几个字节就能确定真实类型，这是防伪造上传的核心。
# 每种图片格式的魔数：
#   JPEG  FF D8 FF
#   PNG   89 50 4E 47 0D 0A 1A 0A
#   GIF   GIF87a / GIF89a
#   WEBP  RIFF....WEBP（第 0-3 字节是 RIFF，第 8-11 字节是 WEBP）
MAGIC_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
]


@dataclass
class StoredFile:
    """保存成功后的结果"""

    relative_path: str   # 数据库中存储的相对路径，如 uploads/equipment/2026/10/xxx.jpg
    url: str             # 可直接访问的 URL，如 /static/uploads/equipment/2026/10/xxx.jpg
    size: int            # 文件字节数
    mime_type: str       # 真实 MIME 类型
    original_name: str   # 用户上传时的原始文件名（仅用于日志）


def detect_image_type(content: bytes) -> Optional[tuple[str, str]]:
    """
    通过文件头检测图片的真实类型。

    :return: (mime_type, extension)；无法识别时返回 None
    """
    if len(content) < 12:
        return None

    for signature, mime_type, extension in MAGIC_SIGNATURES:
        if content.startswith(signature):
            return mime_type, extension

    # WebP 需要看两个位置，单独判断
    if content[0:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", ".webp"

    return None


class FileStorageService:
    """
    本地文件存储服务。

    所有方法都是同步的（文件 IO 本身没有异步 API）。
    在 async 路由里调用时，如果文件较大，应该用
    starlette.concurrency.run_in_threadpool 包一层，避免阻塞事件循环。
    本项目限制 5MB 以内，直接同步写盘的影响可以接受，
    但生产环境或大文件场景必须放进线程池。
    """

    def __init__(self, subdir: str) -> None:
        """
        :param subdir: 业务子目录，如 "equipment"、"user"
        """
        self.subdir = subdir
        self.base_dir = UPLOAD_ROOT / subdir

    # ---------- 校验 ----------

    def validate(self, filename: Optional[str], content: bytes) -> tuple[str, str]:
        """
        校验上传的文件，返回 (mime_type, extension)。

        校验顺序是刻意的：先查空 → 再查大小 → 最后查类型。
        先做便宜的检查（内存判断），后做需要读数据的检查。
        """
        if not content:
            raise FileUploadError(ErrorCode.FILE_EMPTY)

        if len(content) > settings.max_upload_size_bytes:
            actual_mb = len(content) / 1024 / 1024
            raise FileUploadError(
                ErrorCode.FILE_TOO_LARGE,
                f"文件大小 {actual_mb:.1f}MB 超出限制（最大 {settings.max_upload_size_mb}MB）",
            )

        detected = detect_image_type(content)
        if detected is None:
            # 这里不信任 filename 的扩展名，只报「无法识别」
            raise FileUploadError(
                ErrorCode.FILE_TYPE_NOT_ALLOWED,
                "无法识别的图片格式，请上传 JPG / PNG / GIF / WebP 格式的图片",
            )

        mime_type, extension = detected

        # 双重保险：即使文件头合法，也要确认这个类型在允许清单里
        if mime_type not in settings.allowed_image_type_list:
            raise FileUploadError(
                ErrorCode.FILE_TYPE_NOT_ALLOWED,
                f"不支持的图片类型 {mime_type}",
            )

        return mime_type, extension

    # ---------- 保存 ----------

    def save(self, filename: Optional[str], content: bytes) -> StoredFile:
        """
        校验并保存文件。

        :param filename: 用户上传时的原始文件名（只用于日志，不参与路径生成）
        :param content:  文件二进制内容
        :return: StoredFile
        """
        mime_type, extension = self.validate(filename, content)

        # 按年月分目录：避免单个目录堆积几万个文件
        now = datetime.now()
        relative_dir = Path(self.subdir) / f"{now.year:04d}" / f"{now.month:02d}"
        target_dir = UPLOAD_ROOT / relative_dir

        # 文件名用 UUID，彻底避免重名、覆盖、以及路径穿越攻击
        # （如果用用户提供的文件名，攻击者可以传 "../../../etc/passwd" 之类）
        stored_name = f"{uuid.uuid4().hex}{extension}"
        target_path = target_dir / stored_name

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(content)
        except OSError as exc:
            logger.error("文件写入失败 | path=%s | %s", target_path, exc, exc_info=True)
            raise FileUploadError(ErrorCode.FILE_SAVE_FAILED) from exc

        # 数据库中存的相对路径（不含 /static 前缀），
        # 前端统一用 API_BASE + "/static/" + 该值 拼接
        relative_path = f"uploads/{relative_dir.as_posix()}/{stored_name}"

        logger.info(
            "文件已保存 | 原始名=%s | 存储路径=%s | 大小=%dB | 类型=%s",
            filename, relative_path, len(content), mime_type,
        )

        return StoredFile(
            relative_path=relative_path,
            url=f"{STATIC_URL_PREFIX}/{relative_dir.as_posix()}/{stored_name}",
            size=len(content),
            mime_type=mime_type,
            original_name=filename or "",
        )

    # ---------- 删除 ----------

    @staticmethod
    def delete(relative_path: Optional[str]) -> bool:
        """
        删除已存储的文件。

        安全要点（很重要）：必须校验解析后的路径仍在 UPLOAD_ROOT 之内。
        否则如果数据库里的路径被篡改成 "../../../../Windows/System32/xxx"，
        就会删除系统文件——这叫「路径穿越（Path Traversal）」漏洞。
        """
        if not relative_path:
            return False

        try:
            candidate = (BASE_DIR / "app" / "static" / relative_path).resolve()
            root = UPLOAD_ROOT.resolve()

            # Python 3.9+ 的 is_relative_to 语义清晰
            if not candidate.is_relative_to(root):
                logger.error("拒绝删除越界路径 | 请求路径=%s | 解析路径=%s", relative_path, candidate)
                return False

            if candidate.is_file():
                candidate.unlink()
                logger.info("文件已删除 | %s", relative_path)
                return True
        except OSError as exc:
            logger.warning("删除文件失败 | %s | %s", relative_path, exc)

        return False

    @staticmethod
    def exists(relative_path: Optional[str]) -> bool:
        """检查文件是否真实存在（用于排查「数据库有记录但文件丢了」的情况）"""
        if not relative_path:
            return False
        candidate = (BASE_DIR / "app" / "static" / relative_path).resolve()
        root = UPLOAD_ROOT.resolve()
        if not candidate.is_relative_to(root):
            return False
        return candidate.is_file()


# ====================== 预定义的服务实例 ======================

equipment_image_storage = FileStorageService("equipment")
user_avatar_storage = FileStorageService("user")
