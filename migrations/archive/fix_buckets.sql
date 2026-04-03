-- =========================================================================
-- E-resumehub Storage Bucket Fix
-- Run this in your Supabase SQL Editor
-- =========================================================================

-- 1. Create the 'profile-pictures' bucket for User Photos
insert into storage.buckets (id, name, public)
values ('profile-pictures', 'profile-pictures', true)
on conflict (id) do update set public = true;

-- 2. Create the 'resumes-pdf' bucket for generated PDFs
insert into storage.buckets (id, name, public)
values ('resumes-pdf', 'resumes-pdf', true)
on conflict (id) do update set public = true;

-- =========================================================================
-- Row Level Security (RLS) Policies
-- Ensure users can upload files and everyone can view them
-- =========================================================================

-- Enable RLS on objects (standard safety)
alter table storage.objects enable row level security;

-- -------------------------------------------------------------------------
-- A. PROFILE PICTURES (Public Read, Owner Write)
-- -------------------------------------------------------------------------

-- Allow Public Read
create policy "Public Read Profiles"
on storage.objects for select
using ( bucket_id = 'profile-pictures' );

-- Allow Authenticated Users to Upload (INSERT)
create policy "User Upload Profiles"
on storage.objects for insert
to authenticated
with check ( bucket_id = 'profile-pictures' );

-- Allow Users to Update their own files (UPDATE)
create policy "User Update Profiles"
on storage.objects for update
to authenticated
using ( bucket_id = 'profile-pictures' and auth.uid() = owner );

-- Allow Users to Delete their own files (DELETE)
create policy "User Delete Profiles"
on storage.objects for delete
to authenticated
using ( bucket_id = 'profile-pictures' and auth.uid() = owner );

-- -------------------------------------------------------------------------
-- B. RESUME PDFS (Public Read, Owner Write)
-- -------------------------------------------------------------------------

-- Allow Public Read (for download links)
create policy "Public Read Resumes"
on storage.objects for select
using ( bucket_id = 'resumes-pdf' );

-- Allow Authenticated Users to Upload
create policy "User Upload Resumes"
on storage.objects for insert
to authenticated
with check ( bucket_id = 'resumes-pdf' );

-- Allow Owners to Update
create policy "User Update Resumes"
on storage.objects for update
to authenticated
using ( bucket_id = 'resumes-pdf' and auth.uid() = owner );

-- Allow Owners to Delete
create policy "User Delete Resumes"
on storage.objects for delete
to authenticated
using ( bucket_id = 'resumes-pdf' and auth.uid() = owner );
