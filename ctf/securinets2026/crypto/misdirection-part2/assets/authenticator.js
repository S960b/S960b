const crypto = require('crypto');
const cbor = require('cbor');
const { ec, keyPair, WEBAUTHN_ORIGIN } = require('./config');

function sha256(data) {
    return crypto.createHash('sha256').update(data).digest();
}

function buildClientData(type, challengeB64Url) {
    return Buffer.from(JSON.stringify({
        type,
        challenge: challengeB64Url,
        origin: WEBAUTHN_ORIGIN,
        crossOrigin: false
    }));
}

async function forgeRegistration(rpId, challengeB64Url, credIdHex) {
    const clientDataJSON = buildClientData('webauthn.create', challengeB64Url);

    const pub = keyPair.getPublic();
    const coseKey = new Map([
        [1, 2],   // kty: EC2
        [3, -7],  // algo: ES256
        [-1, 1],  // curve: P-256
        [-2, Buffer.from(pub.getX().toArray('be', 32))],
        [-3, Buffer.from(pub.getY().toArray('be', 32))]
    ]);

    const rpIdHash = sha256(rpId);
    const flags = Buffer.from([0x45]); // UserPresense | UserVerification | Attestation
    const signCount = Buffer.alloc(4, 0);
    const aaguid = Buffer.alloc(16, 0);
    const credId = Buffer.from(credIdHex, 'hex');
    const credIdLen = Buffer.alloc(2);
    credIdLen.writeUInt16BE(credId.length, 0);

    const authData = Buffer.concat([rpIdHash, flags, signCount, aaguid, credIdLen, credId, cbor.encode(coseKey)]);
    const attObj = { fmt: 'none', attStmt: {}, authData };

    return {
        credIdHex,
        clientDataJSON: clientDataJSON.toString('base64'),
        attestationObject: cbor.encode(attObj).toString('base64')
    };
}

async function forgeAuthentication(rpId, challengeB64Url, credIdB64Url) {
    const clientDataJSON = buildClientData('webauthn.get', challengeB64Url);
    const clientDataHash = sha256(clientDataJSON);

    const rpIdHash = sha256(rpId);
    const flags = Buffer.from([0x05]); // UserPresent | UserVerif
    const signCount = Buffer.alloc(4);
    signCount.writeUInt32BE(1, 0);

    const authData = Buffer.concat([rpIdHash, flags, signCount]);
    const msgHash = sha256(Buffer.concat([authData, clientDataHash]));
    const signature = keyPair.sign(msgHash, { k: () => ec.keyFromPrivate(rpIdHash, 'hex').getPrivate() });

    return {
        credIdB64Url,
        clientDataJSON: clientDataJSON.toString('base64'),
        authenticatorData: authData.toString('base64'),
        signature: Buffer.from(signature.toDER()).toString('base64')
    };
}

module.exports = { forgeRegistration, forgeAuthentication };
