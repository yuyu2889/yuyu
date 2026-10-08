"""
Services 层：业务逻辑所在地。

规则：
- 只处理「业务规则」，不关心 HTTP（不 import fastapi）
- 可以调用 repository 访问数据库
- 事务边界由 service 控制（一个业务操作 = 一个事务）
"""
