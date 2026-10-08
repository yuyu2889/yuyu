# MySQL 使用速查

## 一、连接信息

```
主机：127.0.0.1
端口：3306
```

| 账号 | 密码 | 权限 | 用途 |
|------|------|------|------|
| `root` | `******` | 全部数据库 | 管理用（建库、建账号、改结构） |
| `yuyu` | `******` | 只能操作 `lab_equipment_db` 和 `lab_booking_v2` | 项目代码使用 |

**两个数据库**：

| 库名 | 用途 |
|------|------|
| `lab_equipment_db` | 原项目（V1）的数据库 |
| `lab_booking_v2` | 重写版（V2）的数据库 |

---

## 二、命令行为什么一开始敲 `mysql` 找不到

**原因**：MySQL 的 `bin` 目录没在系统的 `PATH` 环境变量里。

`PATH` 是 Windows 用来"找命令"的目录列表。敲 `mysql` 时，系统会去 PATH
列出的目录里挨个找 `mysql.exe`，找不到就报"不是内部或外部命令"。

**注意一个容易误解的点**：

| 事项 | 是否依赖 PATH |
|------|--------------|
| MySQL **服务**能否运行 | ❌ 不依赖。服务管理器用完整路径启动 `mysqld.exe` |
| 项目代码能否连数据库 | ❌ 不依赖。Python 驱动直连 `127.0.0.1:3306` |
| 在命令行敲 `mysql` 命令 | ✅ 依赖 |

所以"敲命令找不到"只是**命令行便利性**问题，不代表 MySQL 没装好或不能用。

**已修复**：MySQL 的 bin 目录已加入用户 PATH（2026-10-08 完成）。

```
C:\Program Files\MySQL\mysql-8.4.9-winx64\bin
```

⚠️ **改了 PATH 后必须重开命令行窗口才生效**。
已经打开的窗口用的是旧 PATH，不会自动更新。

---

## 三、常用命令

### 进入交互式命令行

```bash
mysql -u root -p******
# 或者连指定数据库
mysql -u root -p****** lab_booking_v2
```

进去之后：

```sql
-- 看有哪些数据库
SHOW DATABASES;

-- 切换数据库
USE lab_booking_v2;

-- 看有哪些表
SHOW TABLES;

-- 看表结构
DESC equipment;

-- 查数据（记得结尾加分号）
SELECT id, name, status FROM equipment LIMIT 10;

-- 退出
EXIT;
```

### 一条命令直接执行 SQL（不进交互界面）

```bash
mysql -u root -p****** -e "SELECT COUNT(*) FROM lab_booking_v2.bookings;"
```

### 看中文不乱码

命令行里如果中文显示成乱码，加上字符集参数：

```bash
mysql -u root -p****** --default-character-set=utf8mb4 -e "SELECT name FROM lab_booking_v2.equipment LIMIT 5;"
```

> 说明：乱码通常只是**终端显示**问题，数据库里存的数据是好的。
> 要验证这一点，可以用 `SELECT HEX(name)` 看原始字节。

---

## 四、服务管理

MySQL 注册成了 Windows 服务，名字 **`MySQL84`**，**开机自动启动**，平时不用管。

```powershell
# 查看状态
Get-Service MySQL84

# 停止（比如要改配置）
Stop-Service MySQL84

# 启动
Start-Service MySQL84

# 重启
Restart-Service MySQL84
```

**如果服务启动失败**，去看错误日志：

```
C:\Program Files\MySQL\data\mysql-error.log
```

启动失败的真正原因都记在这里面。

---

## 五、文件位置

| 内容 | 路径 |
|------|------|
| 程序目录 | `C:\Program Files\MySQL\mysql-8.4.9-winx64\` |
| 可执行文件 | `C:\Program Files\MySQL\mysql-8.4.9-winx64\bin\` |
| 配置文件 | `C:\Program Files\MySQL\my.ini` |
| 数据目录 | `C:\Program Files\MySQL\data\` |
| 错误日志 | `C:\Program Files\MySQL\data\mysql-error.log` |

**配置文件 `my.ini` 里改了这些**（都是参照官方默认值）：

```ini
[mysqld]
basedir = "C:/Program Files/MySQL/mysql-8.4.9-winx64"
datadir = "C:/Program Files/MySQL/data"
port = 3306
bind-address = 127.0.0.1              # 只允许本机连接（更安全）
character-set-server = utf8mb4        # 中文和 emoji 都不会乱码
collation-server = utf8mb4_0900_ai_ci
authentication_policy = caching_sha2_password
log-error = "C:/Program Files/MySQL/data/mysql-error.log"
innodb_buffer_pool_size = 128M
```

---

## 六、常见问题

### Q：忘了 root 密码怎么办？

需要**跳过权限验证**重置，步骤较长，且必须停服务。可以让我帮你做。

### Q：想用图形化工具连数据库？

推荐 **DBeaver**（免费开源）或 **Navicat**（收费）。

连接参数填：

```
主机：127.0.0.1
端口：3306
用户名：yuyu（或 root）
密码：******
数据库：lab_booking_v2
```

### Q：改 PATH 改坏了怎么办？

我改之前备份了原 PATH：

```
C:\code\PATH备份_20261008_144042.txt
```

里面是**改之前完整的用户 PATH**。恢复方法（在 PowerShell 里执行）：

```powershell
# 读出备份内容并写回
$old = Get-Content "C:\code\PATH备份_20261008_144042.txt" -Raw
[Environment]::SetEnvironmentVariable("Path", $old.Trim(), "User")
```

### Q：`Access denied for user 'yuyu'` 怎么办？

说明这个账号没有对应数据库的权限。用 root 补授权：

```bash
mysql -u root -p****** -e "GRANT ALL PRIVILEGES ON lab_booking_v2.* TO 'yuyu'@'127.0.0.1'; FLUSH PRIVILEGES;"
```

### Q：`cryptography package is required` 报错怎么办？

这是 MySQL 8 的 `caching_sha2_password` 认证插件需要 Python 的 `cryptography` 包。
V2 的 `requirements.txt` 里已经包含它了。如果还是报错：

```powershell
cd backend
.venv\Scripts\python.exe -m pip install cryptography==44.0.0
```
