-- Selected Codex telemetry only. Prompts, reasoning text, and tool arguments are excluded.
DO $$ BEGIN IF current_database()<>'usage_meter' THEN RAISE EXCEPTION 'Wrong database'; END IF; END $$;
CREATE TABLE IF NOT EXISTS public.codex_sessions (
 host TEXT NOT NULL, thread_id TEXT NOT NULL, session_id TEXT, parent_thread_id TEXT,
 started_at TIMESTAMPTZ, cwd TEXT, source JSONB, cli_version TEXT,
 PRIMARY KEY(host,thread_id)
);
CREATE TABLE IF NOT EXISTS public.codex_turns (
 host TEXT NOT NULL, thread_id TEXT NOT NULL, turn_id TEXT NOT NULL,
 root_turn_id TEXT, model TEXT, effort TEXT, service_tier TEXT,
 started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, duration_ms BIGINT,
 PRIMARY KEY(host,thread_id,turn_id)
);
CREATE TABLE IF NOT EXISTS public.token_usage (
 event_key TEXT PRIMARY KEY, response_id TEXT, observed_at TIMESTAMPTZ NOT NULL,
 host TEXT NOT NULL, thread_id TEXT NOT NULL, session_id TEXT, turn_id TEXT, root_turn_id TEXT,
 model TEXT, effort TEXT, service_tier TEXT,
 input_tokens BIGINT, cached_input_tokens BIGINT, cache_write_input_tokens BIGINT,
 output_tokens BIGINT, reasoning_output_tokens BIGINT, total_tokens BIGINT,
 usage JSONB NOT NULL, source_file TEXT NOT NULL, source_offset BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS token_usage_time ON public.token_usage(observed_at);
CREATE INDEX IF NOT EXISTS token_usage_turn ON public.token_usage(host,thread_id,turn_id);
CREATE TABLE IF NOT EXISTS public.session_allowance (
 event_key TEXT PRIMARY KEY, observed_at TIMESTAMPTZ NOT NULL,
 host TEXT NOT NULL, thread_id TEXT NOT NULL, turn_id TEXT,
 limit_id TEXT, window_kind TEXT NOT NULL, used_percent DOUBLE PRECISION,
 window_minutes INTEGER, resets_at TIMESTAMPTZ, plan_type TEXT,
 source_file TEXT NOT NULL, source_offset BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS public.session_import_files (
 host TEXT NOT NULL, source_file TEXT NOT NULL, range_start TIMESTAMPTZ NOT NULL,
 range_end TIMESTAMPTZ NOT NULL, byte_offset BIGINT NOT NULL, context JSONB NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(host,source_file,range_start,range_end)
);
CREATE OR REPLACE VIEW public.daily_usage AS
 SELECT (observed_at AT TIME ZONE 'America/New_York')::date AS usage_date,
 model,effort,service_tier,host,count(*) AS model_responses,
 sum(input_tokens) AS input_tokens,sum(cached_input_tokens) AS cached_input_tokens,
 sum(input_tokens-cached_input_tokens) AS uncached_input_tokens,
 sum(cache_write_input_tokens) AS cache_write_input_tokens,
 sum(output_tokens) AS output_tokens,sum(reasoning_output_tokens) AS reasoning_output_tokens,
 sum(total_tokens) AS total_tokens
 FROM public.token_usage GROUP BY 1,2,3,4,5;
CREATE OR REPLACE VIEW public.task_usage AS
 SELECT u.host,u.thread_id,u.turn_id,u.root_turn_id,u.model,u.effort,
 min(u.observed_at) AS first_response_at,max(u.observed_at) AS last_response_at,
 count(*) AS model_responses,sum(u.input_tokens) AS input_tokens,
 sum(u.cached_input_tokens) AS cached_input_tokens,sum(u.output_tokens) AS output_tokens,
 sum(u.reasoning_output_tokens) AS reasoning_output_tokens,sum(u.total_tokens) AS total_tokens
 FROM public.token_usage u GROUP BY 1,2,3,4,5,6;
