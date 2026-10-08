"""
安全模块单元测试。

这些测试**不需要数据库**，跑起来飞快（毫秒级），
可以开发时随手运行 —— 这是单元测试最大的价值：
即时反馈。

运行：
    .venv\\Scripts\\python.exe -m pytest tests/unit -v
"""
import pytest

from app.core.security import (
    calc_token_expires_at,
    decode_jwt_token,
    create_jwt_token,
    generate_token,
    get_password_hash,
    needs_rehash,
    should_renew_token,
    utc_now,
    validate_password_strength,
    verify_password,
)


class TestPasswordHashing:
    """密码哈希与校验"""

    def test_hash_is_not_plaintext(self):
        """哈希结果绝不能等于明文密码"""
        password = "MySecret@123"
        hashed = get_password_hash(password)
        assert hashed != password
        assert password not in hashed

    def test_verify_correct_password(self):
        """正确密码应通过校验"""
        password = "MySecret@123"
        hashed = get_password_hash(password)
        assert verify_password(password, hashed) is True

    def test_verify_wrong_password(self):
        """错误密码必须被拒绝"""
        hashed = get_password_hash("MySecret@123")
        assert verify_password("WrongPassword@1", hashed) is False

    def test_same_password_different_hash(self):
        """
        同一个密码两次哈希结果必须不同。

        这是 bcrypt「自带随机盐」的体现，也是防彩虹表的关键。
        如果两次结果相同，说明用了固定盐（或者没加盐），是严重缺陷。
        """
        password = "SamePassword@1"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)
        assert hash1 != hash2
        # 但两个哈希都能验证通过同一个密码
        assert verify_password(password, hash1)
        assert verify_password(password, hash2)

    def test_hash_format_is_bcrypt(self):
        """哈希串应以 bcrypt 的标识开头（$2b$ 是 bcrypt 版本标识）"""
        hashed = get_password_hash("MySecret@123")
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$")

    def test_invalid_hash_does_not_crash(self):
        """
        数据库里存了非法格式的哈希（比如老数据是 SHA256 的十六进制串）时，
        verify 应该返回 False 而不是抛异常把接口打成 500。
        """
        fake_sha256 = "a" * 64
        assert verify_password("anything", fake_sha256) is False

    def test_long_password_truncated_by_bytes(self):
        """
        bcrypt 只处理前 72 字节。

        测试要点：截断必须按**字节**而不是字符。
        中文一个字 3 字节，所以 25 个中文字 = 75 字节 > 72。
        """
        # 24 个中文字 = 72 字节，完整保留
        pw_72_bytes = "密" * 24
        assert len(pw_72_bytes.encode("utf-8")) == 72
        hashed = get_password_hash(pw_72_bytes)
        assert verify_password(pw_72_bytes, hashed) is True

        # 25 个中文字，超出部分被截断；前 72 字节相同的密码会被视为同一个
        pw_75_bytes = "密" * 25
        # 这两个密码前 72 字节相同，所以应该验证通过（这是 bcrypt 的已知行为）
        assert verify_password(pw_75_bytes, hashed) is True

    def test_needs_rehash(self):
        """当前配置生成的哈希不需要重算"""
        hashed = get_password_hash("MySecret@123")
        assert needs_rehash(hashed) is False


class TestPasswordStrength:
    """密码强度校验"""

    @pytest.mark.parametrize(
        "password,should_pass",
        [
            ("Short@1", False),         # 太短
            ("12345678", False),         # 纯数字
            ("abcdefgh", False),         # 纯字母
            ("password", False),         # 虽然 8 位但纯字母 → 拒绝
            ("12345678", False),         # 常见弱密码
            ("MySecret@123", True),      # 合格
            ("Abcd1234!", True),         # 合格
            ("a" * 73, False),           # 超过 72 字节上限
        ],
    )
    def test_strength_rules(self, password, should_pass):
        result = validate_password_strength(password)
        if should_pass:
            assert result is None, f"密码 {password!r} 应该通过校验，但被拒绝：{result}"
        else:
            assert result is not None, f"密码 {password!r} 应该被拒绝，但通过了"


class TestTokenGeneration:
    """Token 生成"""

    def test_token_is_uuid_format(self):
        """Token 应该是标准 UUID 格式（36 字符，含 4 个连字符）"""
        token = generate_token()
        assert len(token) == 36
        assert token.count("-") == 4

    def test_tokens_are_unique(self):
        """
        连续生成 1000 个 Token 不应重复。

        这验证了 uuid4 的随机性足够（122 位随机空间）。
        """
        tokens = {generate_token() for _ in range(1000)}
        assert len(tokens) == 1000

    def test_expires_at_is_future(self):
        """过期时间必须在未来"""
        expires = calc_token_expires_at()
        assert expires > utc_now()


class TestSlidingExpiration:
    """滑动续期判断"""

    def test_fresh_token_needs_no_renew(self):
        """刚签发的 Token（剩余时间接近满额）不需要续期"""
        expires = calc_token_expires_at()
        assert should_renew_token(expires) is False

    def test_expiring_token_needs_renew(self):
        """
        剩余有效期不足阈值（默认 50%）时需要续期。

        构造一个 1 天后过期的 Token（总有效期 7 天，剩余不到 50%）。
        """
        from datetime import timedelta

        expires = utc_now() + timedelta(days=1)
        assert should_renew_token(expires) is True

    def test_expired_token_needs_renew(self):
        """已过期的 Token 也应被判定为需要续期（虽然实际会先被拒绝）"""
        from datetime import timedelta

        expires = utc_now() - timedelta(hours=1)
        assert should_renew_token(expires) is True


class TestJWT:
    """JWT 备用实现（用于和 UUID Token 方案对比）"""

    def test_encode_decode_roundtrip(self):
        token = create_jwt_token(subject=42, extra_claims={"username": "tester"})
        payload = decode_jwt_token(token)
        assert payload is not None
        assert payload["sub"] == "42"
        assert payload["username"] == "tester"
        # JWT 会自带签发时间和过期时间
        assert "iat" in payload
        assert "exp" in payload

    def test_tampered_token_rejected(self):
        """
        篡改过的 JWT 必须被拒绝。

        这是 JWT 的核心安全属性：签名保证内容不可伪造。
        注意：这里改的是 payload 部分，签名就对不上了。
        """
        token = create_jwt_token(subject=1)
        header, payload, signature = token.split(".")
        # 篡改 payload
        tampered = f"{header}.{payload[:-4]}AAAA.{signature}"
        assert decode_jwt_token(tampered) is None

    def test_garbage_token_rejected(self):
        """非法字符串不能解析成功，也不能抛异常"""
        assert decode_jwt_token("not-a-jwt-at-all") is None
        assert decode_jwt_token("") is None
