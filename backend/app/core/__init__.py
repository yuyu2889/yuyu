"""
核心基础设施包。

这里集中放与业务无关的「技术能力」：
- config      配置管理
- database    数据库连接与会话
- cache       Redis 缓存
- security    密码哈希 / Token / JWT
- exceptions  异常体系与全局处理器
- response    统一响应体
- enums       业务枚举与状态机
- logging     日志配置
- rate_limit  限流
"""
