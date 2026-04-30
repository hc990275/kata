// 引入必要的依赖库
const { chromium } = require('playwright-extra');
const stealth = require('puppeteer-extra-plugin-stealth')();
const fs = require('fs');

// 启用 stealth 插件，抹除 Playwright 的自动化特征，降低被风控拦截的概率
chromium.use(stealth);

// 从 GitHub Actions 环境变量中读取配置
// USERS_JSON 格式应为: [{"username":"xxx","password":"xxx"}]
const USERS_JSON = process.env.USERS_JSON;
const HTTP_PROXY = process.env.HTTP_PROXY;

// --- 针对 ALTCHA 的注入脚本 ---
// 该脚本会定期检查页面上的 <altcha-widget> 元素，并提取其位置和状态
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
    }, 500); // 每 500 毫秒扫描一次
})();
`;

/**
 * 处理 ALTCHA 验证的核心函数
 * @param {object} page - Playwright 的 Page 对象
 * @returns {boolean} - 返回当前是否已验证成功 (verified)
 */
async function handleAltcha(page) {
    // 从浏览器上下文中获取注入脚本抛出的数据
    const data = await page.evaluate(() => window.__altcha_data);
    if (!data || !data.exists) return false;

    // 如果状态不是 'verified' (已验证) 也不是 'verifying' (正在计算)，则发起点击
    if (data.state !== 'verified' && data.state !== 'verifying') {
        console.log(`[ALTCHA] 当前状态: ${data.state}，准备发起物理点击...`);
        // 使用 CDP (Chrome DevTools Protocol) 发送底层的系统级鼠标点击，比 page.click() 更逼真
        const client = await page.context().newCDPSession(page);
        
        // 鼠标按下
        await client.send('Input.dispatchMouseEvent', {
            type: 'mousePressed', x: data.x, y: data.y, button: 'left', clickCount: 1
        });
        
        // 模拟人类按压停顿 100 毫秒
        await new Promise(r => setTimeout(r, 100));
        
        // 鼠标抬起
        await client.send('Input.dispatchMouseEvent', {
            type: 'mouseReleased', x: data.x, y: data.y, button: 'left', clickCount: 1
        });
        
        await client.detach();
    }
    
    return data.state === 'verified';
}

// 主执行函数 (立即执行的异步箭头函数)
(async () => {
    // 基础配置检查
    if (!USERS_JSON) {
        console.error("❌ 致命错误: 未找到 USERS_JSON 环境变量，请在 GitHub Secrets 中配置。");
        process.exit(1);
    }

    const users = JSON.parse(USERS_JSON);
    
    console.log("正在启动 Chromium 浏览器...");
    // 启动浏览器
    // 注意：headless 必须为 false，配合 GitHub Actions 的 xvfb 虚拟屏幕使用，否则 ALTCHA 可能无法正常渲染
    const browser = await chromium.launch({ 
        headless: false, 
        proxy: HTTP_PROXY ? { server: HTTP_PROXY } : undefined 
    });

    // 创建上下文并设置统一的视口大小
    const context = await browser.newContext({
        viewport: { width: 1280, height: 720 }
    });
    const page = await context.newPage();
    
    // 全局注入 ALTCHA 监控脚本
    await page.addInitScript(ALTCHA_HOOK);

    // 遍历处理每个用户账号
    for (const user of users) {
        console.log(`\n=========================================`);
        console.log(`开始处理用户账号: ${user.username}`);
        console.log(`=========================================`);
        
        try {
            // 1. 访问登录页面并等待网络空闲
            console.log("正在访问登录页面...");
            await page.goto('https://dashboard.katabump.com/auth/login', { waitUntil: 'networkidle' });
            
            // 2. 填写账号密码
            console.log("填写账号密码信息...");
            await page.getByRole('textbox', { name: 'Email' }).fill(user.username);
            await page.getByRole('textbox', { name: 'Password' }).fill(user.password);
            
            // 3. 处理登录页面的 ALTCHA 验证码
            console.log("检测登录页验证码...");
            await new Promise(r => setTimeout(r, 2000)); // 预留时间让页面完全加载
            await handleAltcha(page);
            
            // 点击登录按钮
            await page.getByRole('button', { name: 'Login' }).click();

            // 4. 等待跳转到仪表盘
            console.log("等待进入 Dashboard...");
            await page.waitForURL('**/dashboard', { timeout: 15000 });
            
            // 5. 点击首个服务器的 "See" 按钮进入详情页
            console.log("进入服务器详情页...");
            await page.getByRole('link', { name: 'See' }).first().click();

            // 6. 查找并点击第一层的 "Renew" 按钮
            console.log("查找续期按钮...");
            const renewBtn = page.getByRole('button', { name: 'Renew' }).first();
            await renewBtn.waitFor({ state: 'visible' });
            await renewBtn.click();

            // 7. 处理弹出的续期确认模态框
            console.log("等待续期确认模态框...");
            await page.locator('#renew-modal').waitFor({ state: 'visible' });
            
            // 8. 循环等待 ALTCHA 的 PoW (工作量证明) 计算完成
            // ALTCHA 需要消耗 CPU 算力，通常需要几秒钟，最大重试 30 次 (约 30 秒)
            console.log("等待 ALTCHA PoW 算力验证完成...");
            let isVerified = false;
            for (let i = 0; i < 30; i++) {
                // 每次循环尝试触发或检查状态
                await handleAltcha(page);
                isVerified = await page.evaluate(() => window.__altcha_data?.state === 'verified');
                
                if (isVerified) {
                    console.log(`[ALTCHA] 第 ${i + 1} 次检查：验证成功！`);
                    break;
                }
                await page.waitForTimeout(1000); // 每次循环间隔 1 秒
            }

            // 9. 验证通过后，点击最终的确认续期按钮
            if (isVerified) {
                // 保存截图供 GitHub Actions Artifacts 查阅
                if (!fs.existsSync('screenshots')) fs.mkdirSync('screenshots');
                await page.screenshot({ path: `screenshots/${user.username}_verified.png` });
                
                console.log("正在提交最终续期请求...");
                await page.locator('#renew-modal').getByRole('button', { name: 'Renew' }).click();
                
                // 等待一下让请求发出去
                await new Promise(r => setTimeout(r, 3000));
                console.log(`✅ [${user.username}] 续期指令执行完毕。`);
            } else {
                console.log(`❌ [${user.username}] ALTCHA 验证超时，未能执行续期。`);
            }

        } catch (err) {
            console.error(`❌ [${user.username}] 运行过程中发生错误: ${err.message}`);
        }
        
        // 每次处理完一个账号，清空当前上下文的 Cookie，防止串号
        console.log("清理会话缓存，准备下一个账号...");
        await context.clearCookies();
    }

    console.log("\n所有账号处理完毕，关闭浏览器。");
    await browser.close();
})();
