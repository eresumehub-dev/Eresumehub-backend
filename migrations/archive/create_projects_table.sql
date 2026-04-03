-- Create Projects table
create table if not exists public.projects (
  id uuid default gen_random_uuid() primary key,
  profile_id uuid references public.user_profiles(id) on delete cascade not null,
  title text not null,
  role text,
  link text,
  description text,
  technologies text[],
  start_date date,
  end_date date,
  is_current boolean default false,
  display_order integer default 0,
  created_at timestamp with time zone default timezone('utc'::text, now()) not null
);

-- Enable Row Level Security
alter table public.projects enable row level security;

-- Policies
-- 1. View: Users can view projects belonging to their profile
create policy "Users can view own projects"
  on public.projects for select
  using ( 
    auth.uid() = (select user_id from public.user_profiles where id = public.projects.profile_id) 
  );

-- 2. Insert: Users can insert projects into their profile
create policy "Users can insert own projects"
  on public.projects for insert
  with check ( 
    auth.uid() = (select user_id from public.user_profiles where id = public.projects.profile_id)
  );

-- 3. Update: Users can update their own projects
create policy "Users can update own projects"
  on public.projects for update
  using ( 
    auth.uid() = (select user_id from public.user_profiles where id = public.projects.profile_id)
  );

-- 4. Delete: Users can delete their own projects
create policy "Users can delete own projects"
  on public.projects for delete
  using ( 
    auth.uid() = (select user_id from public.user_profiles where id = public.projects.profile_id)
  );
