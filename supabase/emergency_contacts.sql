-- Existing tables must have compatible fields. This does not migrate existing tables.
create table if not exists public.emergency_contacts (
    id text primary key default gen_random_uuid()::text,
    user_id text not null,
    name text not null,
    phone text not null,
    relationship text,
    priority integer not null default 1
);
create index if not exists emergency_contacts_user_priority_idx
    on public.emergency_contacts (user_id, priority, id);
alter table public.emergency_contacts enable row level security;
-- Backend secret/service-role access only. No public read policies are created.
