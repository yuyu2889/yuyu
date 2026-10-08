"""
应用包。

注意：main.py 里必须 import app.models，让 SQLAlchemy 完成所有模型的
关系映射。否则字符串形式的 relationship("Booking") 在配置 mapper 时
会因为找不到该类而抛 InvalidRequestError。
"""
