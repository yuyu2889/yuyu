"""
图片文件存储模块单元测试。

这是本项目**安全最关键**的模块，必须重点测试：
- 伪装扩展名的文件要被拒绝（读文件头判断真实类型）
- 超大文件要被拒绝
- 路径穿越攻击要被防住

运行：
    .venv\\Scripts\\python.exe -m pytest tests/unit -v
"""
import pytest

from app.core.exceptions import FileUploadError
from app.core.response import ErrorCode
from app.services.file_storage import (
    MAGIC_SIGNATURES,
    UPLOAD_ROOT,
    FileStorageService,
    detect_image_type,
)

# ====================== 测试用的文件内容 ======================

# 合法的最小 JPEG（文件头 FF D8 FF）
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 100 + b"\xff\xd9"

# 合法的 PNG（文件头 89 50 4E 47 0D 0A 1A 0A）
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

# 合法的 GIF
GIF_BYTES = b"GIF89a" + b"\x00" * 100

# 合法的 WebP（RIFF....WEBP）
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100

# Windows 可执行文件（MZ 头）—— 伪装成图片的重点测试对象
EXE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00" + b"\x00" * 100

# PHP 脚本
PHP_BYTES = b"<?php system($_GET['cmd']); ?>" + b"\x00" * 50

# PDF
PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 100


class TestDetectImageType:
    """文件头类型检测"""

    @pytest.mark.parametrize(
        "content,expected_mime,expected_ext",
        [
            (JPEG_BYTES, "image/jpeg", ".jpg"),
            (PNG_BYTES, "image/png", ".png"),
            (GIF_BYTES, "image/gif", ".gif"),
            (WEBP_BYTES, "image/webp", ".webp"),
        ],
    )
    def test_detect_valid_formats(self, content, expected_mime, expected_ext):
        """四种允许的图片格式都要能正确识别"""
        result = detect_image_type(content)
        assert result is not None, "应该能识别出类型"
        assert result == (expected_mime, expected_ext)

    @pytest.mark.parametrize(
        "content,name",
        [
            (EXE_BYTES, "Windows 可执行文件"),
            (PHP_BYTES, "PHP 脚本"),
            (PDF_BYTES, "PDF 文件"),
            (b"", "空文件"),
            (b"\x00\x01\x02\x03", "随机字节"),
            (b"\xff\xd8", "长度不足的伪 JPEG 头"),
        ],
    )
    def test_reject_invalid_formats(self, content, name):
        """非图片格式必须返回 None（无法识别）"""
        assert detect_image_type(content) is None, f"{name} 不应被识别为图片"

    def test_short_content_does_not_crash(self):
        """内容极短时不能抛异常（边界情况）"""
        for length in range(0, 13):
            detect_image_type(b"\xff" * length)  # 不应抛异常

    def test_all_magic_signatures_are_valid(self):
        """魔数表本身的格式校验（防止有人改表时写错）"""
        for signature, mime, ext in MAGIC_SIGNATURES:
            assert isinstance(signature, bytes)
            assert mime.startswith("image/")
            assert ext.startswith(".")


class TestFileStorageValidation:
    """文件存储的校验逻辑"""

    def setup_method(self):
        """每个用例前创建独立的服务实例"""
        self.service = FileStorageService("test")

    def test_valid_jpeg_passes_validation(self):
        mime, ext = self.service.validate("photo.jpg", JPEG_BYTES)
        assert mime == "image/jpeg"
        assert ext == ".jpg"

    def test_empty_file_rejected(self):
        """空文件必须被拒绝"""
        with pytest.raises(FileUploadError) as exc_info:
            self.service.validate("empty.jpg", b"")
        assert exc_info.value.code == ErrorCode.FILE_EMPTY

    def test_oversized_file_rejected(self):
        """超过大小限制的文件必须被拒绝"""
        # 构造 6MB 内容（限制是 5MB）
        big = b"\xff\xd8\xff" + b"\x00" * (6 * 1024 * 1024)
        with pytest.raises(FileUploadError) as exc_info:
            self.service.validate("big.jpg", big)
        assert exc_info.value.code == ErrorCode.FILE_TOO_LARGE

    def test_executable_disguised_as_jpg_rejected(self):
        """
        【核心安全测试】把 exe 改名成 .jpg 必须被拒绝。

        这就是"读文件头"相对"看扩展名"的价值：
        文件名可以随便改，文件开头几个字节改不了。
        """
        with pytest.raises(FileUploadError) as exc_info:
            self.service.validate("innocent.jpg", EXE_BYTES)
        assert exc_info.value.code == ErrorCode.FILE_TYPE_NOT_ALLOWED

    def test_php_disguised_as_png_rejected(self):
        """PHP 脚本伪装成 png 也必须被拒绝（防 webshell 上传）"""
        with pytest.raises(FileUploadError) as exc_info:
            self.service.validate("shell.png", PHP_BYTES)
        assert exc_info.value.code == ErrorCode.FILE_TYPE_NOT_ALLOWED

    def test_valid_content_with_wrong_extension_uses_real_type(self):
        """
        内容合法但扩展名骗人时，应该按**真实类型**处理。

        比如把 PNG 内容命名为 .jpg，应识别为 png 并生成 .png 扩展名。
        这保证了存储的扩展名永远和内容一致。
        """
        mime, ext = self.service.validate("misleading.jpg", PNG_BYTES)
        assert mime == "image/png"
        assert ext == ".png"

    def test_no_filename_still_works(self):
        """没有文件名时也应能正常校验（不依赖文件名）"""
        mime, ext = self.service.validate(None, JPEG_BYTES)
        assert mime == "image/jpeg"


class TestFileStorageSave:
    """文件保存与路径生成"""

    def setup_method(self):
        self.service = FileStorageService("test")

    def test_save_generates_uuid_filename(self):
        """
        保存后的文件名必须是 UUID 格式，不能包含用户提供的名字。

        这样做的两个目的：
        1. 避免重名覆盖（并发上传不同文件不会互相覆盖）
        2. 防路径穿越（用户传 "../../etc/passwd" 也没用，因为名字是我们生成的）
        """
        stored = self.service.save("my photo.jpg", JPEG_BYTES)

        filename = stored.relative_path.split("/")[-1]
        name_without_ext = filename.rsplit(".", 1)[0]

        # UUID4 的 hex 形式是 32 位十六进制
        assert len(name_without_ext) == 32
        assert all(c in "0123456789abcdef" for c in name_without_ext)
        # 用户提供的文件名不应出现在路径里
        assert "my photo" not in stored.relative_path

    def test_save_organizes_by_year_month(self):
        """应按年月分目录，避免单目录文件过多"""
        from datetime import datetime

        stored = self.service.save("t.jpg", JPEG_BYTES)
        now = datetime.now()
        expected_part = f"test/{now.year:04d}/{now.month:02d}"
        assert expected_part in stored.relative_path

    def test_save_returns_correct_metadata(self):
        """返回的元信息要准确"""
        stored = self.service.save("t.png", PNG_BYTES)
        assert stored.size == len(PNG_BYTES)
        assert stored.mime_type == "image/png"
        assert stored.relative_path.startswith("uploads/test/")
        assert stored.url.startswith("/static/uploads/test/")

    def test_saved_file_exists_on_disk(self):
        """保存后文件必须真的存在，且内容一致"""
        stored = self.service.save("t.jpg", JPEG_BYTES)
        assert self.service.exists(stored.relative_path) is True

        # 清理
        self.service.delete(stored.relative_path)

    def test_two_saves_do_not_collide(self):
        """连续保存同名文件，两次路径必须不同（不会覆盖）"""
        s1 = self.service.save("same.jpg", JPEG_BYTES)
        s2 = self.service.save("same.jpg", JPEG_BYTES)
        assert s1.relative_path != s2.relative_path

        self.service.delete(s1.relative_path)
        self.service.delete(s2.relative_path)


class TestPathTraversalProtection:
    """路径穿越防护（Web 安全重点）"""

    def test_delete_rejects_parent_directory_traversal(self):
        """
        删除时必须拒绝越界路径。

        如果不做校验，数据库里存的路径一旦被篡改成
        "../../../Windows/System32/drivers/etc/hosts"，
        删除接口就会去删系统文件 —— 这叫路径穿越漏洞。
        """
        malicious_paths = [
            "../../../../Windows/System32/drivers/etc/hosts",
            "uploads/../../../../etc/passwd",
            "/etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "uploads/test/../../../../../../tmp/evil.txt",
        ]
        for path in malicious_paths:
            result = FileStorageService.delete(path)
            assert result is False, f"越界路径 {path!r} 必须被拒绝删除"

    def test_delete_rejects_path_outside_upload_root(self):
        """指向项目其他目录的路径也要拒绝（比如配置文件）"""
        result = FileStorageService.delete("../../.env")
        assert result is False

    def test_exists_rejects_traversal(self):
        """exists 检查同样不能越界"""
        assert FileStorageService.exists("../../../../etc/passwd") is False

    def test_delete_normal_path_works(self):
        """正常的业务路径应该能删除（保证防护没有误伤正常功能）"""
        service = FileStorageService("test")
        stored = service.save("normal.jpg", JPEG_BYTES)
        assert service.exists(stored.relative_path) is True

        result = FileStorageService.delete(stored.relative_path)
        assert result is True
        assert service.exists(stored.relative_path) is False

    def test_delete_none_returns_false(self):
        """None 和空字符串要安全处理"""
        assert FileStorageService.delete(None) is False
        assert FileStorageService.delete("") is False

    def test_deleted_file_reports_not_exists(self):
        """删除不存在的文件不应抛异常"""
        assert FileStorageService.delete("uploads/test/not-exist-file.jpg") is False
