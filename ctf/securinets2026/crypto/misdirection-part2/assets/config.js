const crypto = require('crypto');
const EC = require('elliptic').ec;

const ec = new EC('p256');

const ADMIN_PRIVATE_KEY = process.env.ADMIN_PRIVATE_KEY;
if (!ADMIN_PRIVATE_KEY) {
    console.log('ERROR: ADMIN_PRIVATE_KEY env var not set');
    process.exit(1);
}

const WEBAUTHN_ORIGIN = process.env.WEBAUTHN_ORIGIN;
const keyPair = ec.keyFromPrivate(ADMIN_PRIVATE_KEY, 'hex');
const BOT_USERNAME = `_bot_${crypto.randomBytes(4).toString('hex')}`;

module.exports = { ec, keyPair, WEBAUTHN_ORIGIN, BOT_USERNAME };
