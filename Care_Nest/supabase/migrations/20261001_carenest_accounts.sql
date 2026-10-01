-- CareNest worker and employer tables on Supabase.
-- Apply in the SQL editor (or `supabase db query`) using the service role.
-- Never store secret keys in this file.

create table if not exists public.carenest_employers (
  user_id bigint primary key,
  email text unique not null,
  full_name text not null default '',
  role text not null default 'employer',
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.carenest_workers (
  user_id bigint primary key,
  email text unique not null,
  full_name text not null default '',
  role text not null default 'worker',
  is_active boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.carenest_jobs (
  id bigint primary key,
  employer_user_id bigint references public.carenest_employers(user_id) on delete cascade,
  title text not null,
  job_type text,
  location text,
  pay numeric(12,2),
  currency text default 'KES',
  status text not null default 'DRAFT',
  image_url text,
  start_date date,
  created_at timestamptz not null default now()
);

alter table public.carenest_employers enable row level security;
alter table public.carenest_workers enable row level security;
alter table public.carenest_jobs enable row level security;

drop policy if exists carenest_employers_self_read on public.carenest_employers;
create policy carenest_employers_self_read on public.carenest_employers
  for select using (email = coalesce(auth.jwt()->>'email', ''));
drop policy if exists carenest_workers_self_read on public.carenest_workers;
create policy carenest_workers_self_read on public.carenest_workers
  for select using (email = coalesce(auth.jwt()->>'email', ''));

insert into storage.buckets (id, name, public)
values ('job-images', 'job-images', true)
on conflict (id) do nothing;
