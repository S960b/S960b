const { chromium } = require('playwright');
const { forgeRegistration, forgeAuthentication } = require('./authenticator');
const { installWebAuthnStub } = require('./browser-stub');
const { registerAndLogin } = require('./auth-flow');

async function runAdminBot(targetUrl) {
    const browser = await chromium.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-gpu']
    });
    const context = await browser.newContext();
    const page = await context.newPage();

    try {
        await page.exposeFunction('forgeRegistration', forgeRegistration);
        await page.exposeFunction('forgeAuthentication', forgeAuthentication);
        await page.addInitScript(installWebAuthnStub);

        await registerAndLogin(page, targetUrl);
    } catch (err) {
        console.log(`ERROR: ${err.message}`);
    } finally {
        await browser.close();
    }
}

runAdminBot(process.argv[2]);
