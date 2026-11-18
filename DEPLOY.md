# 部署需要的东西（按顺序做，每步都有验证）

> 目标：`https://<你的域名>/` = 管理平台，`https://<你的域名>/api` = 客户端后端地址

---

## 一、你需要提供的 3 类凭据

| # | 东西 | 从哪拿 | 给到哪 |
|---|---|---|---|
| 1 | **Vercel 完整令牌**（`vercel_xxx` 或 account token） | vercel.com/account/tokens → Create | 给我，或你自己 CLI 登录 |
| 2 | **Supabase** `Project URL` | Supabase 项目 → Settings → API | 第 3 步配到 Vercel |
| 3 | **Supabase** `anon` + `service_role` | 同上 | 第 3 步配到 Vercel |

> 你给的 `vck_...` 是**集成令牌**，实测只能读取（`/v2/user`、`/v9/projects` 可读，
> 创建项目、部署、写环境变量全部 403）。部署必须换**完整账户令牌**。

---

## 二、步骤（两种走法，选一种）

### 走法 A：我全程 CLI 部署（推荐，快）

**A1. 你建 Supabase 项目**
1. 打开 https://supabase.com/dashboard
2. New project → 填名字、数据库密码、区域选 Singapore（离国内近）
3. 等 2 分钟初始化完成
4. Settings → API，抄下：
   - `Project URL`（形如 `https://abcdefgh.supabase.co`）
   - `anon public`
   - `service_role`（点 Reveal）

**A2. 执行建表 SQL**
- Supabase → SQL Editor → New query
- 把 `backend/supabase/schema.sql` 整段粘进去 → Run
- 验证：左侧 Table Editor 应出现 `profiles` / `entitlements` / `announcements` / `order_logs`

**A3. 给我完整 Vercel 令牌**
- https://vercel.com/account/tokens → Create Token
- Scope 选 **Full Account**，Expiration 选 1 天（部署完就失效）
- 把 `vercel_xxxxx` 发我

**A4. 我执行部署**
```powershell
vercel --token $TOKEN --yes --prod
vercel env add SUPABASE_URL              # 值 = Project URL
vercel env add SUPABASE_ANON_KEY
vercel env add SUPABASE_SERVICE_ROLE_KEY
vercel env add ADMIN_KEY                 # 我生成一个强随机串
vercel env add MAX_ORDERS                # 3
vercel --token $TOKEN --prod             # 重新部署让环境变量生效
```
**A5. 我验证**
- `GET https://<域名>/api` → `{"ok":true,...}`
- `POST /api action=admin.ping` + `x-admin-key` → `{"ok":true}`
- 建一个测试账号 → 客户端登录成功

---

### 走法 B：你在网页操作（不需要给我令牌）

**B1~B2 同 A1、A2**

**B3. Vercel 建项目**
1. https://vercel.com/new → 选 "Deploy from Git" 或直接拖拽上传
2. 更简单：把 `backend/` 目录打成 zip，用 `vercel` 网页的 "Upload" 方式
3. Framework 选 **Other**，Root Directory 选 `backend`

**B4. 配环境变量**（Vercel 项目 → Settings → Environment Variables）

| Key | Value |
|---|---|
| `SUPABASE_URL` | 你的 Project URL |
| `SUPABASE_ANON_KEY` | anon public |
| `SUPABASE_SERVICE_ROLE_KEY` | service_role |
| `ADMIN_KEY` | 自己定，如 `ag-9f3c7d2e` |
| `MAX_ORDERS` | `3` |

**B5. Redeploy**，然后打开 `https://<域名>/` 看管理平台是否正常

**B6. 把域名告诉我**，我改客户端地址并打包

---

## 三、域名拿到之后的收尾（我做）

1. 改 `client_v33/app/endpoint.pyc` 的 `API_BASE` → 你的 Vercel 域名
2. 跑全量自检（12 项）
3. PyInstaller 打包成单文件 exe
4. 验证 exe 能启动、能连后端登录
5. 输出到 `dist/抢单助手-v3.3.exe`

---

## 四、打包参数（已定）

```
--onefile --noconsole
--hidden-import pystray --hidden-import pystray._win32
--hidden-import rapidocr_onnxruntime
--collect-all rapidocr_onnxruntime
--collect-all onnxruntime
--name 抢单助手
```

> 注意：`rapidocr_onnxruntime` 的模型文件必须整包收进去，
> 否则打包后点"开始抢单"会报错（README 里提到的 selftest 就是为了查这个）。
