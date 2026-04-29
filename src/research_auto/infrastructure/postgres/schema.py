EXTENSION_SQL = """
create extension if not exists pgcrypto;
"""

CATALOG_SQL = """
create table if not exists conferences (
    id uuid primary key default gen_random_uuid(),
    slug text not null unique,
    name text not null,
    year integer not null,
    homepage_url text,
    source_system text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists tracks (
    id uuid primary key default gen_random_uuid(),
    conference_id uuid not null references conferences(id) on delete cascade,
    slug text not null,
    name text not null,
    track_url text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (conference_id, slug)
);

create table if not exists crawl_runs (
    id uuid primary key default gen_random_uuid(),
    conference_id uuid references conferences(id) on delete set null,
    track_id uuid references tracks(id) on delete set null,
    seed_url text not null,
    status text not null,
    started_at timestamptz,
    finished_at timestamptz,
    error_message text,
    created_at timestamptz not null default now()
);
"""

PAPER_SQL = """
create table if not exists papers (
    id uuid primary key default gen_random_uuid(),
    conference_id uuid not null references conferences(id) on delete cascade,
    track_id uuid references tracks(id) on delete set null,
    source_paper_key text,
    canonical_title text not null,
    title_normalized text not null,
    abstract text,
    year integer not null,
    paper_type text,
    session_name text,
    detail_url text,
    canonical_url text,
    best_pdf_url text,
    best_landing_url text,
    doi text,
    arxiv_id text,
    openreview_id text,
    source_confidence numeric(4,3),
    starred boolean not null default false,
    resolution_status text not null default 'pending',
    status text not null default 'discovered',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

alter table papers add column if not exists best_pdf_url text;
alter table papers add column if not exists best_landing_url text;
alter table papers add column if not exists starred boolean not null default false;
alter table papers add column if not exists resolution_status text not null default 'pending';

create unique index if not exists papers_track_title_udx
    on papers (conference_id, track_id, title_normalized);

create index if not exists papers_doi_idx on papers (doi);
create index if not exists papers_arxiv_id_idx on papers (arxiv_id);

create table if not exists paper_authors (
    id uuid primary key default gen_random_uuid(),
    paper_id uuid not null references papers(id) on delete cascade,
    author_order integer not null,
    display_name text not null,
    affiliation text,
    orcid text,
    unique (paper_id, author_order)
);
"""

JOB_SQL = """
create table if not exists jobs (
    id uuid primary key default gen_random_uuid(),
    job_type text not null,
    payload jsonb not null,
    dedupe_key text,
    status text not null default 'pending',
    priority integer not null default 100,
    attempt_count integer not null default 0,
    max_attempts integer not null default 5,
    available_at timestamptz not null default now(),
    locked_at timestamptz,
    worker_id text,
    last_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists jobs_claim_idx
    on jobs (status, available_at, priority, created_at);

create unique index if not exists jobs_active_dedupe_udx
    on jobs (dedupe_key)
    where dedupe_key is not null and status in ('pending', 'running');

create table if not exists job_attempts (
    id uuid primary key default gen_random_uuid(),
    job_id uuid not null references jobs(id) on delete cascade,
    worker_id text,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    success boolean,
    error_message text,
    log_excerpt text
);

create table if not exists worker_queue_state (
    queue_name text primary key,
    last_started_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists page_snapshots (
    id uuid primary key default gen_random_uuid(),
    crawl_run_id uuid references crawl_runs(id) on delete cascade,
    url text not null,
    body text not null,
    checksum_sha256 text not null,
    captured_at timestamptz not null default now()
);
"""

ARTIFACT_PARSE_SQL = """
create table if not exists artifacts (
    id uuid primary key default gen_random_uuid(),
    paper_id uuid not null references papers(id) on delete cascade,
    artifact_kind text not null,
    label text,
    resolution_reason text,
    source_url text not null,
    resolved_url text,
    mime_type text,
    downloadable boolean not null default false,
    download_status text not null default 'pending',
    local_path text,
    storage_uri text,
    storage_key text,
    checksum_sha256 text,
    byte_size bigint,
    downloaded_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (paper_id, artifact_kind, source_url)
);

create index if not exists artifacts_paper_idx on artifacts (paper_id);

alter table artifacts add column if not exists resolution_reason text;
alter table artifacts add column if not exists storage_uri text;
alter table artifacts add column if not exists storage_key text;

create table if not exists paper_parses (
    id uuid primary key default gen_random_uuid(),
    paper_id uuid not null references papers(id) on delete cascade,
    artifact_id uuid not null references artifacts(id) on delete cascade,
    parser_version text not null,
    parse_status text not null default 'succeeded',
    source_text text not null,
    full_text text not null,
    abstract_text text,
    page_count integer,
    content_hash text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (artifact_id, parser_version, content_hash)
);

alter table paper_parses add column if not exists source_text text;
alter table paper_parses alter column source_text set default '';
update paper_parses set source_text = full_text where source_text is null;
alter table paper_parses alter column source_text set not null;
alter table paper_parses alter column source_text drop default;

create table if not exists paper_chunks (
    id uuid primary key default gen_random_uuid(),
    paper_parse_id uuid not null references paper_parses(id) on delete cascade,
    paper_id uuid not null references papers(id) on delete cascade,
    section_name text,
    chunk_index integer not null,
    token_count integer,
    content text not null,
    created_at timestamptz not null default now(),
    unique (paper_parse_id, chunk_index)
);

create index if not exists paper_chunks_paper_idx on paper_chunks (paper_id);

create table if not exists paper_summaries (
    id uuid primary key default gen_random_uuid(),
    paper_id uuid not null references papers(id) on delete cascade,
    paper_parse_id uuid not null references paper_parses(id) on delete cascade,
    provider text not null,
    model_name text not null,
    prompt_version text not null,
    problem text,
    research_question text,
    research_question_zh text,
    method text,
    evaluation text,
    results text,
    conclusions text,
    conclusions_zh text,
    future_work text,
    future_work_zh text,
    takeaway text,
    summary_short text,
    summary_long text,
    summary_short_zh text,
    summary_long_zh text,
    contributions jsonb not null default '[]'::jsonb,
    limitations jsonb not null default '[]'::jsonb,
    tags jsonb not null default '[]'::jsonb,
    raw_response jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (paper_parse_id, provider, model_name, prompt_version)
);

create table if not exists arxiv_query_cache (
    query_key text primary key,
    search_query text not null,
    response_body text not null,
    fetched_at timestamptz not null default now(),
    expires_at timestamptz not null
);

create index if not exists arxiv_query_cache_expires_idx on arxiv_query_cache (expires_at);

alter table paper_summaries add column if not exists problem text;
alter table paper_summaries add column if not exists research_question text;
alter table paper_summaries add column if not exists research_question_zh text;
alter table paper_summaries add column if not exists method text;
alter table paper_summaries add column if not exists evaluation text;
alter table paper_summaries add column if not exists results text;
alter table paper_summaries add column if not exists conclusions text;
alter table paper_summaries add column if not exists conclusions_zh text;
alter table paper_summaries add column if not exists future_work text;
alter table paper_summaries add column if not exists future_work_zh text;
alter table paper_summaries add column if not exists takeaway text;
alter table paper_summaries add column if not exists summary_short_zh text;
alter table paper_summaries add column if not exists summary_long_zh text;
"""

TRIGGER_SQL = """
create or replace function set_updated_at() returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists conferences_set_updated_at on conferences;
create trigger conferences_set_updated_at before update on conferences for each row execute function set_updated_at();

drop trigger if exists tracks_set_updated_at on tracks;
create trigger tracks_set_updated_at before update on tracks for each row execute function set_updated_at();

drop trigger if exists papers_set_updated_at on papers;
create trigger papers_set_updated_at before update on papers for each row execute function set_updated_at();

drop trigger if exists jobs_set_updated_at on jobs;
create trigger jobs_set_updated_at before update on jobs for each row execute function set_updated_at();

drop trigger if exists worker_queue_state_set_updated_at on worker_queue_state;
create trigger worker_queue_state_set_updated_at before update on worker_queue_state for each row execute function set_updated_at();

drop trigger if exists artifacts_set_updated_at on artifacts;
create trigger artifacts_set_updated_at before update on artifacts for each row execute function set_updated_at();

drop trigger if exists paper_parses_set_updated_at on paper_parses;
create trigger paper_parses_set_updated_at before update on paper_parses for each row execute function set_updated_at();

drop trigger if exists paper_summaries_set_updated_at on paper_summaries;
create trigger paper_summaries_set_updated_at before update on paper_summaries for each row execute function set_updated_at();
"""

SCHEMA_SQL = "\n\n".join(
    [
        EXTENSION_SQL,
        CATALOG_SQL,
        PAPER_SQL,
        JOB_SQL,
        ARTIFACT_PARSE_SQL,
        TRIGGER_SQL,
    ]
)
