// 本地调试服务器：把 api/index.js 跑在普通 Node 上（Vercel 的 res.status 由 shim 提供）
// 用法：
//   node dev-server.js            → 连真实 Supabase（需先设置环境变量）
//   MOCK=1 node dev-server.js     → 纯内存假数据，只验证客户端联通（端口 3000）
//
// 两种模式都顺手做了三件事，好让本地能完整测「客户端 + 管理平台」：
//   ① 剥掉 /api 前缀 —— 客户端填 http://127.0.0.1:3100 或 .../3100/api 都能通；
//   ② 放开 CORS + 处理 OPTIONS 预检 —— 管理平台用自定义头 x-admin-key，必须预检；
//   ③ GET 走 public/ 静态文件 —— 浏览器直接开 http://127.0.0.1:3100/ 就是管理平台。
const http = require('http');
const fs = require('fs');
const path = require('path');

const MOCK = process.env.MOCK === '1';
const PORT = Number(process.env.PORT || 3000);
const PUBLIC = path.join(__dirname, 'public');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
};

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
  'Access-Control-Allow-Headers': 'content-type, x-admin-key, authorization',
  'Access-Control-Max-Age': '600',
};

function shim(res) {
  res.status = (c) => { res.statusCode = c; return res; };
  return res;
}

// /api/xxx → /xxx（Vercel 上接口挂在 /api 下，本地挂在根上）
function stripApi(req) {
  const u = req.url || '/';
  const v = u.replace(/^\/api(?=\/|$|\?)/, '');
  req.url = v || '/';
}

function serveStatic(url, res) {
  let rel;
  try {
    rel = decodeURIComponent(String(url || '/').split('?')[0]);
  } catch {
    rel = '/';
  }
  if (rel === '/' || rel === '') rel = '/admin.html';
  // 防目录穿越
  const safe = path.normalize(rel).replace(/^(\.\.[/\\])+/, '');
  const file = path.join(PUBLIC, safe);
  if (!file.startsWith(PUBLIC) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    return res.end(`404 ${rel}\n（本地只有 backend/public 下的静态文件；客户端接口是 POST 到同一地址）`);
  }
  res.writeHead(200, {
    'Content-Type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream',
  });
  fs.createReadStream(file).pipe(res);
}

// 返回 true 表示这个请求已经被处理掉了（预检 / 静态）
function preflight(req, res) {
  for (const [k, v] of Object.entries(CORS)) res.setHeader(k, v);
  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    res.end();
    return true;
  }
  if (req.method === 'GET' || req.method === 'HEAD') {
    serveStatic(req.url, res);
    return true;
  }
  return false;
}

if (!MOCK) {
  const fn = require('./api/index.js');
  http.createServer((req, res) => {
    shim(res);
    stripApi(req);
    if (preflight(req, res)) return;
    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => { req.body = body; fn(req, res); });
  }).listen(PORT, () => console.log(`api on http://127.0.0.1:${PORT}/  (real supabase)`));
} else {
  // ---------------------------------------------------------------- mock
  const users = [{ username: 'demo', password: 'demo123', id: '00000000-0000-0000-0000-000000000001' }];
  const ent = { '00000000-0000-0000-0000-000000000001': { status: 'active', expire_at: null, note: '' } };
  const orders = {};
  const anns = [{ id: 1, title: '欢迎', body: '这是模拟公告，部署后可替换', level: 'info', active: true }];
  const ADMIN = process.env.ADMIN_KEY || 'devkey';

  http.createServer((req, res) => {
    shim(res);
    stripApi(req);
    if (preflight(req, res)) return;

    let body = '';
    req.on('data', (c) => (body += c));
    req.on('end', () => {
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      let j = {};
      try { j = JSON.parse(body || '{}'); } catch { /* noop */ }
      const action = j.action || '';
      const out = (o) => res.end(JSON.stringify(o));
      const admin = (req.headers['x-admin-key'] || '') === ADMIN;

      if (action === 'login') {
        const u = users.find((x) => x.username === j.username && x.password === j.password);
        if (!u) return out({ ok: false, error: '账号或密码错误' });
        const e = ent[u.id];
        if (!e || e.status !== 'active') return out({ ok: false, error: '无使用资格' });
        return out({ ok: true, token: 'mock-token', user_id: u.id,
          ent: { allowed: true, permanent: !e.expire_at, expire_at: e.expire_at },
          ann: anns.filter((a) => a.active), quota: { max: 3, used: orders[u.id] || 0 } });
      }
      if (action === 'heartbeat') {
        return out({ ok: true, ent: { allowed: true }, ann: anns.filter((a) => a.active),
          quota: { max: 3, used: 0 } });
      }
      if (action === 'order') {
        orders['00000000-0000-0000-0000-000000000001'] =
          (orders['00000000-0000-0000-0000-000000000001'] || 0) + 1;
        return out({ ok: true, quota: { max: 3, used: orders['00000000-0000-0000-0000-000000000001'] } });
      }
      if (action.startsWith('admin.')) {
        if (!admin) return out({ ok: false, error: '管理员密钥错误' });
        if (action === 'admin.ping') return out({ ok: true });
        if (action === 'admin.list') {
          return out({ ok: true, users: users.map((u) => ({
            id: u.id, username: u.username, ent: ent[u.id] || null, today: orders[u.id] || 0 })) });
        }
        if (action === 'admin.grant') {
          ent[j.user_id] = { status: 'active', expire_at: j.days ? new Date(Date.now() + j.days * 864e5).toISOString() : null };
          return out({ ok: true });
        }
        if (action === 'admin.revoke') {
          if (!ent[j.user_id]) return out({ ok: false, error: '用户不存在' });
          ent[j.user_id].status = 'revoked';
          return out({ ok: true });
        }
        if (action === 'admin.create') {
          if (!j.username || !j.password) return out({ ok: false, error: '账号和密码都要填' });
          if (users.some((u) => u.username === j.username)) return out({ ok: false, error: '账号已存在' });
          const id = 'u' + users.length;
          users.push({ username: j.username, password: j.password, id });
          // 建号即给资格（真实后端由 handle_new_user 触发器 + admin.create 两步完成）
          ent[id] = {
            status: 'active',
            expire_at: j.days ? new Date(Date.now() + Number(j.days) * 864e5).toISOString() : null,
            note: '',
          };
          return out({ ok: true, user_id: id });
        }
        if (action === 'admin.annList') return out({ ok: true, items: anns });
        if (action === 'admin.annUpdate') {
          const a = anns.find((x) => x.id === Number(j.id));
          if (!a) return out({ ok: false, error: '公告不存在' });
          if (j.active !== undefined) a.active = !!j.active;
          if (j.level !== undefined) a.level = j.level;
          if (j.title !== undefined) a.title = j.title;
          if (j.body !== undefined) a.body = j.body;
          return out({ ok: true, id: a.id });
        }
        if (action === 'admin.announce') {
          if (j.del) { const i = anns.findIndex((a) => a.id === j.del); if (i >= 0) anns.splice(i, 1); }
          else if (j.title !== undefined || j.body !== undefined) {
            anns.unshift({ id: Date.now(), title: j.title || '', body: j.body || '',
              level: j.level || 'info', active: true });
          } else return out({ ok: false, error: '缺少公告内容' });
          return out({ ok: true });
        }
      }
      res.status(404);
      out({ ok: false, error: 'unknown action' });
    });
  }).listen(PORT, () => {
    console.log(`MOCK api on http://127.0.0.1:${PORT}/`);
    console.log(`  管理平台  http://127.0.0.1:${PORT}/   管理密钥 ${ADMIN}`);
    console.log(`  客户端    账号 demo / demo123   后端地址 http://127.0.0.1:${PORT}`);
  });
}
