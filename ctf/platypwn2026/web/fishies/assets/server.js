const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const cookieParser = require('cookie-parser');
const Handlebars = require('handlebars');
const puppeteer = require('puppeteer');

// App

const app = express();
const fishes = new Map();
const adminToken = crypto.randomBytes(32).toString('hex');
const template = Handlebars.compile(
  fs.readFileSync(path.join(__dirname, 'templates', 'index.html'), 'utf8'),
);
let nextId = 1;

app.use(express.json({ limit: '64kb' }));
app.use(cookieParser());
app.use('/static', express.static(path.join(__dirname, 'static')));

function isAdmin(req) {
  return req.cookies.admin === adminToken;
}

function requireAdmin(req, res, next) {
  if (!isAdmin(req)) return res.status(403).send('admin only');
  next();
}

// Pages

app.get('/', (req, res) => {
  res.type('html').send(template({
    fishes: [...fishes.values()].map(fishView),
    isAdmin: isAdmin(req),
  }));
});

function fishView(fish) {
  return {
    ...fish,
    reviewAscii: escape(fish.ascii),
    adminFishData: JSON.stringify(fish.fishData, null, 2),
    style: [
      `--duration:${number(fish.fishData.duration)}s`,
      `--depth:${number(fish.fishData.depth)}%`,
      `--bob:${number(fish.fishData.bob)}px`,
      `--delay:-${number(fish.fishData.delay)}s`,
      `--hue:${number(fish.fishData.hue)}`,
    ].join(';'),
  };
}

function escape(value) {
  let escaped = String(value);
  for (let index = 0; index < escaped.length; index++) {
    const replacement = escaped[index] === '<' ? '&lt;' : escaped[index] === '>' ? '&gt;' : null;
    if (!replacement) continue;
    escaped = escaped.slice(0, index) + replacement + escaped.slice(index + 1);
    index += replacement.length;
  }
  return escaped;
}

// Add Fish

app.post('/api/fish', (req, res) => {
  const id = addFish(req.body.ascii, req.body.fishData);
  res.json({ id });
});

function addFish(ascii, fishData) {
  const id = nextId++;
  fishes.set(id, {
    id,
    ascii: String(ascii || '><>').slice(0, 2000),
    fishData: checkFishData(fishData),
  });
  return id;
}

function checkFishData(value = {}) {
  return {
    duration: number(value.duration),
    depth: number(value.depth),
    bob: number(value.bob),
    delay: number(value.delay),
    hue: number(value.hue),
    direction: value.direction === 'right' ? 'right' : 'left',
  };
}

function number(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 1;
}

addFish('::<>', { duration: 12, depth: 16, bob: 3, delay: 2, hue: 185, direction: 'right' });
addFish('><>', { duration: 17, depth: 39, bob: 7, delay: 9, hue: 205, direction: 'right' });
addFish('><(((\'>', { duration: 8, depth: 63, bob: 4, delay: 4, hue: 165, direction: 'right' });
addFish("<'))))><", { duration: 14, depth: 81, bob: 8, delay: 7, hue: 40 });

// Fish Reporting

app.post('/api/fish/:id/report', (req, res) => {
  const id = Number(req.params.id);
  if (!fishes.has(id)) return res.status(404).send('fish not found');
  res.send('review queued');
  visitFish(id);
});

async function visitFish(id) {
  let browser;
  try {
    browser = await puppeteer.launch({
      headless: true,
      args: ['--no-sandbox', '--disable-setuid-sandbox'],
      timeout: 30000,
    });
    await browser.setCookie({
      name: 'admin', value: adminToken, domain: '127.0.0.1',
      path: '/', httpOnly: true, sameSite: 'Strict',
    });
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:80/#fish${id}`, {
      waitUntil: 'domcontentloaded', timeout: 10000,
    });
    await new Promise(resolve => setTimeout(resolve, 20000));
  } catch (error) {
    console.error('review failed:', error.message);
  } finally {
    if (browser) await browser.close().catch(() => { });
  }
}

// Update Fish Data

app.patch('/api/admin/fish/:id/fishData', requireAdmin, (req, res) => {
  const fish = fishes.get(Number(req.params.id));
  if (!fish) return res.status(404).send('fish not found');
  combine(fish.fishData, req.body);
  res.type('text').send('fish data updated');
});

function obj(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function combine(target, source) {
  if (!obj(target) || !obj(source)) return target;
  for (const key of Object.keys(source)) {
    if (obj(source[key])) {
      if (!target[key]) target[key] = {};
      combine(target[key], source[key]);
    } else {
      target[key] = source[key];
    }
  }
  return target;
}

// Admin Debug Fetch

app.post('/api/admin/fetch', requireAdmin, async (req, res) => {
  try {
    const url = new URL(String(req.body.url));
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)) {
      return res.status(400).send('loopback HTTP only');
    }
    const upstream = await fetch(url, { signal: AbortSignal.timeout(1000) });
    res.status(upstream.status).type('application/json').send(await upstream.text());
  } catch (error) {
    res.status(502).send(String(error.message));
  }
});

// Start

app.listen(80, '0.0.0.0', () => console.log('listening on http://0.0.0.0:80'));
