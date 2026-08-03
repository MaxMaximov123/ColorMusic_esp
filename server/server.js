const http = require('http');
const express = require('express');
const path = require('path');
const { WebSocketServer } = require('ws');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const url = require('url');
const knex = require('knex');

const WebSocket = require('ws');

const PORT = process.env.PORT || 3000;
const JWT_SECRET = process.env.JWT_SECRET || 'change-me';
const JWT_EXPIRES = '24h';
const ADMIN_USER = process.env.ADMIN_USER || 'admin';
const ADMIN_PASS = process.env.ADMIN_PASS || 'admin';
const DETECTOR_HOST = process.env.DETECTOR_HOST || 'detector';
const DETECTOR_PORT = process.env.DETECTOR_PORT || '8001';

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

// ── Detector proxy ──

app.use('/api/detector', authMiddleware, (req, res) => {
  const detectorPath = '/api' + req.url;
  const options = {
    hostname: DETECTOR_HOST,
    port: parseInt(DETECTOR_PORT),
    path: detectorPath,
    method: req.method,
    headers: { 'Content-Type': 'application/json' },
  };

  const proxyReq = http.request(options, (proxyRes) => {
    res.status(proxyRes.statusCode);
    Object.entries(proxyRes.headers).forEach(([k, v]) => res.setHeader(k, v));
    proxyRes.pipe(res);
  });

  proxyReq.on('error', (err) => {
    console.error('[detector-proxy]', err.message);
    res.status(502).json({ error: 'Detector service unavailable' });
  });

  if (req.method !== 'GET' && req.body) {
    proxyReq.write(JSON.stringify(req.body));
  }
  proxyReq.end();
});

app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// ── State ──

const devices = new Map();
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
      socket.destroy();
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
      socket.destroy();
      return;
    }
    wssCamera.handleUpgrade(req, socket, head, ws => wssCamera.emit('connection', ws, req));
    return;
  }

  socket.destroy();
});

// ── Camera WS proxy (bridge to detector service) ──

wssCamera.on('connection', (clientWs) => {
  const detectorUrl = `ws://${DETECTOR_HOST}:${DETECTOR_PORT}/ws/stream`;
  const detectorWs = new WebSocket(detectorUrl);
  const pendingMessages = [];
  let detectorOpen = false;

  detectorWs.on('open', () => {
    console.log('[camera-proxy] connected to detector');
    detectorOpen = true;
    for (const { data, isBinary } of pendingMessages) {
      detectorWs.send(data, { binary: isBinary });
    }
    pendingMessages.length = 0;
  });

  clientWs.on('message', (data, isBinary) => {
    if (detectorOpen && detectorWs.readyState === WebSocket.OPEN) {
      detectorWs.send(data, { binary: isBinary });
    } else {
      pendingMessages.push({ data, isBinary });
    }
  });

  detectorWs.on('message', (data, isBinary) => {
    if (clientWs.readyState === WebSocket.OPEN) {
      clientWs.send(data, { binary: isBinary });
    }
  });

  clientWs.on('close', () => {
    detectorWs.close();
    console.log('[camera-proxy] client disconnected');
  });

  detectorWs.on('close', () => {
    if (clientWs.readyState === WebSocket.OPEN) clientWs.close();
  });

  detectorWs.on('error', (err) => {
    console.error('[camera-proxy] detector error:', err.message);
    if (clientWs.readyState === WebSocket.OPEN) {
      clientWs.send(JSON.stringify({ error: 'Detector service unavailable' }));
      clientWs.close();
    }
  });

  clientWs.on('error', () => {
    detectorWs.close();
  });
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
          db('temperature_history')
            .insert({ device_id: deviceId, temperature: msg.state.temp })
            .catch(err => console.error('[db]', err.message));
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
