// 引入必要的依赖库
const { chromium } = require('playwright-extra');
const stealth = require('puppeteer-extra-plugin-stealth')();
const fs = require('fs');

// 启用 stealth 插件，抹除 Playwright 的自动化特征
chromium.use(stealth);

// 从 GitHub Actions 环境变量中读取配置
const USERS_JSON = process.env.USERS_JSON;
const HTTP_PROXY = process.env.HTTP_PROXY;

// --- 针对 ALTCHA 的注入脚本 ---
const ALTCHA_HOOK = `
(function() {
    setInterval(() => {
        const altcha = document.querySelector('altcha-widget');
        if (altcha) {
            const rect = altcha.getBoundingClientRect();
            window.__altcha_data = {
                x: rect.left + rect.width / 2,
                y: rect.top + rect.height / 2,
                state: altcha.getAttribute('state'), // 状态：'unverified', 'verifying', 'verified'
                exists: true
            };
        }
    }, 500); 
})();
`;

/**
 * 处理 ALTCHA 验证的核心函数
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
        await new Promise(r => setTimeout(r, 100));
        await client.send('Input.dispatchMouseEvent', {
            type: 'mouseReleased', x: data.x, y: data.y, button: 'left', clickCount: 1
        });
        await client.detach();
    }
    
    return data.state === 'verified';
}

(async () => {
    if (!USERS_JSON) {
        console.error("❌ 致命错误: 未找到 USERS_JSON 环境变量。");
        process.exit(1);
    }

    const users = JSON.parse(USERS_JSON);
    
    console.log("正在启动 Chromium 浏览器...");
    const browser = await chromium.launch({ 
        headless: false, 
        proxy: HTTP_PROXY ? { server: HTTP_PROXY } : undefined 
    });

    const context = await browser.newContext({
        viewport: { width: 1280, height: 720 }
    });
    const page = await context.newPage();
    
    await page.addInitScript(ALTCHA_HOOK);

    for (const user of users) {
        console.log(`\n=========================================`);
        console.log(`开始处理用户账号: ${user.username}`);
        console.log(`=========================================`);
        
        try {
            console.log("正在访问登录页面...");
            // 【重点修复】：将 networkidle 改为 domcontentloaded，并将超时时间放宽至 60 秒
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
            
            await page.getByRole('button', { name: 'Login' }).click();

            console.log("等待进入 Dashboard...");
            // 跳转也放宽超时限制
            await page.waitForURL('**/dashboard', { timeout: 60000 }).catch(() => {
                console.log("⚠️ URL 等待超时，但可能已加载，尝试继续...");
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
                if (!fs.existsSync('screenshots')) fs.mkdirSync('screenshots');
                await page.screenshot({ path: `screenshots/${user.username}_verified.png` });
                
                console.log("正在提交最终续期请求...");
                await page.locator('#renew-modal').getByRole('button', { name: 'Renew' }).click();
                
                await new Promise(r => setTimeout(r, 3000));
                console.log(`✅ [${user.username}] 续期指令执行完毕。`);
            } else {
                console.log(`❌ [${user.username}] ALTCHA 验证超时，未能执行续期。`);
            }

        } catch (err) {
            console.error(`❌ [${user.username}] 运行过程中发生错误: ${err.message}`);
        }
        
        console.log("清理会话缓存，准备下一个账号...");
        await context.clearCookies();
    }

    console.log("\n所有账号处理完毕，关闭浏览器。");
    await browser.close();
})();
