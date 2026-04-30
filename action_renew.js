// 引入必要的依赖库
const { chromium } = require('playwright-extra');
const stealth = require('puppeteer-extra-plugin-stealth')();
const fs = require('fs');
const axios = require('axios'); // 引入 axios 用于发送 TG 消息

// 启用 stealth 插件，抹除 Playwright 的自动化特征，降低被风控拦截的概率
chromium.use(stealth);

// 从 GitHub Actions 环境变量中读取配置
const USERS_JSON = process.env.USERS_JSON;
const HTTP_PROXY = process.env.HTTP_PROXY;
const TG_BOT_TOKEN = process.env.TG_BOT_TOKEN; // TG 机器人 Token
const TG_CHAT_ID = process.env.TG_CHAT_ID;     // TG 接收人 ID

/**
 * 发送 Telegram 消息的辅助函数
 * @param {string} message - 要发送的文本消息
 */
async function sendTgMessage(message) {
    if (!TG_BOT_TOKEN || !TG_CHAT_ID) return; // 如果未配置 TG，直接跳过不报错
    try {
        const url = `https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage`;
        await axios.post(url, {
            chat_id: TG_CHAT_ID,
            text: message,
            parse_mode: 'HTML' // 允许使用简单的 HTML 标签格式化消息
        });
        console.log(`[TG 推送] 消息发送成功`);
    } catch (e) {
        console.error(`[TG 推送失败] ${e.message}`);
    }
}

// --- 针对 ALTCHA 的注入脚本 ---
const ALTCHA_HOOK = `
(function() {
    setInterval(() => {
        const altcha = document.querySelector('altcha-widget');
        if (altcha) {
            const rect = altcha.getBoundingClientRect();
            // 将数据挂载到 window 对象，供外部 Playwright 读取
            window.__altcha_data = {
                x: rect.left + rect.width / 2, // 计算元素的中心点 X 坐标
                y: rect.top + rect.height / 2,  // 计算元素的中心点 Y 坐标
                state: altcha.getAttribute('state'), // 状态：'unverified', 'verifying', 'verified'
                exists: true
            };
        }
    }, 500); 
})();
`;

/**
 * 处理 ALTCHA 验证的核心函数
 * @param {object} page - Playwright 的 Page 对象
 * @returns {boolean} - 返回当前是否已验证成功 (verified)
 */
async function handleAltcha(page) {
    const data = await page.evaluate(() => window.__altcha_data);
    if (!data || !data.exists) return false;

    // 如果状态不是 'verified' 也不是 'verifying'，则发起 CDP 物理点击
    if (data.state !== 'verified' && data.state !== 'verifying') {
        console.log(`[ALTCHA] 当前状态: ${data.state}，准备发起物理点击...`);
        const client = await page.context().newCDPSession(page);
        
        await client.send('Input.dispatchMouseEvent', {
            type: 'mousePressed', x: data.x, y: data.y, button: 'left', clickCount: 1
        });
        await new Promise(r => setTimeout(r, 100)); // 模拟人类按压停顿
        await client.send('Input.dispatchMouseEvent', {
            type: 'mouseReleased', x: data.x, y: data.y, button: 'left', clickCount: 1
        });
        await client.detach();
    }
    
    return data.state === 'verified';
}

// 主执行函数
(async () => {
    // 基础配置检查
    if (!USERS_JSON) {
        console.error("❌ 致命错误: 未找到 USERS_JSON 环境变量。");
        process.exit(1);
    }

    const users = JSON.parse(USERS_JSON);
    let tgReport = `<b>Katabump 自动续期报告</b>\n\n`; // 初始化 TG 推送文本

    console.log("正在启动 Chromium 浏览器...");
    const browser = await chromium.launch({ 
        headless: false, // 必须为 false，配合 GitHub Actions 的 xvfb 虚拟屏幕使用
        proxy: HTTP_PROXY ? { server: HTTP_PROXY } : undefined 
    });

    const context = await browser.newContext({
        viewport: { width: 1280, height: 720 }
    });
    const page = await context.newPage();
    
    // 全局注入 ALTCHA 监控脚本
    await page.addInitScript(ALTCHA_HOOK);

    for (const user of users) {
        console.log(`\n=========================================`);
        console.log(`开始处理用户账号: ${user.username}`);
        console.log(`=========================================`);
        
        try {
            console.log("正在访问登录页面...");
            // 使用 domcontentloaded 和 60s 超时，防止被无限期的后台请求卡死
            await page.goto('https://dashboard.katabump.com/auth/login', { 
                waitUntil: 'domcontentloaded', 
                timeout: 60000 
            });
            
            console.log("填写账号密码信息...");
            await page.getByRole('textbox', { name: 'Email' }).fill(user.username);
            await page.getByRole('textbox', { name: 'Password' }).fill(user.password);
            
            console.log("检测登录页验证码...");
            await new Promise(r => setTimeout(r, 2000)); 
            await handleAltcha(page);
            
            // 【重点修复】使用 exact: true 精确匹配，防止与 "Login with Discord" 按钮冲突
            console.log("点击登录按钮...");
            await page.getByRole('button', { name: 'Login', exact: true }).click();

            console.log("等待进入 Dashboard...");
            await page.waitForURL('**/dashboard', { timeout: 60000 }).catch(() => {
                console.log("⚠️ URL 等待超时，尝试继续执行...");
            });
            
            console.log("进入服务器详情页...");
            await page.getByRole('link', { name: 'See' }).first().click();

            console.log("查找续期按钮...");
            const renewBtn = page.getByRole('button', { name: 'Renew' }).first();
            await renewBtn.waitFor({ state: 'visible', timeout: 30000 });
            await renewBtn.click();

            console.log("等待续期确认模态框...");
            await page.locator('#renew-modal').waitFor({ state: 'visible', timeout: 30000 });
            
            console.log("等待 ALTCHA PoW 算力验证完成...");
            let isVerified = false;
            // 循环 30 次，每次 1 秒，等待 CPU 算力验证完成
            for (let i = 0; i < 30; i++) {
                await handleAltcha(page);
                isVerified = await page.evaluate(() => window.__altcha_data?.state === 'verified');
                
                if (isVerified) {
                    console.log(`[ALTCHA] 第 ${i + 1} 次检查：验证成功！`);
                    break;
                }
                await page.waitForTimeout(1000); 
            }

            if (isVerified) {
                // 保存截图供 GitHub Actions Artifacts 查阅
                if (!fs.existsSync('screenshots')) fs.mkdirSync('screenshots');
                await page.screenshot({ path: `screenshots/${user.username}_verified.png` });
                
                console.log("正在提交最终续期请求...");
                await page.locator('#renew-modal').getByRole('button', { name: 'Renew' }).click();
                
                await new Promise(r => setTimeout(r, 3000));
                console.log(`✅ [${user.username}] 续期指令执行完毕。`);
                tgReport += `✅ ${user.username} - 续期成功\n`;
            } else {
                console.log(`❌ [${user.username}] ALTCHA 验证超时，未能执行续期。`);
                tgReport += `❌ ${user.username} - 验证码计算超时\n`;
            }

        } catch (err) {
            console.error(`❌ [${user.username}] 运行过程中发生错误: ${err.message}`);
            // 只截取错误信息的第一行发送给 TG，避免信息过长
            tgReport += `⚠️ ${user.username} - 运行错误: ${err.message.split('\n')[0]}\n`;
        }
        
        console.log("清理会话缓存，准备下一个账号...");
        await context.clearCookies();
    }

    console.log("\n所有账号处理完毕，关闭浏览器。");
    await browser.close();
    
    // 所有任务结束后，发送 TG 报告
    tgReport += `\n<i>执行时间: ${new Date().toISOString()}</i>`;
    await sendTgMessage(tgReport);
})();
