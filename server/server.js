const http = require('http');
const express = require('express');
const path = require('path');
const { WebSocketServer } = require('ws');
const WebSocket = require('ws');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const url = require('url');
const knex = require('knex');
const crypto = require('crypto');

const PORT = process.env.PORT || 3000;
const JWT_SECRET = process.env.JWT_SECRET || 'change-me';
const JWT_EXPIRES = '24h';
const ADMIN_USER = process.env.ADMIN_USER || 'admin';
const ADMIN_PASS = process.env.ADMIN_PASS || 'admin';

// ── Database (knex) ──

const db = knex({
  client: 'pg',
  connection: {
    host: process.env.DB_HOST || 'localhost',
    port: parseInt(process.env.DB_PORT || '5432'),
    database: process.env.DB_NAME || 'smarthome',
    user: process.env.DB_USER || 'smarthome',
    password: process.env.DB_PASS || 'smarthome',
  },
  pool: { min: 1, max: 10 },
});

async function waitForDB(retries = 20, delay = 2000) {
  for (let i = 0; i < retries; i++) {
    try {
      await db.raw('SELECT 1');
      console.log('[db] connected');
      return;
    } catch (err) {
      console.log(`[db] waiting... (${i + 1}/${retries})`);
      await new Promise(r => setTimeout(r, delay));
    }
  }
  throw new Error('Could not connect to database');
}

async function initDB() {
  if (!(await db.schema.hasTable('users'))) {
    await db.schema.createTable('users', t => {
      t.increments('id').primary();
      t.string('username', 50).unique().notNullable();
      t.string('password_hash', 255).notNullable();
      t.timestamp('created_at').defaultTo(db.fn.now());
    });
  }

  if (!(await db.schema.hasTable('devices'))) {
    await db.schema.createTable('devices', t => {
      t.string('id', 64).primary();
      t.string('name', 100).notNullable().defaultTo('');
      t.string('device_type', 20).notNullable().defaultTo('unknown');
      t.timestamp('last_seen');
      t.timestamp('created_at').defaultTo(db.fn.now());
    });
  }

  if (!(await db.schema.hasTable('temperature_history'))) {
    await db.schema.createTable('temperature_history', t => {
      t.bigIncrements('id').primary();
      t.string('device_id', 64).notNullable()
        .references('id').inTable('devices').onDelete('CASCADE');
      t.float('temperature').notNullable();
      t.timestamp('recorded_at').defaultTo(db.fn.now());
      t.index(['device_id', 'recorded_at']);
    });
  }

  if (!(await db.schema.hasTable('ipeye_credentials'))) {
    await db.schema.createTable('ipeye_credentials', t => {
      t.integer('user_id').primary()
        .references('id').inTable('users').onDelete('CASCADE');
      t.string('ipeye_login', 255).notNullable();
      t.string('ipeye_password', 255).notNullable();
      t.timestamp('updated_at').defaultTo(db.fn.now());
    });
  }

  await db.raw(`
    CREATE INDEX IF NOT EXISTS idx_temp_device_time
      ON temperature_history(device_id, recorded_at DESC);
    CREATE INDEX IF NOT EXISTS idx_temp_recorded_at
      ON temperature_history(recorded_at DESC)
  `).catch(() => {});

  const admin = await db('users').where('username', ADMIN_USER).first();
  if (!admin) {
    const hash = await bcrypt.hash(ADMIN_PASS, 10);
    await db('users').insert({ username: ADMIN_USER, password_hash: hash });
    console.log(`[db] admin user "${ADMIN_USER}" created`);
  }
}

// ── Express ──

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

function authMiddleware(req, res, next) {
  const header = req.headers.authorization;
  if (!header || !header.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'No token' });
  }
  try {
    req.user = jwt.verify(header.slice(7), JWT_SECRET);
    next();
  } catch {
    res.status(401).json({ error: 'Invalid token' });
  }
}

app.post('/api/login', async (req, res) => {
  const { username, password } = req.body || {};
  if (!username || !password) {
    return res.status(400).json({ error: 'Username and password required' });
  }
  try {
    const user = await db('users').where('username', username).first();
    if (!user) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    const valid = await bcrypt.compare(password, user.password_hash);
    if (!valid) {
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    const token = jwt.sign({ id: user.id, username }, JWT_SECRET, { expiresIn: JWT_EXPIRES });
    res.json({ token, username });
  } catch (err) {
    console.error('[login]', err.message);
    res.status(500).json({ error: 'Server error' });
  }
});

app.get('/api/devices', authMiddleware, (req, res) => {
  res.json(buildDeviceList());
});

app.get('/api/temperature/:deviceId', authMiddleware, async (req, res) => {
  const hours = parseInt(req.query.hours) || 24;
  try {
    const rows = await db('temperature_history')
      .where('device_id', req.params.deviceId)
      .where('recorded_at', '>', db.raw("NOW() - INTERVAL '1 hour' * ?", [hours]))
      .orderBy('recorded_at', 'asc')
      .select('temperature', 'recorded_at');
    res.json(rows);
  } catch (err) {
    console.error('[temp-history]', err.message);
    res.status(500).json({ error: 'Server error' });
  }
});

// ── IPeye Client ──

const IPEYE_BASE = 'https://www.ipeye.ru/ipeye_service/index.php';
const IPEYE_SITE = 'https://www.ipeye.ru';
const IPEYE_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36';

class IpeyeClient {
  constructor() {
    this.cookies = {};
    this.loggedIn = false;
  }

  _parseCookies(resp) {
    const setCookie = resp.headers.getSetCookie?.() || [];
    for (const h of setCookie) {
      const pair = h.split(';')[0];
      const eq = pair.indexOf('=');
      if (eq > 0) this.cookies[pair.slice(0, eq).trim()] = pair.slice(eq + 1).trim();
    }
  }

  _cookieStr() {
    return Object.entries(this.cookies).map(([k, v]) => `${k}=${v}`).join('; ');
  }

  async login(login, password) {
    let r = await fetch(IPEYE_SITE + '/', {
      headers: { 'User-Agent': IPEYE_UA, Accept: 'text/html' },
    });
    this._parseCookies(r);

    r = await fetch(`${IPEYE_BASE}?route=proc_login`, {
      method: 'POST',
      headers: {
        'User-Agent': IPEYE_UA,
        Accept: 'application/json, text/javascript, */*; q=0.01',
        Referer: IPEYE_SITE + '/',
        'X-Requested-With': 'XMLHttpRequest',
        Origin: IPEYE_SITE,
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        Cookie: this._cookieStr(),
      },
      body: new URLSearchParams({
        service_url_relative: 'ipeye_service/',
        login,
        pass: password,
        captcha: 'false',
      }).toString(),
    });
    this._parseCookies(r);
    if (r.status !== 200) return false;

    r = await fetch(`${IPEYE_BASE}?route=page_index`, {
      headers: {
        'User-Agent': IPEYE_UA, Accept: 'text/html',
        Referer: IPEYE_SITE + '/', Cookie: this._cookieStr(),
      },
    });
    this._parseCookies(r);
    this.loggedIn = true;
    return true;
  }

  async getCameras() {
    const data = new URLSearchParams();
    data.set('draw', '1');
    data.set('start', '0');
    data.set('length', '100');
    data.set('search[value]', '');
    data.set('search[regex]', 'true');
    data.set('order[0][column]', '2');
    data.set('order[0][dir]', 'asc');
    const cols = [
      'devices.devcode', 'devices.devcode', 'devices.name',
      'devices_groups.name', 'tariffs.name', 'devices.dvr_limit',
      '', '', '', 'devices_groups.id', 'devices.permissions',
      'devices.model_id', 'devices.storage_server',
    ];
    cols.forEach((c, i) => {
      data.set(`columns[${i}][data]`, c);
      data.set(`columns[${i}][name]`, '');
      data.set(`columns[${i}][searchable]`, c ? 'true' : 'false');
      data.set(`columns[${i}][orderable]`, c ? 'true' : 'false');
      data.set(`columns[${i}][search][value]`, '');
      data.set(`columns[${i}][search][regex]`, 'false');
    });

    const r = await fetch(`${IPEYE_BASE}?route=proc_device`, {
      method: 'POST',
      headers: {
        'User-Agent': IPEYE_UA,
        Accept: 'application/json, text/javascript, */*; q=0.01',
        Referer: `${IPEYE_BASE}?route=page_index`,
        'X-Requested-With': 'XMLHttpRequest',
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        Cookie: this._cookieStr(),
      },
      body: data.toString(),
    });
    if (r.status !== 200) return [];
    const result = await r.json();
    return (result.data || []).map(d => ({ id: d.devcode || '', name: d.device_name || '' }));
  }

  async authorizeStream(deviceId) {
    const r = await fetch(
      `${IPEYE_BASE}?route=page_play_ajax&new_websocket&devid=${deviceId}`,
      {
        headers: {
          'User-Agent': IPEYE_UA, Accept: '*/*',
          Referer: `${IPEYE_BASE}?route=page_play&devcode=${deviceId}`,
          'X-Requested-With': 'XMLHttpRequest',
          Cookie: this._cookieStr(),
        },
      }
    );
    if (r.status !== 200) return null;
    const data = await r.json();
    return data.server || null;
  }
}

const ipeyeSessions = new Map();

// ── IPeye endpoints ──

app.get('/api/ipeye/connect', authMiddleware, async (req, res) => {
  try {
    const cred = await db('ipeye_credentials').where('user_id', req.user.id).first();
    if (!cred) return res.json({ saved: false });

    const client = new IpeyeClient();
    const ok = await client.login(cred.ipeye_login, cred.ipeye_password);
    if (!ok) return res.json({ saved: true, error: 'IPeye auth failed' });

    const cameras = await client.getCameras();
    const sessionId = crypto.randomUUID();
    ipeyeSessions.set(sessionId, client);
    res.json({ session: sessionId, cameras });
  } catch (err) {
    console.error('[ipeye-connect]', err.message);
    res.json({ saved: false, error: 'Сервис видеонаблюдения недоступен' });
  }
});

app.post('/api/ipeye/login', authMiddleware, async (req, res) => {
  const { login, password } = req.body || {};
  if (!login || !password) return res.status(400).json({ error: 'Credentials required' });
  try {
    const client = new IpeyeClient();
    const ok = await client.login(login, password);
    if (!ok) return res.status(401).json({ error: 'Ошибка авторизации IPeye' });

    const cameras = await client.getCameras();
    const sessionId = crypto.randomUUID();
    ipeyeSessions.set(sessionId, client);
    res.json({ session: sessionId, cameras });
  } catch (err) {
    console.error('[ipeye-login]', err.message);
    res.status(500).json({ error: 'Сервис видеонаблюдения недоступен' });
  }
});

app.post('/api/ipeye/save', authMiddleware, async (req, res) => {
  const { login, password } = req.body || {};
  if (!login || !password) return res.status(400).json({ error: 'Credentials required' });
  try {
    await db('ipeye_credentials')
      .insert({ user_id: req.user.id, ipeye_login: login, ipeye_password: password, updated_at: db.fn.now() })
      .onConflict('user_id')
      .merge({ ipeye_login: login, ipeye_password: password, updated_at: db.fn.now() });
    res.json({ ok: true });
  } catch (err) {
    console.error('[ipeye-save]', err.message);
    res.status(500).json({ error: 'Server error' });
  }
});

app.delete('/api/ipeye/forget', authMiddleware, async (req, res) => {
  try {
    await db('ipeye_credentials').where('user_id', req.user.id).delete();
    res.json({ ok: true });
  } catch (err) {
    console.error('[ipeye-forget]', err.message);
    res.status(500).json({ error: 'Server error' });
  }
});

app.get('/api/ipeye/authorize/:cameraId', authMiddleware, async (req, res) => {
  const { session } = req.query;
  if (!session || !ipeyeSessions.has(session)) {
    return res.status(400).json({ error: 'Invalid IPeye session' });
  }
  try {
    const client = ipeyeSessions.get(session);
    const wsServer = await client.authorizeStream(req.params.cameraId);
    if (!wsServer) return res.status(502).json({ error: 'Failed to authorize stream' });
    res.json({ wsUrl: `wss://${wsServer}/ws/mp4/live?name=${req.params.cameraId}` });
  } catch (err) {
    console.error('[ipeye-authorize]', err.message);
    res.status(500).json({ error: 'Authorization failed' });
  }
});

app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// ── State ──

const devices = new Map();
const lastTempWrite = new Map();
const clients = new Set();

function buildDeviceList() {
  const list = [];
  for (const [id, dev] of devices) {
    list.push({ id, name: dev.name, deviceType: dev.deviceType, online: dev.online, state: dev.state });
  }
  return list;
}

function broadcastToClients(msg) {
  const data = JSON.stringify(msg);
  for (const ws of clients) {
    if (ws.readyState === 1) ws.send(data);
  }
}

function sendJson(ws, obj) {
  if (ws.readyState === 1) ws.send(JSON.stringify(obj));
}

// ── WebSocket ──

const server = http.createServer(app);
const wssDevice = new WebSocketServer({ noServer: true });
const wssClient = new WebSocketServer({ noServer: true });
const wssCamera = new WebSocketServer({ noServer: true });

server.on('upgrade', (req, socket, head) => {
  const { pathname, query } = url.parse(req.url, true);

  if (pathname === '/ws/device') {
    wssDevice.handleUpgrade(req, socket, head, ws => wssDevice.emit('connection', ws, req));
    return;
  }

  if (pathname === '/ws/client') {
    const token = query.token;
    if (!token) { socket.destroy(); return; }
    try {
      jwt.verify(token, JWT_SECRET);
    } catch {
      wssClient.handleUpgrade(req, socket, head, ws => {
        ws.close(4001, 'Token expired');
      });
      return;
    }
    wssClient.handleUpgrade(req, socket, head, ws => wssClient.emit('connection', ws, req));
    return;
  }

  if (pathname === '/ws/camera') {
    const token = query.token;
    if (!token) { socket.destroy(); return; }
    try {
      jwt.verify(token, JWT_SECRET);
    } catch {
      wssCamera.handleUpgrade(req, socket, head, ws => {
        ws.close(4001, 'Token expired');
      });
      return;
    }
    wssCamera.handleUpgrade(req, socket, head, ws => wssCamera.emit('connection', ws, req));
    return;
  }

  socket.destroy();
});

// ── Device connections ──

wssDevice.on('connection', (ws, req) => {
  let deviceId = null;
  console.log('[device] connected from', req.socket.remoteAddress);

  ws.isAlive = true;
  ws.on('pong', () => { ws.isAlive = true; });

  ws.on('message', (raw) => {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }

    if (msg.type === 'hello' && msg.deviceId) {
      deviceId = msg.deviceId;
      const existing = devices.get(deviceId);
      if (existing && existing.ws && existing.ws !== ws) {
        try { existing.ws.close(); } catch {}
      }
      const deviceType = msg.deviceType || 'unknown';
      devices.set(deviceId, {
        ws,
        state: msg.state || {},
        name: msg.name || deviceId,
        deviceType,
        online: true,
      });
      console.log('[device] registered:', deviceId, deviceType, msg.name || '');

      db('devices')
        .insert({ id: deviceId, name: msg.name || deviceId, device_type: deviceType, last_seen: db.fn.now() })
        .onConflict('id')
        .merge({ name: msg.name || deviceId, device_type: deviceType, last_seen: db.fn.now() })
        .catch(err => console.error('[db]', err.message));

      broadcastToClients({ type: 'devices', devices: buildDeviceList() });
    }

    if (msg.type === 'state' && deviceId) {
      const dev = devices.get(deviceId);
      if (dev) {
        dev.state = { ...dev.state, ...msg.state };

        if (dev.deviceType === 'tempsensor' && msg.state.temp !== undefined) {
          const now = Date.now();
          const lastWrite = lastTempWrite.get(deviceId) || 0;
          if (now - lastWrite >= 600_000) {
            lastTempWrite.set(deviceId, now);
            db('temperature_history')
              .insert({ device_id: deviceId, temperature: msg.state.temp })
              .catch(err => console.error('[db]', err.message));
          }
        }

        db('devices')
          .where('id', deviceId)
          .update({ last_seen: db.fn.now() })
          .catch(err => console.error('[db]', err.message));

        broadcastToClients({ type: 'state', deviceId, state: dev.state });
      }
    }

    if (msg.type === 'calibrate' && deviceId) {
      broadcastToClients({ type: 'calibrate', deviceId, result: msg.result });
    }
  });

  ws.on('close', () => {
    console.log('[device] disconnected:', deviceId || '(unregistered)');
    if (deviceId) {
      const dev = devices.get(deviceId);
      if (dev && dev.ws === ws) {
        dev.online = false;
        dev.ws = null;
        broadcastToClients({ type: 'devices', devices: buildDeviceList() });
      }
    }
  });

  ws.on('error', (err) => console.log('[device] error:', err.message));
});

// ── Client connections ──

wssClient.on('connection', (ws, req) => {
  clients.add(ws);
  console.log('[client] connected', `(${clients.size} total)`);

  ws.isAlive = true;
  ws.on('pong', () => { ws.isAlive = true; });

  sendJson(ws, { type: 'devices', devices: buildDeviceList() });

  ws.on('message', (raw) => {
    let msg;
    try { msg = JSON.parse(raw); } catch { return; }

    if (msg.type === 'list') {
      sendJson(ws, { type: 'devices', devices: buildDeviceList() });
      return;
    }

    if (msg.type === 'get' && msg.deviceId) {
      const dev = devices.get(msg.deviceId);
      if (dev) sendJson(ws, { type: 'state', deviceId: msg.deviceId, state: dev.state });
      return;
    }

    if (msg.type === 'set' && msg.deviceId && msg.params) {
      const dev = devices.get(msg.deviceId);
      if (dev && dev.ws && dev.online) {
        sendJson(dev.ws, { type: 'set', params: msg.params });
      }
      return;
    }

    if (msg.type === 'calibrate' && msg.deviceId) {
      const dev = devices.get(msg.deviceId);
      if (dev && dev.ws && dev.online) {
        sendJson(dev.ws, { type: 'calibrate' });
      }
      return;
    }
  });

  ws.on('close', () => {
    clients.delete(ws);
    console.log('[client] disconnected', `(${clients.size} remaining)`);
  });

  ws.on('error', (err) => console.log('[client] error:', err.message));
});

// ── Camera WS proxy ──

wssCamera.on('connection', (clientWs, req) => {
  const { session, camera } = url.parse(req.url, true).query;

  clientWs.isAlive = true;
  clientWs.on('pong', () => { clientWs.isAlive = true; });

  if (!session || !camera || !ipeyeSessions.has(session)) {
    clientWs.send(JSON.stringify({ error: 'Invalid session' }));
    clientWs.close();
    return;
  }

  const client = ipeyeSessions.get(session);
  console.log('[camera-proxy] authorizing stream', camera);

  client.authorizeStream(camera).then(wsServer => {
    if (!wsServer) {
      clientWs.send(JSON.stringify({ error: 'Failed to authorize stream' }));
      clientWs.close();
      return;
    }

    const wsUrl = `wss://${wsServer}/ws/mp4/live?name=${camera}`;
    console.log('[camera-proxy] connecting to', wsUrl);

    const ipeyeWs = new WebSocket(wsUrl, {
      headers: {
        'User-Agent': IPEYE_UA,
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Accept-Language': 'ru',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
      },
      origin: IPEYE_SITE,
    });

    ipeyeWs.on('open', () => {
      console.log('[camera-proxy] IPeye connected');
      if (clientWs.readyState === 1) {
        clientWs.send(JSON.stringify({ status: 'connected' }));
      }
    });

    ipeyeWs.on('message', (data, isBinary) => {
      if (clientWs.readyState === 1) {
        clientWs.send(data, { binary: isBinary });
      }
    });

    clientWs.on('close', () => {
      console.log('[camera-proxy] client disconnected');
      ipeyeWs.close();
    });

    ipeyeWs.on('close', () => {
      if (clientWs.readyState === 1) clientWs.close();
    });

    ipeyeWs.on('error', (err) => {
      console.error('[camera-proxy] IPeye error:', err.message);
      if (clientWs.readyState === 1) {
        clientWs.send(JSON.stringify({ error: 'IPeye connection error' }));
        clientWs.close();
      }
    });

    clientWs.on('error', () => ipeyeWs.close());
  }).catch(err => {
    console.error('[camera-proxy] auth error:', err.message);
    if (clientWs.readyState === 1) {
      clientWs.send(JSON.stringify({ error: 'Authorization failed' }));
      clientWs.close();
    }
  });
});

// ── Keepalive ──

const pingTimer = setInterval(() => {
  for (const ws of wssDevice.clients) {
    if (!ws.isAlive) { ws.terminate(); continue; }
    ws.isAlive = false;
    ws.ping();
  }
  for (const ws of wssClient.clients) {
    if (!ws.isAlive) { ws.terminate(); continue; }
    ws.isAlive = false;
    ws.ping();
  }
  for (const ws of wssCamera.clients) {
    if (!ws.isAlive) { ws.terminate(); continue; }
    ws.isAlive = false;
    ws.ping();
  }
}, 30_000);

server.on('close', () => clearInterval(pingTimer));

// ── Start ──

(async () => {
  await waitForDB();
  await initDB();
  server.listen(PORT, '0.0.0.0', () => {
    console.log(`SmartHome server on 0.0.0.0:${PORT}`);
  });
})();
