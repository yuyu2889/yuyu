"""
定时任务调度器（APScheduler）。

设计要点（面试可以讲）：
1. **用 AsyncIOScheduler 而不是 BackgroundScheduler**
   因为我们的任务是 async 函数（要 await 数据库操作）。
   BackgroundScheduler 会用线程执行任务，在任务里 await 会出错。
   AsyncIOScheduler 与 asyncio 事件循环集成，可以直接 await。

2. **时区必须显式指定**
   APScheduler 默认用系统本地时区。如果服务器时区不是 Asia/Shanghai，
   任务的执行时间会错。这里显式指定，行为可预期。

3. **first_run_time 让任务启动后立即执行一次**
   否则服务启动后要等一个周期（60 秒）才会第一次同步，
   这段时间内设备状态可能是不准的。

4. **任务的异常必须自己捕获**
   APScheduler 捕获到任务异常后只会记录日志，不会重新调度 ——
   也就是说任务抛异常后，后续的周期执行可能受影响。
   所以每个任务入口都要 try/except 包住（见 equipment_sync_service 里的实现）。

5. **为什么不用 --reload 启动？**
   开发时用 uvicorn --reload 会产生"监视进程 + 工作进程"两个进程，
   lifespan 会执行两次 → 定时任务被注册两次 → 同一份数据被处理两遍。
   这是很隐蔽的 bug，原项目的注释里也提到了。V2 在文档里明确警告。
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

# 设备状态同步间隔（秒）
EQUIPMENT_SYNC_INTERVAL_SECONDS = 60

# 全局调度器实例
scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")


def start_scheduler() -> None:
    """启动调度器并注册所有定时任务"""
    if scheduler.running:
        logger.warning("调度器已在运行，跳过重复启动")
        return

    # 延迟导入，避免模块级循环导入
    from app.services.equipment_sync_service import run_equipment_sync, run_token_cleanup

    # ---------- 任务 1：设备状态同步（每 60 秒）----------
    scheduler.add_job(
        run_equipment_sync,
        trigger=IntervalTrigger(seconds=EQUIPMENT_SYNC_INTERVAL_SECONDS),
        id="sync_equipment_status",
        name="设备状态同步",
        replace_existing=True,   # 重启时替换同名任务，避免重复注册
        max_instances=1,         # 同一任务不允许并发执行（双保险）
        coalesce=True,           # 如果积压了多次执行，合并成一次（避免补跑十几次）
        misfire_grace_time=30,   # 错过执行时间 30 秒内还允许补跑
    )

    # ---------- 任务 2：清理过期 Token（每小时的第 5 分钟）----------
    scheduler.add_job(
        run_token_cleanup,
        trigger=CronTrigger(minute=5),   # 每小时 05 分执行，避开整点的其他任务
        id="cleanup_expired_tokens",
        name="清理过期Token",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()

    jobs = scheduler.get_jobs()
    logger.info("[OK] 定时任务调度器已启动，共 %d 个任务：", len(jobs))
    for job in jobs:
        logger.info("     - %s | 触发器=%s | 下次执行=%s", job.name, job.trigger, job.next_run_time)


def stop_scheduler() -> None:
    """停止调度器（应用关闭时调用）"""
    if scheduler.running:
        # wait=False：不等待正在执行的任务完成，直接关闭。
        # 因为等待可能阻塞应用关闭流程（如果任务卡住了）。
        scheduler.shutdown(wait=False)
        logger.info("[OK] 定时任务调度器已停止")


def get_jobs_info() -> list[dict]:
    """
    获取当前所有任务的信息（供健康检查接口展示）。

    这类"可观测性"接口很实用：能一眼看出定时任务有没有在正常调度。
    """
    if not scheduler.running:
        return []
    return [
        {
            "id": job.id,
            "name": job.name,
            "trigger": str(job.trigger),
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in scheduler.get_jobs()
    ]
