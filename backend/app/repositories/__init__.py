"""
Repositories 层：只负责数据访问（SQL）。

规则：
- 只做增删改查，不含任何业务判断
- 不 commit 事务（事务由 service 控制），需要时只 flush
- 好处：业务逻辑可以脱离数据库单测（传一个内存 SQLite 或 mock 即可）
"""
