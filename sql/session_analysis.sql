-- Read-only bootstrap analysis. Usage dates are New York local dates.
SELECT usage_date,model,effort,host,sum(model_responses) responses,
 sum(input_tokens) input_tokens,sum(cached_input_tokens) cached_input_tokens,
 sum(uncached_input_tokens) uncached_input_tokens,sum(output_tokens) output_tokens,
 sum(reasoning_output_tokens) reasoning_tokens,sum(total_tokens) total_tokens
FROM public.daily_usage GROUP BY 1,2,3,4 ORDER BY 1,2,3;

-- Historical allowance ranges by local day; keep different quota windows separate.
SELECT (observed_at AT TIME ZONE 'America/New_York')::date day,limit_id,window_kind,window_minutes,
 min(used_percent) min_used,max(used_percent) max_used,count(*) observations
FROM public.session_allowance GROUP BY 1,2,3,4 ORDER BY 1,2,3;

-- Most expensive turns in observed raw token volume (not subscription credits).
SELECT host,thread_id,turn_id,model,effort,first_response_at,last_response_at,
 model_responses,input_tokens,cached_input_tokens,output_tokens,reasoning_output_tokens,total_tokens
FROM public.task_usage ORDER BY total_tokens DESC LIMIT 20;

-- UTC versus local date comparison for the dates in question.
SELECT (observed_at AT TIME ZONE 'UTC')::date utc_day,
 (observed_at AT TIME ZONE 'America/New_York')::date local_day,
 count(*) responses,sum(total_tokens) total_tokens
FROM public.token_usage GROUP BY 1,2 ORDER BY 1,2;
