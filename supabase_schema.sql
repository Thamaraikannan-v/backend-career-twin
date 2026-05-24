-- ============================================================
-- Career Twin — Supabase Schema
-- Run this entire file in the Supabase SQL Editor.
-- ============================================================

-- ── Subscriptions table ──────────────────────────────────────
create table if not exists public.subscriptions (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid references auth.users(id) on delete cascade not null unique,
  stripe_customer  text,
  stripe_sub_id    text,
  status           text not null default 'free'
                     check (status in ('free', 'pro', 'cancelled')),
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

alter table public.subscriptions enable row level security;
create policy "users_own_subscription"
  on public.subscriptions for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- ── Analyses table ───────────────────────────────────────────
create table if not exists public.analyses (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid references auth.users(id) on delete cascade not null,
  status               text not null default 'running'
                         check (status in ('running', 'done', 'failed')),
  company_name         text,
  jd_text              text,
  resume_url           text,

  candidate_profile    jsonb,
  jd_signals           jsonb,
  company_intel        jsonb,
  recruiter_simulation jsonb,
  resume_rewrite       jsonb,
  keyword_analysis     jsonb,
  baseline_score       float,

  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

-- Auto-update updated_at on any row change
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger analyses_updated_at
  before update on public.analyses
  for each row execute function public.set_updated_at();

create trigger subscriptions_updated_at
  before update on public.subscriptions
  for each row execute function public.set_updated_at();

-- ── Row-Level Security ───────────────────────────────────────
alter table public.analyses enable row level security;
create policy "users_own_analyses"
  on public.analyses for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- ── Indexes ──────────────────────────────────────────────────
create index if not exists analyses_user_id_idx   on public.analyses(user_id);
create index if not exists analyses_status_idx    on public.analyses(status);
create index if not exists analyses_created_idx   on public.analyses(created_at desc);

-- ── Storage bucket for resumes ───────────────────────────────
insert into storage.buckets (id, name, public)
  values ('resumes', 'resumes', false)
  on conflict do nothing;

create policy "resume_owner_read"
  on storage.objects for select
  using (
    bucket_id = 'resumes'
    and auth.uid()::text = (storage.foldername(name))[1]
  );

create policy "resume_owner_insert"
  on storage.objects for insert
  with check (
    bucket_id = 'resumes'
    and auth.uid()::text = (storage.foldername(name))[1]
  );
