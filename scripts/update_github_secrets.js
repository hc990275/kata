const fs = require('fs');
const path = require('path');
const axios = require('axios');
const sodium = require('libsodium-wrappers');

// GitHub 配置
const GITHUB_TOKEN = process.env.GITHUB_TOKEN;
const OWNER = 'hc990275';
const REPO = 'kata';
const SECRET_NAME = 'USERS_JSON';

const API_BASE = `https://api.github.com/repos/${OWNER}/${REPO}`;

const headers = {
    'Authorization': `Bearer ${GITHUB_TOKEN}`,
    'Accept': 'application/vnd.github+json',
    'User-Agent': 'Node.js-Script',
    'X-GitHub-Api-Version': '2022-11-28'
};

async function getPublicKey() {
    console.log('[GitHub] 正在获取 Repository Public Key...');
    const response = await axios.get(`${API_BASE}/actions/secrets/public-key`, { headers });
    return response.data;
}

async function encryptSecret(secretValue, keyBase64) {
    await sodium.ready;
    const binKey = sodium.from_base64(keyBase64, sodium.base64_variants.ORIGINAL);
    const binMessage = sodium.from_string(secretValue);
    
    // Encrypt using Libsodium box seal
    const encryptedBytes = sodium.crypto_box_seal(binMessage, binKey);
    return sodium.to_base64(encryptedBytes, sodium.base64_variants.ORIGINAL);
}

async function updateSecret(secretName, encryptedValue, keyId) {
    console.log(`[GitHub] 正在上传/更新 Secret: ${secretName}...`);
    const url = `${API_BASE}/actions/secrets/${secretName}`;
    const data = {
        encrypted_value: encryptedValue,
        key_id: keyId
    };
    const response = await axios.put(url, data, { headers });
    if (response.status === 201 || response.status === 204) {
        console.log(`[GitHub] ✅ Secret ${secretName} 更新成功！ (状态码: ${response.status})`);
    } else {
        throw new Error(`更新失败，状态码: ${response.status}`);
    }
}

async function main() {
    try {
        if (!GITHUB_TOKEN) {
            throw new Error('未设置 GITHUB_TOKEN 环境变量，请在运行脚本前通过 GITHUB_TOKEN=your_token 设置。');
        }
        
        const loginJsonPath = path.join(__dirname, '..', 'login.json');
        if (!fs.existsSync(loginJsonPath)) {
            throw new Error(`找不到 login.json 文件: ${loginJsonPath}`);
        }
        
        console.log('[本地] 正在读取 login.json...');
        const loginData = fs.readFileSync(loginJsonPath, 'utf8');
        // 解析以确保它是合法的 JSON
        const parsed = JSON.parse(loginData);
        // 重新序列化为紧凑的字符串
        const compactJsonStr = JSON.stringify(parsed);
        console.log(`[本地] 读取成功，包含 ${parsed.length} 个账号`);

        // 获取公钥
        const publicKeyInfo = await getPublicKey();
        const { key_id, key } = publicKeyInfo;
        console.log(`[GitHub] 成功获取公钥，Key ID: ${key_id}`);

        // 加密
        console.log('[加密] 正在使用 libsodium 对账号信息进行加密...');
        const encryptedBase64 = await encryptSecret(compactJsonStr, key);
        console.log('[加密] 加密完成');

        // 上传
        await updateSecret(SECRET_NAME, encryptedBase64, key_id);
        
    } catch (error) {
        console.error('❌ 执行过程中发生错误:', error.response ? error.response.data : error.message);
        process.exit(1);
    }
}

main();
