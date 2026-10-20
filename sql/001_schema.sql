-- Usage Meter account observations only; no prompts or credentials.
-- Additive schema: never drops existing tables. Run only against usage_meter.
DO $$ BEGIN
 IF current_database() <> 'usage_meter' THEN RAISE EXCEPTION 'Wrong database'; END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.snapshots (
 id BIGSERIAL PRIMARY KEY,
 source_key TEXT NOT NULL UNIQUE, -- Stable import/event identity; prevents replay duplicates.
 timestamp DOUBLE PRECISION NOT NULL, -- Original Unix epoch, preserved exactly from SQLite.
 observed_at TIMESTAMPTZ NOT NULL, -- Human-readable observation time for pgAdmin.
 kind TEXT NOT NULL CHECK(kind IN ('limits','activity')),
 payload JSONB NOT NULL, -- Raw account response; not a model-generated estimate.
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS snapshots_kind_time ON public.snapshots(kind, timestamp DESC);
CREATE TABLE IF NOT EXISTS public.samples (
 id BIGSERIAL PRIMARY KEY,
 source_key TEXT NOT NULL UNIQUE,
 timestamp DOUBLE PRECISION NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL,
 used DOUBLE PRECISION NOT NULL CHECK(used >= 0 AND used <= 100), -- Percentage points used.
 resets DOUBLE PRECISION NOT NULL, -- Original reset epoch.
 resets_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS samples_reset_time ON public.samples(resets, timestamp);
