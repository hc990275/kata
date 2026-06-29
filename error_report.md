# 卡塔服务器 (kata) 错误与坑点记录档案

## 1. GitHub Secrets 账号变量作用域混淆 (2026-06-29)
**问题现象**：在更新自动续期账号时，将 HidenCloud 的账号密码错误覆盖为了 KataBump 的账号集合，导致 `main.py` 获取不到正确的凭据。
**发生原因**：误以为 `login.json` 中的所有账号适用于该仓库下的所有续期脚本。实际上，该仓库包含两套完全独立的自动化业务：
1. **KataBump 卡塔面板 (JS架构)**：
   - 依赖文件：`login.json`（本地使用，不提交）
   - 依赖脚本：`action_renew.js`
   - 依赖工作流：`.github/workflows/renew.yml`
   - **对应云端变量**：必须将整个 JSON 数组赋值给 GitHub Secrets 中的 **`USERS_JSON`**。
2. **HidenCloud (Python架构)**：
   - 依赖脚本：`main.py`
   - 依赖工作流：`.github/workflows/hidencloud-auto-renew.yml`
   - **对应云端变量**：必须将单个或多个账号按照 `邮箱---密码` 的多行字符串格式，赋值给 GitHub Secrets 中的 **`ACCOUNTS`**。
**解决方案**：已使用 Python 底层 API 重新将正确的 JSON 内容写入 `USERS_JSON`，并将独立的 HidenCloud 账号写回 `ACCOUNTS`。后续维护必须严格铭记此两条业务线的变量物理隔离。

## 2. HidenCloud 续期调度日期计算误差 (2026-06-29)
**问题现象**：HidenCloud 续期脚本经常在还差一两天时就提前唤醒，随后又因“未满足 <= 1天限制”而跳过，导致产生无意义的执行日志。
**发生原因**：旧版脚本计算逻辑依赖于网页 Modal 弹窗中的粗略文本（如 `expires in 3 days`）。由于该网站在计算剩余时间时使用了向下取整（例如 3 天 20 小时会被生硬截断显示为 3 天），导致脚本计算出的唤醒日期比实际所需的日期提前了 1 天。
**解决方案**：废弃模糊的相对剩余天数文本。改为精确抓取网页上的绝对到期日（如 `03 Jul 2026`），将其解析为标准的 `datetime` 对象，在代码中严格倒推减去 1 天作为“目标续期日”，再由目标续期日减去今天，得出绝对精确的 Action 休眠天数。
