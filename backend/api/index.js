// Vercel Serverless: 账号资格 / 公告 / 每日抢单额度 / 管理端
// 环境变量：SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_ANON_KEY, ADMIN_KEY

const SUPA = (process.env.SUPABASE_URL || '').replace(/\/$/, '');
const SERVICE = process.env.SUPABASE_SERVICE_ROLE_KEY || '';
const ANON = process.env.SUPABASE_ANON_KEY || '';
const ADMIN = process.env.ADMIN_KEY || '';

const MAX_ORDERS = Number(process.env.MAX_ORDERS || 3);

function cors(res, req) {
  const origin = req.headers.origin || '*';
  res.setHeader('Access-Control-Allow-Origin', origin);
  res.setHeader('Vary', 'Origin');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Admin-Key');
}

function json(res, code, body) {
  res.status(code).setHeader('Content-Type', 'application/json; charset=utf-8');
  res.setHeader('Cache-Control', 'no-store');
  res.end(JSON.stringify(body));
}

async function sb(path, opts = {}) {
  const r = await fetch(`${SUPA}${path}`, {
    ...opts,
    headers: {
      apikey: SERVICE,
      Authorization: `Bearer ${SERVICE}`,
      'Content-Type': 'application/json',
      ...(opts.headers || {}),
    },
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`supabase ${r.status}: ${text.slice(0, 300)}`);
  return {
    data: text ? JSON.parse(text) : null,
    headers: r.headers,
  };
}

// 以北京时间为准算「今天」，避免 UTC 16:00 后跨天
function todayCN() {
  return new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);
}

function emailOf(username) {
  const u = String(username || '').trim();
  if (!u) throw new Error('账号不能为空');
  return u.includes('@') ? u : `${u}@app.local`;
}

async function authUser(req) {
  const h = req.headers.authorization || '';
  const token = h.startsWith('Bearer ') ? h.slice(7) : '';
  if (!token) throw Object.assign(new Error('未登录'), { code: 401 });
  const r = await fetch(`${SUPA}/auth/v1/user`, {
    headers: { apikey: ANON, Authorization: `Bearer ${token}` },
  });
  if (!r.ok) throw Object.assign(new Error('登录已失效'), { code: 401 });
  return { user: await r.json(), token };
}

async function todayCount(userId) {
  const today = todayCN();
  const { headers } = await sb(
    `/rest/v1/order_logs?select=id&user_id=eq.${userId}&day=eq.${today}`,
    { headers: { Prefer: 'count=exact', Range: '0-0' } },
  );
  const cr = headers.get('content-range') || '0-0/0';
  return Number(cr.split('/')[1] || 0);
}

async function entitlement(userId) {
  const { data } = await sb(
    `/rest/v1/entitlements?select=status,expire_at,note&user_id=eq.${userId}`,
  );
  const row = (data || [])[0];
  if (!row) return { allowed: false, reason: '账号未授权，请联系管理员', permanent: false };
  const now = Date.now();
  const expired = row.expire_at ? new Date(row.expire_at).getTime() < now : false;
  const allowed = row.status === 'active' && !expired;
  return {
    allowed,
    reason: allowed ? '' : expired ? '使用期限已到期' : '使用资格已被停止',
    permanent: !row.expire_at,
    expire_at: row.expire_at,
    status: row.status,
  };
}

async function announcements() {
  try {
    const { data } = await sb(
      '/rest/v1/announcements?select=id,title,body,level,active&active=eq.true&order=id.desc&limit=5',
    );
    return data || [];
  } catch {
    return [];
  }
}

async function bundle(userId) {
  const [ent, ann, used] = await Promise.all([entitlement(userId), announcements(), todayCount(userId)]);
  return { ent, ann, quota: { max: MAX_ORDERS, used } };
}

// ---------------------------------------------------------------- 业务
async function doLogin(req, body) {
  const email = emailOf(body.username);
  const r = await fetch(`${SUPA}/auth/v1/token?grant_type=password`, {
    method: 'POST',
    headers: { apikey: ANON, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password: String(body.password || '') }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    throw Object.assign(new Error(j.error_description || j.msg || '账号或密码错误'), { code: 401 });
  }
  const b = await bundle(j.user.id);
  if (!b.ent.allowed) return { ok: false, error: b.ent.reason || '无使用资格' };
  return { ok: true, token: j.access_token, refresh: j.refresh_token, user_id: j.user.id, ...b };
}

async function doOrder(userId, body) {
  const today = todayCN();
  const used = await todayCount(userId);
  if (used >= MAX_ORDERS) return { ok: false, error: '今日额度已用完' };
  await sb('/rest/v1/order_logs', {
    method: 'POST',
    body: JSON.stringify({
      user_id: userId,
      day: today,
      code: String(body.code || '').slice(0, 16),
      target: String(body.target || '').slice(0, 120),
    }),
  });
  return { ok: true, quota: { max: MAX_ORDERS, used: used + 1 } };
}

// ---------------------------------------------------------------- 管理端
function requireAdmin(req) {
  if (!ADMIN) throw Object.assign(new Error('服务端未配置 ADMIN_KEY'), { code: 500 });
  if ((req.headers['x-admin-key'] || '') !== ADMIN) {
    throw Object.assign(new Error('管理员密钥错误'), { code: 401 });
  }
}

async function adminList() {
  const [profs, ents, logs] = await Promise.all([
    sb('/rest/v1/profiles?select=id,username,created_at&order=created_at.desc&limit=500'),
    sb('/rest/v1/entitlements?select=user_id,status,expire_at,note&limit=1000'),
    sb(`/rest/v1/order_logs?select=user_id&day=eq.${todayCN()}&limit=5000`),
  ]);
  const emap = new Map((ents.data || []).map((e) => [e.user_id, e]));
  const counts = {};
  for (const l of logs.data || []) counts[l.user_id] = (counts[l.user_id] || 0) + 1;
  return {
    ok: true,
    users: (profs.data || []).map((p) => ({
      ...p,
      ent: emap.get(p.id) || null,
      today: counts[p.id] || 0,
    })),
  };
}

async function adminCreate(body) {
  const email = emailOf(body.username);
  const r = await fetch(`${SUPA}/auth/v1/admin/users`, {
    method: 'POST',
    headers: { apikey: SERVICE, Authorization: `Bearer ${SERVICE}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password: String(body.password || ''), email_confirm: true }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.msg || j.error_description || '创建账号失败');
  await sb(`/rest/v1/profiles?id=eq.${j.id}`, {
    method: 'PATCH',
    headers: { Prefer: 'return=minimal' },
    body: JSON.stringify({ username: String(body.username) }),
  });
  await sb(`/rest/v1/entitlements?on_conflict=user_id`, {
    method: 'POST',
    headers: { Prefer: 'resolution=merge-duplicates,return=minimal' },
    body: JSON.stringify({
      user_id: j.id,
      status: 'active',
      expire_at: body.days ? new Date(Date.now() + Number(body.days) * 864e5).toISOString() : null,
      note: body.note || '',
      updated_at: new Date().toISOString(),
    }),
  });
  return { ok: true, user_id: j.id };
}

async function adminGrant(body) {
  const days = body.days === undefined || body.days === null || body.days === '' ? null : Number(body.days);
  const expire = days ? new Date(Date.now() + days * 864e5).toISOString() : null;
  await sb(`/rest/v1/entitlements?on_conflict=user_id`, {
    method: 'POST',
    headers: { Prefer: 'resolution=merge-duplicates,return=representation' },
    body: JSON.stringify({
      user_id: body.user_id,
      status: 'active',
      expire_at: expire,
      note: body.note || '',
      updated_at: new Date().toISOString(),
    }),
  });
  return { ok: true, expire_at: expire };
}

async function adminDelete(body) {
  const userId = String(body.user_id || '').trim();
  if (!userId) throw new Error('缺少 user_id');
  const r = await fetch(`${SUPA}/auth/v1/admin/users/${encodeURIComponent(userId)}`, {
    method: 'DELETE',
    headers: { apikey: SERVICE, Authorization: `Bearer ${SERVICE}` },
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`删除账号失败: ${text.slice(0, 300)}`);
  return { ok: true, user_id: userId };
}

async function adminResetPassword(body) {
  const userId = String(body.user_id || '').trim();
  const password = String(body.password || '');
  if (!userId || !password) throw new Error('账号和新密码都要填');
  if (password.length < 6) throw new Error('密码至少 6 位');
  const r = await fetch(`${SUPA}/auth/v1/admin/users/${encodeURIComponent(userId)}`, {
    method: 'PUT',
    headers: {
      apikey: SERVICE,
      Authorization: `Bearer ${SERVICE}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ password }),
  });
  const text = await r.text();
  if (!r.ok) throw new Error(`重置密码失败: ${text.slice(0, 300)}`);
  return { ok: true, user_id: userId };
}

async function adminRevoke(body) {
  await sb(`/rest/v1/entitlements?user_id=eq.${body.user_id}`, {
    method: 'PATCH',
    body: JSON.stringify({ status: 'revoked', updated_at: new Date().toISOString() }),
  });
  return { ok: true };
}

async function adminAnnounce(body) {
  if (body.title === undefined && body.del !== undefined) {
    await sb(`/rest/v1/announcements?id=eq.${body.del}`, { method: 'DELETE' });
    return { ok: true };
  }
  await sb('/rest/v1/announcements', {
    method: 'POST',
    body: JSON.stringify({
      title: String(body.title || '').slice(0, 60),
      body: String(body.body || '').slice(0, 400),
      level: String(body.level || 'info'),
      active: body.active !== false,
    }),
  });
  return { ok: true };
}

// 公告的启用 / 停用 / 改内容（客户端只拉 active=true 的公告，
// 所以"停用"等于立即从所有客户端下线，不需要等客户端更新）
async function adminAnnUpdate(body) {
  const id = Number(body.id);
  if (!Number.isInteger(id) || id <= 0) throw new Error('缺少公告 id');
  const patch = {};
  if (body.active !== undefined) patch.active = !!body.active;
  if (body.level !== undefined) patch.level = String(body.level).slice(0, 16);
  if (body.title !== undefined) patch.title = String(body.title).slice(0, 60);
  if (body.body !== undefined) patch.body = String(body.body).slice(0, 400);
  if (!Object.keys(patch).length) throw new Error('没有要更新的字段');
  await sb(`/rest/v1/announcements?id=eq.${id}`, {
    method: 'PATCH',
    headers: { Prefer: 'return=minimal' },
    body: JSON.stringify(patch),
  });
  return { ok: true, id, patch };
}

async function adminAnnList() {
  const { data } = await sb('/rest/v1/announcements?select=*&order=id.desc&limit=50');
  return { ok: true, items: data || [] };
}

// ---------------------------------------------------------------- 入口
module.exports = async (req, res) => {
  cors(res, req);
  if (req.method === 'OPTIONS') {
    res.status(204).end();
    return;
  }
  if (req.method === 'GET') {
    return json(res, 200, { ok: true, service: 'autograb', time: new Date().toISOString() });
  }
  if (req.method !== 'POST') return json(res, 405, { ok: false, error: 'method not allowed' });

  let body = req.body;
  if (typeof body === 'string') {
    try { body = JSON.parse(body); } catch { body = {}; }
  }
  body = body || {};
  const action = String(body.action || '');

  try {
    if (!SUPA || !SERVICE) throw new Error('服务端缺少 SUPABASE_URL / SERVICE_ROLE_KEY');

    if (action === 'login') return json(res, 200, await doLogin(req, body));

    if (action.startsWith('admin.')) {
      requireAdmin(req);
      switch (action) {
        case 'admin.list': return json(res, 200, await adminList());
        case 'admin.create': return json(res, 200, await adminCreate(body));
        case 'admin.grant': return json(res, 200, await adminGrant(body));
        case 'admin.revoke': return json(res, 200, await adminRevoke(body));
        case 'admin.delete': return json(res, 200, await adminDelete(body));
        case 'admin.resetPassword': return json(res, 200, await adminResetPassword(body));
        case 'admin.ping': return json(res, 200, { ok: true });
        case 'admin.announce': return json(res, 200, await adminAnnounce(body));
        case 'admin.annUpdate': return json(res, 200, await adminAnnUpdate(body));
        case 'admin.annList': return json(res, 200, await adminAnnList());
        default: return json(res, 404, { ok: false, error: 'unknown admin action' });
      }
    }

    const { user } = await authUser(req);

    if (action === 'heartbeat') {
      const b = await bundle(user.id);
      if (!b.ent.allowed) return json(res, 200, { ok: false, error: b.ent.reason, ...b });
      return json(res, 200, { ok: true, ...b });
    }
    if (action === 'order') return json(res, 200, await doOrder(user.id, body));

    return json(res, 404, { ok: false, error: 'unknown action' });
  } catch (e) {
    const code = e.code && Number.isInteger(e.code) && e.code >= 400 ? e.code : 400;
    return json(res, code, { ok: false, error: e.message || '服务端错误' });
  }
};
