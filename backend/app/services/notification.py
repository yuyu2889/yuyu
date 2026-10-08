"""
通知服务：邮件发送。

设计要点（面试可以讲，这里有个原项目的真实 bug）：

**原项目的问题**：用 `asyncio.create_task()` 把邮件发送丢到后台，
并且**复用了请求级的数据库会话**：

    # 原项目 routers/booking.py
    asyncio.create_task(
        send_booking_created_email_async(booking_id, user_id, equipment_id, db)
    )

问题在哪？
`get_db()` 依赖在请求结束时会 `await session.close()`，
但后台任务是在响应返回**之后**才执行的 ——
此时会话已经关闭，后台任务再去 `db.get(Booking, ...)` 就会报
ResourceClosedError 之类的错误。表现就是"邮件时好时坏"。

**V2 的解法**：
1. 在**请求内**（会话还有效时）就把邮件需要的所有数据取出来，
   组装成纯 Python 字典
2. 后台任务只拿着这个字典去发邮件，**完全不碰数据库**
3. 发邮件本身是同步的 smtplib 调用，用 `run_in_threadpool` 丢到线程池，
   避免阻塞事件循环

这个"在边界处把数据准备好，跨边界只传纯数据"的思路很通用 ——
凡是"请求内启动、请求后执行"的任务（后台任务、消息队列消费者、
定时任务），都应该遵循这个原则，不要跨边界传递数据库会话或 ORM 对象。
（ORM 对象还涉及 detached instance 问题，访问未加载的属性会报错。）
"""
import logging
import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import to_local_str
from app.models.booking import Booking

logger = logging.getLogger(__name__)


class MailSender:
    """
    邮件发送器。

    策略：**邮件发送失败绝不能影响主业务**。
    用户预约成功了，但邮件发不出去 —— 这不应该让整个请求失败。
    所以所有异常都被捕获并记录日志。
    """

    @staticmethod
    def send(
        to_email: str,
        subject: str,
        body: str,
        html: Optional[str] = None,
    ) -> bool:
        """
        发送邮件（同步方法，调用方负责放进线程池）。

        :return: 是否发送成功
        """
        if not settings.mail_enabled:
            logger.info(
                "邮件功能已关闭（MAIL_ENABLED=false），跳过发送 | 收件人=%s | 主题=%s",
                to_email, subject,
            )
            return False

        if not to_email or not settings.smtp_user:
            logger.warning("邮件配置不完整，跳过发送 | to=%s | smtp_user=%s",
                           to_email, bool(settings.smtp_user))
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = Header(subject, "utf-8")
            msg["From"] = settings.smtp_from or settings.smtp_user
            msg["To"] = to_email

            msg.attach(MIMEText(body, "plain", "utf-8"))
            if html:
                msg.attach(MIMEText(html, "html", "utf-8"))

            context = ssl.create_default_context()
            # 用 SMTP_SSL（端口 465），QQ 邮箱和企业邮箱常用这种方式
            with smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, context=context, timeout=15
            ) as server:
                server.login(settings.smtp_user, settings.smtp_password)
                server.sendmail(msg["From"], [to_email], msg.as_string())

            logger.info("邮件发送成功 | to=%s | subject=%s", to_email, subject)
            return True

        except smtplib.SMTPAuthenticationError:
            # 认证失败是最常见的错误，单独提示（通常是授权码填错了）
            logger.error(
                "邮件发送失败：SMTP 认证失败。请检查 SMTP_USER 和 SMTP_PASSWORD"
                "（注意密码要填「授权码」而不是邮箱登录密码）"
            )
            return False
        except Exception as exc:
            logger.error("邮件发送失败 | to=%s | %s", to_email, exc, exc_info=True)
            return False


class NotificationService:
    """
    业务通知服务。

    职责：把"业务事件"转换成"用户可读的通知内容"。
    这样做的好处：业务代码只关心"发生了什么事件"（预约创建、审核通过），
    不需要知道邮件模板长什么样。以后要加短信/站内信通知，
    只改这一个文件。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ====================== 数据组装（在请求内调用）======================

    @staticmethod
    def _build_booking_info(booking: Booking) -> Dict[str, Any]:
        """
        把预约组装成邮件模板需要的纯数据。

        ⚠️ 这个方法必须在**请求内**（数据库会话还有效时）调用，
        因为访问 booking.equipment.name 可能触发关联查询。
        """
        equipment = booking.equipment
        return {
            "booking_id": booking.id,
            "equipment_name": equipment.name if equipment else "未知设备",
            "equipment_model": equipment.model if equipment else "",
            "booking_date": booking.booking_date.strftime("%Y-%m-%d"),
            "start_time": booking.start_time.strftime("%H:%M"),
            "end_time": booking.end_time.strftime("%H:%M"),
            "purpose": booking.purpose or "",
            "notes": booking.notes or "",
            "created_at": to_local_str(booking.created_at) or "",
        }

    @staticmethod
    def _build_user_info(booking: Booking) -> Dict[str, str]:
        user = booking.user
        return {
            "email": (user.email if user else "") or "",
            "name": (user.real_name or user.username) if user else "用户",
        }

    # ====================== 通知事件 ======================

    async def notify_booking_created(self, booking: Booking) -> None:
        """
        通知：预约已提交，等待审核。

        实现方式：**先组装数据，再交给后台任务**。
        这里没有真的启动后台任务（需要 FastAPI 的 BackgroundTasks 对象），
        而是用"发送到线程池 + 不等待"的方式模拟。
        在路由层会改用 BackgroundTasks 来保证"响应先返回、邮件后发送"。
        """
        user_info = self._build_user_info(booking)
        booking_info = self._build_booking_info(booking)

        if not user_info["email"]:
            logger.info("用户未配置邮箱，跳过预约创建通知 | booking_id=%s", booking.id)
            return

        subject = f"【实验室预约】预约申请已提交 - {booking_info['equipment_name']}"
        body = self._render_created_text(user_info["name"], booking_info)
        html = self._render_created_html(user_info["name"], booking_info)

        await self._send_in_thread(user_info["email"], subject, body, html)

    async def notify_booking_audited(
        self, booking: Booking, result: str, note: Optional[str] = None
    ) -> None:
        """通知：审核结果"""
        user_info = self._build_user_info(booking)
        booking_info = self._build_booking_info(booking)

        if not user_info["email"]:
            logger.info("用户未配置邮箱，跳过审核通知 | booking_id=%s", booking.id)
            return

        approved = result == "approved"
        action = "已通过" if approved else "已拒绝"
        subject = f"【实验室预约】预约审核{action} - {booking_info['equipment_name']}"
        body = self._render_audited_text(user_info["name"], booking_info, approved, note)
        html = self._render_audited_html(user_info["name"], booking_info, approved, note)

        await self._send_in_thread(user_info["email"], subject, body, html)

    @staticmethod
    async def _send_in_thread(to_email: str, subject: str, body: str, html: str) -> None:
        """
        在线程池里发送邮件，不阻塞事件循环。

        为什么用 run_in_threadpool 而不是 asyncio.to_thread？
        两者本质一样（都丢到线程池），run_in_threadpool 是 Starlette 提供的，
        和 FastAPI 的生态更一致，且没有 asyncio.to_thread 的 Python 版本要求
        （to_thread 需要 3.9+）。
        """
        from starlette.concurrency import run_in_threadpool

        try:
            await run_in_threadpool(MailSender.send, to_email, subject, body, html)
        except Exception as exc:
            # 邮件失败绝不能冒泡到业务层
            logger.error("后台邮件任务异常 | to=%s | %s", to_email, exc)

    # ====================== 邮件模板 ======================

    @staticmethod
    def _render_created_text(name: str, info: Dict[str, Any]) -> str:
        return f"""尊敬的 {name}，您好！

您的设备预约申请已成功提交，请等待管理员审核。

预约详情：
  预约编号：{info['booking_id']}
  设备名称：{info['equipment_name']}
  设备型号：{info['equipment_model']}
  预约日期：{info['booking_date']}
  使用时段：{info['start_time']} - {info['end_time']}
  使用目的：{info['purpose']}

审核结果将通过邮件通知您，请注意查收。

—— 实验室设备预约管理系统
提交时间：{info['created_at']}
"""

    @staticmethod
    def _render_created_html(name: str, info: Dict[str, Any]) -> str:
        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:20px;background:#f5f7fa;font-family:'Microsoft YaHei',Arial,sans-serif;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;
              box-shadow:0 2px 12px rgba(0,0,0,.08);overflow:hidden;">
    <div style="background:linear-gradient(135deg,#409EFF,#337ECC);padding:28px;text-align:center;">
      <h2 style="margin:0;color:#fff;font-size:20px;">设备预约管理系统</h2>
      <p style="margin:8px 0 0;color:rgba(255,255,255,.9);font-size:13px;">预约申请已提交</p>
    </div>
    <div style="padding:28px;">
      <p style="color:#333;font-size:15px;">尊敬的 <strong>{name}</strong>，您好！</p>
      <p style="color:#666;font-size:14px;line-height:1.7;">
        您的设备预约申请已成功提交，请等待管理员审核。
      </p>
      <div style="background:#f8f9fa;border-radius:8px;padding:18px;margin:20px 0;">
        <table style="width:100%;font-size:14px;color:#555;border-collapse:collapse;">
          <tr><td style="padding:6px 0;width:90px;color:#909399;">预约编号</td>
              <td style="padding:6px 0;"><strong>{info['booking_id']}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#909399;">设备名称</td>
              <td style="padding:6px 0;"><strong>{info['equipment_name']}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#909399;">设备型号</td>
              <td style="padding:6px 0;">{info['equipment_model']}</td></tr>
          <tr><td style="padding:6px 0;color:#909399;">预约日期</td>
              <td style="padding:6px 0;">{info['booking_date']}</td></tr>
          <tr><td style="padding:6px 0;color:#909399;">使用时段</td>
              <td style="padding:6px 0;">{info['start_time']} - {info['end_time']}</td></tr>
          <tr><td style="padding:6px 0;color:#909399;">使用目的</td>
              <td style="padding:6px 0;">{info['purpose']}</td></tr>
        </table>
      </div>
      <p style="text-align:center;margin:24px 0;">
        <span style="display:inline-block;background:#E6A23C;color:#fff;
                     padding:6px 18px;border-radius:4px;font-size:13px;">⏳ 待审核</span>
      </p>
      <p style="color:#666;font-size:13px;line-height:1.7;">
        审核结果将通过邮件通知您，请注意查收。
      </p>
    </div>
    <div style="padding:16px 28px;background:#fafafa;border-top:1px solid #eee;
                text-align:center;color:#aaa;font-size:12px;">
      <p style="margin:0;">实验室设备预约管理系统 · 此邮件由系统自动发送，请勿回复</p>
      <p style="margin:6px 0 0;">提交时间：{info['created_at']}</p>
    </div>
  </div>
</body></html>"""

    @staticmethod
    def _render_audited_text(
        name: str, info: Dict[str, Any], approved: bool, note: Optional[str]
    ) -> str:
        status = "已通过 ✓" if approved else "已拒绝 ✗"
        note_line = f"\n  审核备注：{note}" if note else ""
        return f"""尊敬的 {name}，您好！

您的设备预约申请审核结果：{status}

预约详情：
  预约编号：{info['booking_id']}
  设备名称：{info['equipment_name']}
  预约日期：{info['booking_date']}
  使用时段：{info['start_time']} - {info['end_time']}
  使用目的：{info['purpose']}{note_line}

{"请按时前往实验室使用设备，使用完毕后请关闭设备电源。" if approved else "您可以选择其他时段重新预约。"}

—— 实验室设备预约管理系统
"""

    @staticmethod
    def _render_audited_html(
        name: str, info: Dict[str, Any], approved: bool, note: Optional[str]
    ) -> str:
        color = "#67C23A" if approved else "#F56C6C"
        badge = "✓ 已通过" if approved else "✗ 已拒绝"
        note_html = (
            f'<tr><td style="padding:6px 0;color:#909399;">审核备注</td>'
            f'<td style="padding:6px 0;color:{color};">{note}</td></tr>'
            if note else ""
        )
        tip = (
            "请按时前往实验室使用设备，使用完毕后请关闭设备电源。"
            if approved else "您可以选择其他时间段重新预约。"
        )
        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:20px;background:#f5f7fa;font-family:'Microsoft YaHei',Arial,sans-serif;">
  <div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;
              box-shadow:0 2px 12px rgba(0,0,0,.08);overflow:hidden;">
    <div style="background:{color};padding:28px;text-align:center;">
      <h2 style="margin:0;color:#fff;font-size:20px;">预约审核结果</h2>
      <p style="margin:8px 0 0;color:rgba(255,255,255,.9);font-size:13px;">{badge}</p>
    </div>
    <div style="padding:28px;">
      <p style="color:#333;font-size:15px;">尊敬的 <strong>{name}</strong>，您好！</p>
      <p style="color:#666;font-size:14px;line-height:1.7;">
        您提交的设备预约申请审核结果如下：
      </p>
      <div style="background:#f8f9fa;border-radius:8px;padding:18px;margin:20px 0;">
        <table style="width:100%;font-size:14px;color:#555;border-collapse:collapse;">
          <tr><td style="padding:6px 0;width:90px;color:#909399;">预约编号</td>
              <td style="padding:6px 0;"><strong>{info['booking_id']}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#909399;">设备名称</td>
              <td style="padding:6px 0;"><strong>{info['equipment_name']}</strong></td></tr>
          <tr><td style="padding:6px 0;color:#909399;">预约日期</td>
              <td style="padding:6px 0;">{info['booking_date']}</td></tr>
          <tr><td style="padding:6px 0;color:#909399;">使用时段</td>
              <td style="padding:6px 0;">{info['start_time']} - {info['end_time']}</td></tr>
          <tr><td style="padding:6px 0;color:#909399;">使用目的</td>
              <td style="padding:6px 0;">{info['purpose']}</td></tr>
          {note_html}
        </table>
      </div>
      <p style="text-align:center;margin:24px 0;">
        <span style="display:inline-block;background:{color};color:#fff;
                     padding:8px 24px;border-radius:4px;font-size:15px;">{badge}</span>
      </p>
      <p style="color:#666;font-size:13px;line-height:1.7;">{tip}</p>
    </div>
    <div style="padding:16px 28px;background:#fafafa;border-top:1px solid #eee;
                text-align:center;color:#aaa;font-size:12px;">
      <p style="margin:0;">实验室设备预约管理系统 · 此邮件由系统自动发送，请勿回复</p>
    </div>
  </div>
</body></html>"""
