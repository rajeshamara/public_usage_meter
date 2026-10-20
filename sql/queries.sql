-- Open in pgAdmin on usage_meter, or run: database.bat query sql\queries.sql
SELECT current_database(), current_user, inet_server_addr(), inet_server_port();
SELECT id, observed_at, used AS percent_used, 100-used AS percent_remaining, resets_at
FROM public.samples ORDER BY observed_at DESC LIMIT 30;
-- Percentage changes, with the observation times bracketing each change.
WITH changes AS (
 SELECT observed_at, used, resets_at,
 lag(observed_at) OVER (PARTITION BY resets ORDER BY timestamp,id) AS previous_observation,
 lag(used) OVER (PARTITION BY resets ORDER BY timestamp,id) AS previous_used
 FROM public.samples
) SELECT *, used-previous_used AS percentage_points FROM changes
WHERE used IS DISTINCT FROM previous_used ORDER BY observed_at DESC LIMIT 30;
-- Daily token history from the latest account response (service dates unchanged).
SELECT bucket->>'startDate' AS usage_date, (bucket->>'tokens')::bigint AS tokens
FROM (SELECT payload FROM public.snapshots WHERE kind='activity' ORDER BY timestamp DESC LIMIT 1) latest,
LATERAL jsonb_array_elements(coalesce(payload->'dailyUsageBuckets','[]'::jsonb)) bucket
ORDER BY usage_date DESC LIMIT 14;
