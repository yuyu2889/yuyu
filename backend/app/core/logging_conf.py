"""
日志配置。

设计要点（面试可以讲）：
1. Windows 中文控制台默认编码是 GBK。如果日志里有 emoji（✅ ❌ 等），
   直接输出会抛 UnicodeEncodeError，严重时会导致应用启动失败
   —— 这正是你原项目踩过的坑。
   这里统一用 UTF-8 打开输出流，并从根上避免这个问题。

2. 同时输出到控制台和文件：
   - 控制台给人看，格式简洁
   - 文件给排查问题用，带完整时间戳和模块名，并按大小轮转（不会把磁盘写满）

3. 统一日志入口后，可以在各处直接 logging.getLogger(__name__)，
   不用再关心 handler 怎么配。
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import BASE_DIR, settings

LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"

# 控制台格式：简洁，方便开发时快速扫
CONSOLE_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s"
# 文件格式：带文件名和行号，方便定位
FILE_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _fix_windows_console_encoding() -> None:
    """
    修复 Windows 控制台编码问题。

    原理：把标准输出/错误流的编码强制设为 UTF-8。
    errors="replace" 是关键——万一终端真不支持某个字符，
    也只会显示成 ?，而不是让整个程序崩溃。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass  # 某些环境（如被重定向到文件）不支持，忽略即可


def setup_logging() -> None:
    """初始化日志系统。在应用启动时调用一次。"""
    _fix_windows_console_encoding()

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if settings.debug else logging.INFO

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(logging.Formatter(CONSOLE_FORMAT, DATE_FORMAT))

    # 按大小轮转：单文件最大 10MB，保留 5 个备份
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",  # 关键：日志文件也用 UTF-8，中文不会乱码
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    # 先清空已有 handler，避免 uvicorn --reload 或测试里重复添加导致日志打印多份
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # 压低第三方库的日志级别，避免刷屏
    for noisy in ("sqlalchemy.engine.Engine", "aiomysql", "asyncio", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # SQLAlchemy 的 SQL 回显单独由配置控制
    if settings.database.echo:
        logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """获取 logger 的便捷函数"""
    return logging.getLogger(name)
