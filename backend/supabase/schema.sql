-- ============================================================
-- 抢单助手 后端表结构（在 Supabase → SQL Editor 里整段执行）
-- ============================================================

-- 1. 用户档案
create table if not exists public.profiles (
  id         uuid primary key references auth.users (id) on delete cascade,
  username   text unique not null,
  created_at timestamptz not null default now()
);

-- 2. 使用资格（没有记录 = 不允许使用）
create table if not exists public.entitlements (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  status     text not null default 'active' check (status in ('active', 'revoked')),
  expire_at  timestamptz,                       -- null = 永久
  note       text default '',
  updated_at timestamptz not null default now()
);

-- 3. 公告
create table if not exists public.announcements (
  id         bigserial primary key,
  title      text not null default '',
  body       text not null default '',
  level      text not null default 'info',      -- info | warn | danger
  active     boolean not null default true,
  created_at timestamptz not null default now()
);

-- 4. 抢单流水（每日 3 单额度以它为唯一依据）

create table if not exists public.order_logs (
  id         bigserial primary key,
  user_id    uuid not null references auth.users (id) on delete cascade,
  day        date not null default ((now() at time zone 'Asia/Shanghai')::date),
  code       text default '',
  target     text default '',
  created_at timestamptz not null default now()
);
create index if not exists order_logs_user_day_idx on public.order_logs (user_id, day);

-- 5. 在 Supabase 后台手工建号时也能自动建档

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer as $$
begin
  insert into public.profiles (id, username)
  values (new.id, coalesce(split_part(new.email, '@', 1), new.id::text))
  on conflict (id) do nothing;
  insert into public.entitlements (user_id, status, expire_at)
  values (new.id, 'active', null)
  on conflict (user_id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- 6. RLS 全开、不给任何 anon/authenticated 策略
--    → 客户端拿不到数据库，只能走 Vercel 接口（service_role 绕过 RLS）

alter table public.profiles      enable row level security;
alter table public.entitlements  enable row level security;
alter table public.announcements enable row level security;
alter table public.order_logs    enable row level security;

-- 7. 常用查询：今天谁抢了几单
create or replace view public.v_today_orders as
select p.username, count(o.id) as orders
from public.profiles p
left join public.order_logs o
  on o.user_id = p.id
 and o.day = (now() at time zone 'Asia/Shanghai')::date
group by p.username
order by orders desc;
