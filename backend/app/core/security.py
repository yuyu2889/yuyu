"""
安全工具：密码哈希、Token 生成、JWT 备用实现。

设计要点（面试可以讲）：
1. 密码为什么必须哈希、不能加密？
   加密是可逆的（有密钥就能解出明文），哈希是不可逆的。
   密码存储只需要"能验证"，不需要"能还原"，所以必须用哈希。
   即使数据库被拖库，攻击者也只能拿到哈希值。

2. 为什么用 bcrypt 而不是 MD5/SHA256？
   - MD5/SHA256 是为"快"设计的，GPU 一秒能算几十亿次，暴力破解成本极低
   - bcrypt 故意设计得"慢"（可调 cost 因子），单次哈希约 100ms，
     把暴力破解的成本提高几个数量级
   - bcrypt 自带随机盐（salt），同一个密码每次哈希结果都不同，
     彻底防住"彩虹表"攻击
   原项目用的是 pbkdf2_sha256（也是安全的选择，NIST 推荐），
   V2 换成 bcrypt 是因为它更常见、面试更常问。

3. bcrypt 的 72 字节限制：
   bcrypt 算法只取密码的前 72 字节，超长部分被静默忽略。
   后果是"前72字节相同的密码"会被判定为同一个密码。
   所以这里显式截断到 72 字节，行为可预期。
   注意：截断要按「字节」而不是「字符」——一个中文占 3 字节，
   所以中文密码超过 24 个字符就会被截断。

4. UUID Token vs JWT：本项目主方案是 UUID Token（存数据库），
   JWT 实现保留在文件末尾用于对比学习。两者的取舍见 issue 文档。
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# ====================== 密码哈希 ======================

pwd_context = CryptContext(
    # 优先用 bcrypt；deprecated="auto" 让 passlib 在验证时
    # 自动兼容其他算法的旧哈希（便于平滑迁移）
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=12,  # cost 因子，2^12 次迭代。数值越大越安全也越慢
)

# bcrypt 算法的硬限制
BCRYPT_MAX_BYTES = 72


def get_password_hash(password: str) -> str:
    """
    生成密码哈希。

    :param password: 明文密码
    :return: 形如 $2b$12$... 的哈希串（已含盐，可直接入库）
    """
    return pwd_context.hash(_truncate_password(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    校验密码。

    注意：这里用 verify 而不是"重新哈希后比较字符串"——
    因为 bcrypt 每次哈希的盐都不同，同一个密码两次哈希结果是不一样的，
    必须用 passlib 提供的 verify 方法（它会从哈希串里取出盐再重算比对）。
    """
    try:
        return pwd_context.verify(_truncate_password(plain_password), hashed_password)
    except Exception:
        # 哈希串格式非法（比如数据库里存的是 MySQL SHA2 的结果）时，
        # 不要让整个登录接口 500，直接判定为验证失败。
        return False


def _truncate_password(password: str) -> str:
    """
    按字节截断到 bcrypt 支持的 72 字节以内。

    为什么要处理多字节？（这是一个容易被忽略的坑）
    "密码"两个字是 6 个字节。如果直接 password[:72] 按字符截断，
    中文密码实际上会超过 72 字节，bcrypt 底层（或新版 passlib）
    可能直接抛异常，或者静默截断导致行为不一致。
    所以这里编码成 bytes、截断、再解码（用 errors="ignore" 丢弃
    可能被截断一半的多字节字符）。
    """
    encoded = password.encode("utf-8")
    if len(encoded) <= BCRYPT_MAX_BYTES:
        return password
    return encoded[:BCRYPT_MAX_BYTES].decode("utf-8", errors="ignore")


def needs_rehash(hashed_password: str) -> bool:
    """
    判断某个哈希是否需要用当前配置重新生成。

    使用场景：以后把 bcrypt__rounds 从 12 提到 14 时，
    用户下次登录成功时可以顺手把旧哈希升级成新配置。
    这是平滑升级哈希强度的标准做法。
    """
    return pwd_context.needs_update(hashed_password)


# ====================== 时间工具 ======================

def utc_now() -> datetime:
    """
    获取当前 UTC 时间（不带时区信息）。

    为什么统一用 UTC？
    因为服务器可能部署在不同时区，用本地时间存过期时间会出错。
    原项目混用了 datetime.now()（本地时间）和数据库的 CURRENT_TIMESTAMP，
    在跨时区部署时会出 bug。
    注意：这里返回 naive datetime，是为了和 MySQL DATETIME 字段类型对齐
    （MySQL 的 DATETIME 不存时区）。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_local_str(dt: Optional[datetime]) -> Optional[str]:
    """
    把 UTC 时间转成"北京时间字符串"，用于展示给用户的邮件、前端等。

    中国是 UTC+8，没有夏令时，所以可以直接加 8 小时。
    """
    if dt is None:
        return None
    return (dt + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


# ====================== Token（主方案：UUID + 数据库） ======================

def generate_token() -> str:
    """
    生成一个随机 Token。

    用 uuid4 而不是随机数拼接：uuid4 有 122 位随机性，
    猜中的概率可以忽略不计，且格式规范、无需自己保证唯一性。
    """
    return str(uuid.uuid4())


def calc_token_expires_at() -> datetime:
    """计算 Token 过期时间（UTC）"""
    return utc_now() + timedelta(minutes=settings.access_token_expire_minutes)


def should_renew_token(expires_at: datetime) -> bool:
    """
    判断 Token 是否需要滑动续期。

    思路：如果「剩余有效期」已经少于「总有效期的某个比例」，就续期。
    例如总有效期 7 天、阈值 0.5，那么当剩余时间不足 3.5 天时，
    用户只要还有访问行为，就自动把有效期延回 7 天。

    好处：活跃用户永远不会被踢下线，而不活跃的用户到期自然失效。
    这比"固定 7 天到点就踢"的体验好很多，是很实用的设计。
    """
    total = timedelta(minutes=settings.access_token_expire_minutes)
    remaining = expires_at - utc_now()
    return remaining < total * settings.token_renew_threshold_ratio


# ====================== JWT（备用方案，用于对比学习） ======================

JWT_ALGORITHM = "HS256"


def create_jwt_token(subject: str | int, extra_claims: Optional[dict[str, Any]] = None) -> str:
    """
    生成 JWT（JSON Web Token）。

    JWT 的三段结构（这部分面试常问）：
      1. Header  {"alg":"HS256","typ":"JWT"}        → Base64Url 编码
      2. Payload {"sub":"1","exp":1730000000,...}   → Base64Url 编码
      3. Signature  用 SECRET_KEY 对前两段签名
    特点：服务端不存储，靠签名保证不可伪造；但也因此「签发后无法撤销」，
    要撤销就得额外维护黑名单——这正是本项目放弃 JWT 改用 UUID Token 的原因。
    """
    now = utc_now()
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.secret_key, algorithm=JWT_ALGORITHM)


def decode_jwt_token(token: str) -> Optional[dict[str, Any]]:
    """
    解码并校验 JWT。失败（签名错误、已过期、格式非法）统一返回 None。

    注意：jwt.decode 会同时校验签名和 exp，所以这里不需要手动比对过期时间。
    """
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None


# ====================== 密码强度校验 ======================

def validate_password_strength(password: str) -> Optional[str]:
    """
    校验密码强度。

    :return: 不合法时返回错误提示，合法返回 None

    为什么要有这个函数？
    弱密码是系统最大的安全短板。而且这里有个细节值得注意：
    密码强度校验必须在「后端」做——前端的校验只是提升体验，
    攻击者可以直接绕过前端调接口。
    """
    if len(password) < 8:
        return "密码长度至少 8 位"
    if len(password) > 72:
        return "密码长度不能超过 72 位"
    if password.isdigit():
        return "密码不能是纯数字"
    if password.isalpha():
        return "密码不能是纯字母"
    if password.lower() in ("12345678", "password", "qwertyui", "11111111"):
        return "密码过于简单，请更换"
    return None
