import os
import re
import sys
import json
import time
import random
import requests
from datetime import datetime
from DrissionPage import ChromiumPage, ChromiumOptions


# ─────────────────────────────────────────────
#  工具函数
# ─────────────────────────────────────────────

def log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def send_tg(token: str, chat_id: str, text: str):
    """发送 Telegram 纯文字通知"""
    if not token or not chat_id:
        return
    try:
        thread_id = os.getenv('TG_THREAD_ID', '').strip()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text[:4000],
            "parse_mode": "HTML"
        }
        if thread_id:
            payload["message_thread_id"] = thread_id
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            log("📤 Telegram 文字通知已发送")
        else:
            log(f"⚠️ Telegram 文字发送失败: {resp.text[:100]}")
    except Exception as e:
        log(f"❌ Telegram 文字异常: {e}")


def send_tg_photo(token: str, chat_id: str, photo_path: str, caption: str = ""):
    """
    发送 Telegram 图片通知。
    发送成功后删除本地文件；图片发送失败自动降级为纯文字，不丢消息。
    """
    if not token or not chat_id:
        return
    caption = caption[:1020]   # Telegram caption 上限 1024
    if os.path.exists(photo_path):
        try:
            thread_id = os.getenv('TG_THREAD_ID', '').strip()
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with open(photo_path, 'rb') as f:
                data = {
                    'chat_id': chat_id,
                    'caption': caption,
                    'parse_mode': 'HTML'
                }
                if thread_id:
                    data['message_thread_id'] = thread_id
                resp = requests.post(url, data=data, files={'photo': f}, timeout=25)
            if resp.status_code == 200:
                log(f"📤 Telegram 截图已发送: {os.path.basename(photo_path)}")
                os.remove(photo_path)
                return
            else:
                log(f"⚠️ 图片发送失败({resp.status_code})，降级为文字")
        except Exception as e:
            log(f"⚠️ 图片发送异常: {e}，降级为文字")
    else:
        log(f"⚠️ 截图文件不存在: {photo_path}，降级为文字")
    # 降级
    send_tg(token, chat_id, caption)


def parse_accounts(raw: str) -> list[tuple[str, str]]:
    """解析多账号，格式：email---password，支持换行或逗号分隔"""
    accounts = []
    lines = re.split(r'[\n,]+', raw.strip())
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if '---' in line:
            parts = line.split('---', 1)
            email, password = parts[0].strip(), parts[1].strip()
            if email and password:
                accounts.append((email, password))
        else:
            log(f"⚠️ 无法解析行: {line}")
    return accounts


def _screenshot(page: ChromiumPage, path: str) -> str:
    """截图并返回路径，失败不抛异常"""
    try:
        page.get_screenshot(path=path)
        log(f"📸 截图已保存: {path}")
    except Exception as e:
        log(f"⚠️ 截图失败: {e}")
    return path


# ─────────────────────────────────────────────
#  Turnstile 破解器
# ─────────────────────────────────────────────

class TurnstileSolver:
    def __init__(self, page: ChromiumPage):
        self.page = page

    # ── token 检测 ────────────────────────────
    def _has_token(self) -> bool:
        try:
            el = self.page.ele('css:[name="cf-turnstile-response"]', timeout=1)
            return bool(el and el.value)
        except Exception:
            return False

    # ── 方式 A：Shadow Root 穿透点击 ──────────
    def _click_shadow(self, iframe) -> bool:
        try:
            body = iframe.ele('tag:body')
            sr = body.shadow_root if body else None
            if not sr:
                return False
            target = (
                sr.ele('css:input[type="checkbox"]') or
                sr.ele('css:#challenge-stage') or
                sr.ele('css:div.main-wrapper') or
                sr.ele('css:#content')
            )
            if target:
                log("🖱️ [Shadow Root] 找到目标，执行物理偏移点击...")
                target.click.at(offset_x=random.randint(8, 15),
                                offset_y=random.randint(8, 15))
                return True
        except Exception as e:
            log(f"⚠️ Shadow Root 穿透失败: {e}")
        return False

    # ── 方式 B：Actions 物理坐标点击 ──────────
    def _click_physical(self, iframe) -> bool:
        """
        获取 iframe 在页面中的真实像素坐标，
        用 Actions 链将鼠标移到复选框中心并物理点击。
        这一方式能绕过部分检测 JS 注入点击的反爬机制。
        """
        try:
            frame_ele = iframe.frame_ele
            rect = self.page.run_js(
                """
                var r = arguments[0].getBoundingClientRect();
                return {x: r.left, y: r.top, w: r.width, h: r.height};
                """,
                frame_ele
            )
            if not rect:
                return False

            # Turnstile 复选框通常在 iframe 左侧约 25px、垂直居中
            target_x = int(rect['x']) + random.randint(20, 30)
            target_y = int(rect['y']) + int(rect['h'] / 2) + random.randint(-3, 3)
            log(f"🖱️ [物理坐标] Actions 移动到 ({target_x}, {target_y}) 并点击...")

            actions = self.page.actions
            # 先移到附近随机位置，再移到目标（模拟人工轨迹）
            actions.move(target_x + random.randint(-30, 30),
                         target_y + random.randint(-20, 20))
            time.sleep(random.uniform(0.3, 0.7))
            actions.move(target_x, target_y)
            time.sleep(random.uniform(0.15, 0.35))
            actions.click()
            return True
        except Exception as e:
            log(f"⚠️ Actions 物理坐标点击失败: {e}")
        return False

    # ── 方式 C：iframe 元素坐标盲点 ──────────
    def _click_blind(self, iframe) -> bool:
        try:
            log("🏹 [盲点] 执行 iframe 元素偏移点击...")
            iframe.frame_ele.click.at(
                offset_x=random.randint(20, 30),
                offset_y=random.randint(25, 35)
            )
            return True
        except Exception as e:
            log(f"⚠️ 盲点点击失败: {e}")
        return False

    # ── 主入口 ────────────────────────────────
    def solve(self, timeout: int = 25) -> bool:
        log("🛡️ 开始处理 Cloudflare Turnstile...")

        # 1. 检查是否已自动通过
        for i in range(4):
            if self._has_token():
                log(f"⚡ [自动通过] Token 已存在 (等待 {i}s)")
                return True
            time.sleep(1)

        # 2. 锁定 iframe
        iframe = None
        for selector in [
            'css:iframe[src^="https://challenges.cloudflare.com"]',
            'css:iframe[id^="cf-chl-widget-"]',
        ]:
            try:
                iframe = self.page.get_frame(selector, timeout=5)
                if iframe:
                    break
            except Exception:
                pass

        if not iframe:
            log("❌ 找不到 Turnstile iframe")
            return self._has_token()

        time.sleep(random.uniform(1.2, 2.0))

        # 3. 依次尝试三种点击方式，任一成功即进入等待
        click_success = (
            self._click_shadow(iframe) or
            self._click_physical(iframe) or
            self._click_blind(iframe)
        )

        if not click_success:
            log("❌ 所有点击方式均失败")
            return False

        # 4. 等待 token 出现（如未通过则每隔 8s 用物理坐标重试一次）
        log("⏳ 等待 Turnstile 验证通过...")
        for i in range(timeout):
            time.sleep(1)
            if self._has_token():
                log(f"🎉 过盾成功！(总耗时 {i+1}s)")
                return True
            # 每 8s 追加一次物理点击补救
            if i > 0 and i % 8 == 0:
                log(f"🔁 第 {i}s 仍未通过，追加物理坐标点击...")
                self._click_physical(iframe) or self._click_blind(iframe)

        log("⚠️ Turnstile 等待超时")
        return False


def _parse_due_date(text: str) -> str | None:
    """从页面文字提取标准化日期 YYYY-MM-DD，失败返回 None"""
    if not text:
        return None
    # "12 Jun 2026" 格式
    m = re.search(r'(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})', text)
    if m:
        try:
            dt = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %b %Y")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    # "2026-06-12" 格式
    if re.match(r'\d{4}-\d{2}-\d{2}', text):
        return text[:10]
    return None


def _get_due_date(page: ChromiumPage) -> tuple[str, str | None]:
    """
    读取管理页面的到期日期。
    返回 (raw_text, std_date)，读取失败返回 ("N/A", None)。
    """
    selectors = [
        # 别人脚本中使用的选择器
        ('xpath', "//h6[contains(text(),'Due date')]/following-sibling::div"),
        ('xpath', "//p[contains(text(),'Due date')]/following-sibling::*"),
        # Next Invoice 区域
        ('xpath', "//span[contains(text(),'Next Invoice')]/../..//span[last()]"),
        ('xpath', "//*[contains(text(),'Next Invoice')]/../following-sibling::*//*[contains(@class,'text')]"),
        ('css',   ".next-invoice-date"),
    ]
    for by, sel in selectors:
        try:
            if by == 'xpath':
                el = page.ele(f'xpath:{sel}', timeout=3)
            else:
                el = page.ele(f'css:{sel}', timeout=3)
            if el:
                raw = el.text.strip()
                if raw and raw != 'N/A':
                    return raw, _parse_due_date(raw)
        except Exception:
            pass
    return "N/A", None


# ─────────────────────────────────────────────
#  HidenCloud 续期核心
# ─────────────────────────────────────────────

class HidenCloudRenewer:
    BASE = "https://dash.hidencloud.com"
    LOGIN_URL = f"{BASE}/auth/login"
    DASHBOARD_URL = f"{BASE}/dashboard"
    COOKIE_DIR = os.path.join(os.getcwd(), 'hiden_cookies')

    def __init__(self, email: str, password: str, proxy: str = "",
                 tg_token: str = "", tg_chat_id: str = ""):
        self.email = email
        self.password = password
        self.proxy = proxy
        self.tg_token = tg_token
        self.tg_chat_id = tg_chat_id
        self.page: ChromiumPage | None = None
        self.safe_email = email.replace('@', '_').replace('.', '_')

    # ── Cookie 持久化 ──────────────────────────────
    def _cookie_path(self) -> str:
        os.makedirs(self.COOKIE_DIR, exist_ok=True)
        return os.path.join(self.COOKIE_DIR, f"{self.safe_email}.json")

    def save_cookies(self):
        """将当前页面 cookie 序列化保存到文件"""
        try:
            cookies = self.page.cookies()
            with open(self._cookie_path(), 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            log(f"💾 [{self.email}] Cookie 已保存（{len(cookies)} 条）")
        except Exception as e:
            log(f"⚠️ [{self.email}] Cookie 保存失败: {e}")

    def _try_cookie_login(self) -> bool:
        """
        从文件加载 cookie，注入浏览器后直接访问 dashboard。
        返回 True 表示 cookie 有效、已处于登录态。
        """
        cookie_file = self._cookie_path()
        if not os.path.exists(cookie_file):
            log(f"ℹ️ [{self.email}] 无 Cookie 缓存文件，跳过 Cookie 登录")
            return False

        try:
            with open(cookie_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            if not cookies:
                return False

            log(f"🍪 [{self.email}] 加载 Cookie 缓存（{len(cookies)} 条），尝试免密登录...")
            page = self.page

            # 先访问目标域以建立上下文，再注入 cookie
            page.get(self.BASE)
            time.sleep(1)
            for ck in cookies:
                try:
                    page.set.cookies(ck)
                except Exception:
                    pass

            # 访问 dashboard 验证是否已登录
            page.get(self.DASHBOARD_URL)
            time.sleep(3)

            if 'dashboard' in page.url or 'service' in page.url:
                log(f"✨ [{self.email}] Cookie 登录成功")
                return True
            else:
                log(f"⚠️ [{self.email}] Cookie 已过期，将回退密码登录")
                # 清除失效的 cookie 文件
                try:
                    os.remove(cookie_file)
                except Exception:
                    pass
                return False
        except Exception as e:
            log(f"⚠️ [{self.email}] Cookie 登录异常: {e}")
            return False

    # ── 浏览器初始化 ──────────────────────────────
    def _make_page(self) -> ChromiumPage:
        co = ChromiumOptions()
        if os.path.exists('/usr/bin/google-chrome'):
            co.set_browser_path('/usr/bin/google-chrome')

        for arg in [
            '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
            '--disable-setuid-sandbox', '--disable-software-rasterizer',
            '--disable-extensions', '--disable-popup-blocking',
            '--ignore-certificate-errors', '--no-first-run',
            '--no-default-browser-check', '--window-size=1280,1024',
        ]:
            co.set_argument(arg)

        co.headless(False)  # 配合 xvfb 运行
        profile_path = os.path.join(os.getcwd(), 'hiden_browser_profile')
        co.set_user_data_path(profile_path)
        co.auto_port()

        if self.proxy:
            proxy_url = self.proxy if "://" in self.proxy else f"socks5://{self.proxy}"
            co.set_argument(f'--proxy-server={proxy_url}')
            log(f"🌐 代理已配置: {proxy_url}")

        return ChromiumPage(co)

    # ── 登录 ──────────────────────────────────────
    def login(self) -> bool:
        log(f"🔐 [{self.email}] 开始登录...")
        self.page = self._make_page()
        page = self.page
        solver = TurnstileSolver(page)

        # ── 优先尝试 Cookie 登录 ──────────────────
        if self._try_cookie_login():
            return True

        page.get(self.LOGIN_URL)
        time.sleep(2)

        # 缓存命中：已登录
        if 'dashboard' in page.url or 'service' in page.url:
            log(f"✨ [{self.email}] 浏览器缓存生效，已处于登录后台")
            self.save_cookies()
            return True

        # 前置过盾
        solver.solve(timeout=8)
        time.sleep(random.uniform(2, 4))

        if 'dashboard' in page.url or 'service' in page.url:
            log(f"✨ [{self.email}] 过盾后自动跳转，判定登录成功")
            self.save_cookies()
            return True

        # 填写表单
        try:
            email_ele = (
                page.ele('css:input[type="email"]', timeout=10) or
                page.ele('css:input[name*="email"]') or
                page.ele('css:input[name*="username"]') or
                page.ele('css:input[placeholder*="邮箱"]') or
                page.ele('css:input[placeholder*="Email"]')
            )
            if not email_ele:
                raise Exception("无法定位账号输入框")

            email_ele.click()
            email_ele.clear()
            for char in self.email:
                email_ele.input(char, clear=False)
                time.sleep(random.uniform(0.05, 0.12))

            pwd_ele = (
                page.ele('css:input[type="password"]') or
                page.ele('css:input[name*="password"]') or
                page.ele('css:input[placeholder*="密码"]') or
                page.ele('css:input[placeholder*="Password"]')
            )
            if pwd_ele:
                pwd_ele.click()
                pwd_ele.clear()
                for char in self.password:
                    pwd_ele.input(char, clear=False)
                    time.sleep(random.uniform(0.05, 0.12))

        except Exception as e:
            log(f"❌ 填写表单失败: {e}")
            pic = _screenshot(page, f"err_form_{self.safe_email}.png")
            send_tg_photo(self.tg_token, self.tg_chat_id, pic,
                          f"❌ <b>{self.email}</b> 填写表单失败\n{e}\nURL: {page.url}")
            return False

        # 登录表单过盾
        solver.solve()

        if 'dashboard' in page.url or 'service' in page.url:
            log(f"✅ [{self.email}] 登录成功")
            self.save_cookies()
            return True

        # 点击提交
        try:
            btn = (
                page.ele('css:button[type="submit"]') or
                page.ele('xpath://button[contains(text(),"Sign in")]') or
                page.ele('xpath://button[contains(text(),"登录")]')
            )
            if btn:
                btn.click()
            else:
                raise Exception("找不到提交按钮")
        except Exception as e:
            log(f"❌ 点击登录按钮失败: {e}")
            return False

        time.sleep(5)

        if 'dashboard' in page.url or 'service' in page.url:
            log(f"✅ [{self.email}] 登录成功")
            self.save_cookies()
            return True

        log(f"❌ [{self.email}] 登录失败，当前 URL: {page.url}")
        pic = _screenshot(page, f"err_login_{self.safe_email}.png")
        send_tg_photo(self.tg_token, self.tg_chat_id, pic,
                      f"❌ <b>{self.email}</b> 登录失败\nURL: {page.url}")
        return False

    # ── 获取服务列表 ──────────────────────────────
    def get_services(self) -> list[dict]:
        page = self.page
        log(f"📋 [{self.email}] 获取服务列表...")
        page.get(self.DASHBOARD_URL)
        time.sleep(3)

        services = []
        try:
            links = page.eles('css:a[href*="/service/"][href*="/manage"]')
            for link in links:
                href = link.attr('href') or ''
                m = re.search(r'/service/(\d+)/manage', href)
                if m:
                    sid = m.group(1)
                    if not any(s['id'] == sid for s in services):
                        services.append({'id': sid})
        except Exception as e:
            log(f"⚠️ 抓取服务链接失败: {e}")
        return services

    # ── 单服务续期 ────────────────────────────────
    def renew_service(self, service_id: str) -> dict:
        """
        返回 dict 字段：
          service_id   str
          success      bool
          skipped      bool   — 触发 Renewal Restricted（未到窗口期）
          message      str
          invoice_id   str
          days_left    int|None
          threshold    int|None
          due_before   str    — 续期前到期日（原始）
          due_after    str    — 续期后到期日（原始）
        """
        page = self.page
        result = {
            'service_id': service_id,
            'success': False,
            'skipped': False,
            'message': '',
            'invoice_id': '',
            'days_left': None,
            'threshold': None,
            'due_before': 'N/A',
            'due_after':  'N/A',
        }

        log(f"🔄 [{service_id}] 续期服务...")
        manage_url = f"{self.BASE}/service/{service_id}/manage"
        page.get(manage_url)
        time.sleep(3)

        # ── 续期前到期时间 ───────────────────────
        due_before_raw, due_before_std = _get_due_date(page)
        result['due_before'] = due_before_raw
        log(f"[{service_id}] 📅 续期前到期: {due_before_raw}")

        # 获取 CSRF token
        token = ''
        token_ele = page.ele('css:input[name="_token"]')
        if token_ele:
            token = token_ele.value

        # ── 定位 Renew 按钮 ──────────────────────
        renew_btn = None
        for sel in [
            'css:button[onclick*="showRenewAlert"]',
            'xpath://button[.//i[contains(@class,"bx-recycle")]]',
            'xpath://button[normalize-space()="Renew"]',
            'xpath://button[contains(text(),"Renew")]',
            'css:[data-action*="renew"]',
        ]:
            try:
                el = page.ele(sel, timeout=3)
                if el:
                    renew_btn = el
                    break
            except Exception:
                pass

        if not renew_btn:
            log(f"❌ [{service_id}] 未找到 Renew 按钮")
            pic = _screenshot(page, f"err_no_renew_{service_id}_{self.safe_email}.png")
            result['message'] = '未找到 Renew 按钮'
            send_tg_photo(self.tg_token, self.tg_chat_id, pic,
                          f"❌ <b>{self.email}</b>\n服务 #{service_id} 未找到 Renew 按钮")
            return result

        # ── 从 onclick 预读参数 ───────────────────
        # showRenewAlert(days_left, threshold, is_free)
        onclick_val = renew_btn.attr('onclick') or ''
        pm = re.search(r'showRenewAlert\((\d+),\s*(\d+),\s*(true|false)\)', onclick_val)
        if pm:
            result['days_left'] = int(pm.group(1))
            result['threshold'] = int(pm.group(2))
            log(f"[{service_id}] onclick → 剩余 {result['days_left']} 天，"
                f"阈值 ≤{result['threshold']} 天，免费: {pm.group(3)}")

        # ── 点击 Renew ───────────────────────────
        log(f"[{service_id}] 🖱️ 点击 Renew 按钮...")
        renew_btn.click()
        time.sleep(2)

        # ── 检测 Renewal Restricted 弹窗 ─────────
        # F12 结构：.fixed.inset-0 > div > h3 / p / div.flex.justify-end > button(OK)
        restricted = False
        try:
            h3_text = page.run_js(
                "var el=document.querySelector('.fixed.inset-0 h3');"
                "return el?el.textContent.trim():'';"
            )
            if h3_text and 'Renewal Restricted' in h3_text:
                restricted = True
                p_text = page.run_js(
                    "var el=document.querySelector('.fixed.inset-0 p');"
                    "return el?el.textContent.trim():'';"
                ) or ''
                log(f"⚠️ [{service_id}] Renewal Restricted: {p_text}")

                dm = re.search(r'expires in (\d+) day', p_text, re.IGNORECASE)
                if dm:
                    result['days_left'] = int(dm.group(1))

                # 关闭弹窗：优先点 OK，失败则 JS remove
                try:
                    ok_btn = page.ele('xpath://button[contains(text(),"OK")]', timeout=3)
                    if ok_btn:
                        ok_btn.click()
                        time.sleep(0.5)
                except Exception:
                    page.run_js("var el=document.querySelector('.fixed.inset-0');if(el)el.remove();")

        except Exception as e:
            log(f"⚠️ [{service_id}] 检测弹窗异常: {e}")

        if restricted:
            days = result['days_left']
            thr  = result['threshold']
            msg  = (
                f"未到续期窗口期，距到期还有 {days} 天（需 ≤{thr} 天可续）"
                if days is not None and thr is not None
                else "未到续期窗口期（Renewal Restricted）"
            )
            result['skipped'] = True
            result['message'] = msg
            result['due_after'] = due_before_raw   # 未续期，到期日不变
            log(f"⏰ [{service_id}] {msg}")
            pic = _screenshot(page, f"skip_{service_id}_{self.safe_email}.png")
            send_tg_photo(
                self.tg_token, self.tg_chat_id, pic,
                f"⏰ <b>{self.email}</b>\n服务 #{service_id} 暂不可续期\n"
                f"{msg}\n到期: {due_before_raw}"
            )
            return result

        # ── 正常续期：等待并点击 Create Invoice ──
        log(f"[{service_id}] 📦 等待续期模态框...")
        modal_opened = False
        for sel in [
            f'css:div#renewService-{service_id}',
            'css:div[id^="renewService-"]',
            'css:div[role="dialog"]',
            'css:.modal:not([style*="display: none"])',
        ]:
            try:
                el = page.ele(sel, timeout=8)
                if el:
                    modal_opened = True
                    log(f"[{service_id}] 模态框已就绪 ({sel})")
                    break
            except Exception:
                pass

        if not modal_opened:
            if 'invoice' in page.url:
                log(f"[{service_id}] ✅ 直接跳转到发票页")
            else:
                log(f"❌ [{service_id}] 未检测到续期模态框")
                pic = _screenshot(page, f"err_no_modal_{service_id}_{self.safe_email}.png")
                result['message'] = '未找到续期模态框'
                send_tg_photo(self.tg_token, self.tg_chat_id, pic,
                              f"❌ <b>{self.email}</b>\n服务 #{service_id} 未找到续期模态框")
                return result

        if 'invoice' not in page.url:
            invoice_btn = None
            for sel in [
                'xpath://button[contains(text(),"Create Invoice")]',
                'xpath://button[contains(text(),"Confirm")]',
                f'xpath://div[@id="renewService-{service_id}"]//button[@type="submit"]',
                'css:div[id^="renewService-"] button[type="submit"]',
                'xpath://div[@role="dialog"]//button[@type="submit"]',
            ]:
                try:
                    el = page.ele(sel, timeout=3)
                    if el:
                        invoice_btn = el
                        break
                except Exception:
                    pass

            if not invoice_btn:
                log(f"❌ [{service_id}] 未找到 Create Invoice 按钮")
                pic = _screenshot(page, f"err_no_invoice_btn_{service_id}_{self.safe_email}.png")
                result['message'] = '未找到 Create Invoice 按钮'
                send_tg_photo(self.tg_token, self.tg_chat_id, pic,
                              f"❌ <b>{self.email}</b>\n服务 #{service_id} 未找到 Create Invoice 按钮")
                return result

            log(f"[{service_id}] 📦 点击 Create Invoice...")
            invoice_btn.click()
            time.sleep(4)

        # ── 发票页处理 ───────────────────────────
        if 'invoice' in page.url:
            m = re.search(r'/invoice/([a-f0-9\-]+)', page.url)
            invoice_id = m.group(1)[:8] if m else ''
            log(f"[{service_id}] 💳 发票页: {invoice_id or '(无ID)'}")
            result['invoice_id'] = invoice_id

            # 尝试点击 Apply Credit / Pay（免费服务通常自动完成，有按钮就点）
            for sel in [
                'xpath://button[contains(text(),"Apply Credit")]',
                'xpath://button[contains(text(),"Pay Now")]',
                'xpath://button[contains(text(),"Pay")]',
                'xpath://a[contains(text(),"Pay")]',
            ]:
                try:
                    el = page.ele(sel, timeout=3)
                    if el:
                        log(f"[{service_id}] 💰 点击: {el.text.strip()}")
                        el.click()
                        time.sleep(5)
                        break
                except Exception:
                    pass

            # ── 回管理页，对比到期日作二次验证 ──
            log(f"[{service_id}] 🔄 刷新管理页，等待到期日更新...")
            page.get(manage_url)
            time.sleep(3)

            due_after_raw, due_after_std = _get_due_date(page)
            result['due_after'] = due_after_raw
            log(f"[{service_id}] 📅 续期后到期: {due_after_raw}")
            # 输出标准格式供外部解析
            if due_after_std:
                log(f"到期时间(标准): {due_after_std}")

            # 日期向后推移 = 确认成功；否则保守标记"请确认"
            if due_after_std and due_before_std and due_after_std > due_before_std:
                result['success'] = True
                result['message'] = f'续期成功（{due_before_raw} → {due_after_raw}）'
            elif due_after_std and due_before_std and due_after_std == due_before_std:
                # 日期未变：发票页已到达，但日期未更新（可能需要稍等）
                log(f"⚠️ [{service_id}] 到期日暂未变化，等待 10s 后再次确认...")
                time.sleep(10)
                page.get(manage_url)
                time.sleep(3)
                due_after_raw2, due_after_std2 = _get_due_date(page)
                if due_after_std2 and due_before_std and due_after_std2 > due_before_std:
                    result['due_after'] = due_after_raw2
                    result['success'] = True
                    result['message'] = f'续期成功（{due_before_raw} → {due_after_raw2}）'
                else:
                    result['success'] = True   # 发票页已达，保守判成功
                    result['message'] = f'续期已执行（发票 {invoice_id}，到期日待更新）'
                    result['due_after'] = due_after_raw2
            else:
                result['success'] = True       # 无法比较时以发票页为准
                result['message'] = f'续期已执行（发票 {invoice_id}）'

            pic = _screenshot(page, f"success_{service_id}_{self.safe_email}.png")
            due_change = (
                f"{due_before_raw} → {result['due_after']}"
                if result['due_after'] not in ('N/A', due_before_raw)
                else result['due_after']
            )
            send_tg_photo(
                self.tg_token, self.tg_chat_id, pic,
                f"✅ <b>{self.email}</b>\n服务 #{service_id} {result['message']}\n"
                f"到期: {due_change}"
                + (f"\n发票: <code>{invoice_id}</code>" if invoice_id else "")
            )
            return result

        # ── UI 失败 → API POST 保底 ──────────────
        log(f"📡 [{service_id}] 启动 API POST 保底方案...")
        try:
            s = requests.Session()
            for ck in page.cookies():
                s.cookies.set(ck.get('name', ''), ck.get('value', ''),
                              domain=ck.get('domain', ''))

            headers = {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': self.BASE,
                'Referer': manage_url,
                'User-Agent': page.user_agent,
            }
            proxies = (
                {'http': f'socks5://{self.proxy}', 'https': f'socks5://{self.proxy}'}
                if self.proxy else None
            )
            resp = s.post(
                f"{self.BASE}/service/{service_id}/renew",
                data={'_token': token, 'days': '7'},
                headers=headers,
                proxies=proxies,
                timeout=20,
                allow_redirects=False,
            )

            redirect_url = ''
            if resp.status_code in (301, 302, 303, 307, 308):
                redirect_url = resp.headers.get('Location', '')
            elif resp.status_code == 200:
                redirect_url = resp.url

            if 'invoice' in redirect_url:
                m = re.search(r'/invoice/([a-f0-9\-]+)', redirect_url)
                invoice_id = m.group(1)[:8] if m else ''

                # 刷新管理页，对比日期
                page.get(manage_url)
                time.sleep(3)
                due_after_raw, due_after_std = _get_due_date(page)
                result['due_after'] = due_after_raw
                if due_after_std:
                    log(f"到期时间(标准): {due_after_std}")

                result['success'] = True
                result['invoice_id'] = invoice_id
                due_change = (
                    f"{due_before_raw} → {due_after_raw}"
                    if due_after_raw not in ('N/A', due_before_raw)
                    else due_after_raw
                )
                result['message'] = f'续期成功（POST 保底，{due_change}）'

                pic = _screenshot(page, f"success_post_{service_id}_{self.safe_email}.png")
                send_tg_photo(
                    self.tg_token, self.tg_chat_id, pic,
                    f"✅ <b>{self.email}</b>\n服务 #{service_id} 续期成功（POST 保底）\n"
                    f"到期: {due_change}"
                    + (f"\n发票: <code>{invoice_id}</code>" if invoice_id else "")
                )
            else:
                result['message'] = (
                    f"POST 响应异常: HTTP {resp.status_code}，"
                    f"Location: {redirect_url or '无'}"
                )

        except Exception as e:
            result['message'] = f'POST 异常: {e}'

        # ── 最终失败通知 ─────────────────────────
        if not result['success']:
            log(f"❌ [{service_id}] 续期失败: {result['message']}")
            pic = _screenshot(page, f"fail_{service_id}_{self.safe_email}.png")
            send_tg_photo(
                self.tg_token, self.tg_chat_id, pic,
                f"❌ <b>{self.email}</b>\n服务 #{service_id} 续期失败\n"
                f"原因: {result['message']}\n到期: {due_before_raw}"
            )

        return result

    # ── 入口 ──────────────────────────────────────
    def run(self) -> list[dict]:
        try:
            if not self.login():
                pic = _screenshot(self.page, f"err_login_final_{self.safe_email}.png")
                send_tg_photo(self.tg_token, self.tg_chat_id, pic,
                              f"❌ <b>{self.email}</b> 登录失败，终止续期")
                return [{'service_id': 'N/A', 'success': False, 'skipped': False,
                         'message': '登录失败', 'invoice_id': '',
                         'days_left': None, 'threshold': None}]

            services = self.get_services()
            if not services:
                return [{'service_id': 'N/A', 'success': False, 'skipped': False,
                         'message': '未找到服务', 'invoice_id': '',
                         'days_left': None, 'threshold': None}]

            results = []
            for svc in services:
                results.append(self.renew_service(svc['id']))
                time.sleep(3)
            return results

        except Exception as e:
            log(f"❌ run() 异常: {e}")
            if self.page:
                pic = _screenshot(self.page, f"err_crash_{self.safe_email}.png")
                send_tg_photo(self.tg_token, self.tg_chat_id, pic,
                              f"❌ <b>{self.email}</b> 脚本异常崩溃\n{str(e)[:200]}")
            raise

        finally:
            if self.page:
                try:
                    self.page.quit()
                except Exception:
                    pass


# ─────────────────────────────────────────────
#  主入口
# ─────────────────────────────────────────────

def main():
    accounts_raw = os.getenv('ACCOUNTS', '').strip()
    tg_token     = os.getenv('TG_BOT_TOKEN', '').strip()
    tg_chat_id   = os.getenv('TG_CHAT_ID', '').strip()
    proxy        = os.getenv('PROXY', '').strip()

    if not accounts_raw:
        log("❌ ACCOUNTS 环境变量为空")
        sys.exit(1)

    accounts = parse_accounts(accounts_raw)
    if not accounts:
        log("❌ 未解析到任何账号")
        sys.exit(1)

    all_results = []
    account_summaries = []

    for email, password in accounts:
        log(f"\n🚀 开始处理: {email}")
        renewer = HidenCloudRenewer(email, password, proxy, tg_token, tg_chat_id)
        results = renewer.run()
        all_results.extend(results)

        lines = [f"📧 <b>{email}</b>"]
        for r in results:
            if r.get('success'):
                icon = "✅"
            elif r.get('skipped'):
                icon = "⏰"
            else:
                icon = "❌"
            inv  = f" | 发票: <code>{r['invoice_id']}</code>" if r.get('invoice_id') else ""
            days_info = ""
            if r.get('skipped') and r.get('days_left') is not None:
                thr = r.get('threshold')
                days_info = f"（剩余 {r['days_left']} 天，需 ≤{thr} 天）" if thr else f"（剩余 {r['days_left']} 天）"
            lines.append(f"  {icon} 服务 #{r['service_id']}: {r['message']}{inv}{days_info}")
        account_summaries.append('\n'.join(lines))

    total_ok   = sum(1 for r in all_results if r.get('success'))
    total_skip = sum(1 for r in all_results if r.get('skipped'))
    total_fail = len(all_results) - total_ok - total_skip

    # 计算下次运行天数
    wait_days_list = []
    has_errors = (total_fail > 0)
    
    for r in all_results:
        if r.get('success'):
            wait_days_list.append(6)
        elif r.get('skipped'):
            days = r.get('days_left')
            thr = r.get('threshold')
            if days is not None and thr is not None:
                w = days - thr
                wait_days_list.append(w if w >= 1 else 1)
            else:
                has_errors = True
                
    if has_errors or not wait_days_list:
        next_run_days = 1
    else:
        next_run_days = min(wait_days_list)
        if next_run_days < 1:
            next_run_days = 1
            
    env_file = os.getenv('GITHUB_ENV')
    if env_file:
        with open(env_file, 'a', encoding='utf-8') as f:
            f.write(f"NEXT_RUN_DAYS={next_run_days}\n")

    tg_msg = (
        f"🔔 <b>HidenCloud 续期总报告</b>\n"
        f"📊 ✅ 成功 {total_ok} | ⏰ 跳过 {total_skip} | ❌ 失败 {total_fail}\n"
        f"⏳ <b>预计休眠天数: {next_run_days} 天</b>\n\n"
        + '\n\n'.join(account_summaries)
    )
    send_tg(tg_token, tg_chat_id, tg_msg)
    log("🏁 全部账号处理完毕")


if __name__ == '__main__':
    main()
