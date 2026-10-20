"""Independently verify each DB token row against its exact JSONL source offset."""
import json
from pathlib import Path
from pg_storage import connect

with connect() as c:
 rows=c.execute("SELECT source_file,source_offset,response_id,input_tokens,cached_input_tokens,cache_write_input_tokens,output_tokens,reasoning_output_tokens,total_tokens FROM token_usage ORDER BY source_file,source_offset").fetchall()
fields=('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')
current=None;handle=None;verified=0
try:
 for path,offset,response,*values in rows:
  if path!=current:
   if handle:handle.close()
   handle=Path(path).open('rb');current=path
  handle.seek(offset);record=json.loads(handle.readline());p=record['payload']
  if record['type']!='token_usage_record' or p.get('response_id')!=response or [p['usage'].get(k) for k in fields]!=values:
   raise RuntimeError('Source verification mismatch at '+path+':'+str(offset))
  verified+=1
finally:
 if handle:handle.close()
print('Verified exact source identity and all six token fields for',verified,'responses.')
