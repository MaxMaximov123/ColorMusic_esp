const http = require('http');
const express = require('express');
const path = require('path');
const { WebSocketServer } = require('ws');
const url = require('url');

const PORT = process.env.PORT || 3000;

// --- Express ---
const app = express();
app.use(express.static(path.join(__dirname, 'public')));

const server = http.createServer(app);

// --- State ---
const devices = new Map();   // deviceId -> { ws, state, name, online }
const clients = new Set();   // Set<WebSocket>

// --- WebSocket servers (one per path) ---
const wssDevice = new WebSocketServer({ noServer: true });
const wssClient = new WebSocketServer({ noServer: true });

server.on('upgrade', (req, socket, head) => {
  const { pathname } = url.parse(req.url);

  if (pathname === '/ws/device') {
    wssDevice.handleUpgrade(req, socket, head, (ws) => {
      wssDevice.emit('connection', ws, req);
    });
  } else if (pathname === '/ws/client') {
    wssClient.handleUpgrade(req, socket, head, (ws) => {
      wssClient.emit('connection', ws, req);
    });
  } else {
    socket.destroy();
  }
});

// --- Helpers ---

function buildDeviceList() {
  const list = [];
  for (const [id, dev] of devices) {
    list.push({ id, name: dev.name, online: dev.online, state: dev.state });
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

// --- Device connections ---

wssDevice.on('connection', (ws, req) => {
  let deviceId = null;
  console.log('[device] new connection from', req.socket.remoteAddress);

  ws.isAlive = true;
  ws.on('pong', () => { ws.isAlive = true; });

  ws.on('message', (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch (err) {
      console.log('[device] bad JSON:', err.message);
      return;
    }

    if (msg.type === 'hello' && msg.deviceId) {
      deviceId = msg.deviceId;
      const existing = devices.get(deviceId);
      // If there was a previous connection, close it
      if (existing && existing.ws && existing.ws !== ws) {
        try { existing.ws.close(); } catch (_) {}
      }
      devices.set(deviceId, {
        ws,
        state: msg.state || {},
        name: msg.name || deviceId,
        online: true,
      });
      console.log('[device] registered:', deviceId, msg.name || '');
      broadcastToClients({ type: 'devices', devices: buildDeviceList() });
    }

    if (msg.type === 'state' && deviceId) {
      const dev = devices.get(deviceId);
      if (dev) {
        dev.state = { ...dev.state, ...msg.state };
        broadcastToClients({
          type: 'state',
          deviceId,
          state: dev.state,
        });
      }
    }

    if (msg.type === 'calibrate' && deviceId) {
      broadcastToClients({
        type: 'calibrate',
        deviceId,
        result: msg.result,
      });
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

  ws.on('error', (err) => {
    console.log('[device] error:', deviceId || '(unknown)', err.message);
  });
});

// --- Client connections ---

wssClient.on('connection', (ws, req) => {
  clients.add(ws);
  console.log('[client] connected from', req.socket.remoteAddress, `(${clients.size} total)`);

  ws.isAlive = true;
  ws.on('pong', () => { ws.isAlive = true; });

  // Send current device list on connect
  sendJson(ws, { type: 'devices', devices: buildDeviceList() });

  ws.on('message', (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch (err) {
      console.log('[client] bad JSON:', err.message);
      return;
    }

    if (msg.type === 'list') {
      sendJson(ws, { type: 'devices', devices: buildDeviceList() });
      return;
    }

    if (msg.type === 'get' && msg.deviceId) {
      const dev = devices.get(msg.deviceId);
      if (dev) {
        sendJson(ws, { type: 'state', deviceId: msg.deviceId, state: dev.state });
      }
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

  ws.on('error', (err) => {
    console.log('[client] error:', err.message);
  });
});

// --- Keepalive ping every 30 seconds ---

const PING_INTERVAL = 30_000;

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
}, PING_INTERVAL);

server.on('close', () => clearInterval(pingTimer));

// --- Start ---

server.listen(PORT, '0.0.0.0', () => {
  console.log(`ColorMusic server listening on 0.0.0.0:${PORT}`);
  console.log(`  Static files: ${path.join(__dirname, 'public')}`);
  console.log(`  Device WS:    ws://0.0.0.0:${PORT}/ws/device`);
  console.log(`  Client WS:    ws://0.0.0.0:${PORT}/ws/client`);
});
