const { BOT_USERNAME } = require('./config');

const REG_SELECTOR = '#register-button, button:has-text("Register"), button:has-text("register")';
const LOGIN_SELECTOR = '#login-button, button:has-text("Login"), button:has-text("login"), button:has-text("Authenticate")';
const USER_SELECTOR = '#username, input[placeholder*="username" i]';
const SUCCESS_WORDS = ['success', 'welcome', 'flag', 'admin', 'dashboard', 'btc', 'balance'];

async function registerAndLogin(page, targetUrl) {
    const parsedTarget = new URL(targetUrl);
    parsedTarget.pathname = '/auth';
    const authUrl = parsedTarget.toString();

    console.error(`[Bot] Navigating to ${authUrl}`);
    await page.goto(authUrl, { waitUntil: 'domcontentloaded', timeout: 15000 });

    console.error("[Bot] Waiting for auth page to load...");
    await page.waitForSelector(REG_SELECTOR, { timeout: 10000 });
    await page.locator(USER_SELECTOR).first().fill(BOT_USERNAME);

    console.error(`[Bot] Registering as ${BOT_USERNAME}...`);
    await page.locator(REG_SELECTOR).first().click();
    await page.waitForTimeout(3000);

    await page.goto(authUrl, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await page.waitForSelector(LOGIN_SELECTOR, { timeout: 10000 });
    await page.locator(USER_SELECTOR).first().fill(BOT_USERNAME);

    console.error(`[Bot] Logging in as ${BOT_USERNAME}...`);
    await page.locator(LOGIN_SELECTOR).first().click();
    await page.waitForTimeout(4000);

    const body = (await page.innerText('body')).toLowerCase();
    if (SUCCESS_WORDS.some(w => body.includes(w))) {
        console.log("SUCCESS: Bot authenticated successfully.");
    } else {
        throw new Error("Authentication failed: Server did not show a success message.");
    }
}

module.exports = { registerAndLogin };
