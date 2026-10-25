-- Apply in the Supabase SQL editor. Existing calls tables must have compatible columns.
-- user_id is text to support the application identifiers supplied by this project.
create table if not exists public.calls (
    id uuid primary key,
    user_id text not null,
    scheduled_for timestamptz,
    status text not null default 'in_progress',
    started_at timestamptz,
    ended_at timestamptz,
    summary text not null default '',
    audio_path text not null default '',
    transcript text not null default ''
);
alter table public.calls enable row level security;
-- Backend secret/service_role key only. No public access policies are installed.
