"""Incremental read-only JSONL ingestion into usage_meter PostgreSQL.
Dates are inclusive America/New_York dates; scan all session directories so older
sessions resumed in the period are included. Per-response records alone count as
consumption. token_count is used ONLY for allowance snapshots, never added again.
Checkpoints commit with imported rows. Incomplete trailing lines remain unread.
No prompt, reasoning body, tool argument, or credential content is persisted.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import socket
from zoneinfo import ZoneInfo
from pg_storage import connect, ROOT
from psycopg.types.json import Jsonb

KINDS={b'session_meta',b'turn_context',b'token_usage_record',b'event_msg'}
TYPE=re.compile(rb'"type"\s*:\s*"([^"\\]+)"')
TOKEN_NAMES=('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens','total_tokens')

def bounds(first,last):
    zone=ZoneInfo('America/New_York')
    start=dt.datetime.combine(dt.date.fromisoformat(first),dt.time(),zone)
    end=dt.datetime.combine(dt.date.fromisoformat(last)+dt.timedelta(days=1),dt.time(),zone)
    if end<=start:raise ValueError('Invalid date range')
    return start,end

def timestamp(value):
    if value is None:return None
    if isinstance(value,(int,float)):
        return dt.datetime.fromtimestamp(value,dt.timezone.utc)
    return dt.datetime.fromisoformat(value.replace('Z','+00:00'))

def parse(path,offset,context,start,end,host):
    """Return selected rows plus a complete-line checkpoint; never sum cumulative totals."""
    meta=context.get('meta',{})
    current=context.get('current',{})
    settings=context.get('settings',{})
    turnmap=context.get('turnmap',{})
    usage=[];allowance=[];turns={};stats={'complete_lines':0,'missing_response_ids':0,'token_count_without_info':0}
    seen=set()
    with path.open('rb') as f:
        f.seek(offset)
        while True:
            position=f.tell();line=f.readline()
            if not line:break
            if not line.endswith(b'\n'):
                f.seek(position);break
            offset=f.tell();stats['complete_lines']+=1
            m=TYPE.search(line[:300])
            if not m or m[1] not in KINDS:continue
            try:record=json.loads(line)
            except ValueError as exc:raise ValueError(f'Malformed complete JSON line in {path.name} at byte {position}') from exc
            kind=record.get('type');p=record.get('payload') or {};stamp=timestamp(record.get('timestamp'))
            if kind=='session_meta':
                meta={k:p.get(k) for k in ('id','session_id','parent_thread_id','timestamp','cwd','source','cli_version')}
                continue
            thread=meta.get('id')
            if kind=='event_msg' and p.get('type')=='thread_settings_applied':
                selected=p.get('thread_settings') or {}
                settings={k:selected.get(k) for k in ('model','reasoning_effort','service_tier')}
            if kind=='turn_context':
                current={k:p.get(k) for k in ('turn_id','root_turn_id','model','effort','service_tier')}
                if current.get('service_tier') is None:
                    current['service_tier']=settings.get('service_tier')
                turnmap[current.get('turn_id')]=current.copy()
            if not stamp or not start<=stamp<end:continue
            tid=p.get('turn_id') or current.get('turn_id')
            if kind=='turn_context' and tid:
                key=(thread,tid);turns.setdefault(key,{}).update(current)
            if kind=='event_msg' and p.get('type') in ('task_started','task_complete') and tid:
                row=turns.setdefault((thread,tid),{})
                row.update(turnmap.get(tid,{}))
                for field in ('started_at','completed_at','duration_ms'):
                    if p.get(field) is not None:row[field]=p[field]
                if p.get('type')=='task_started':row.setdefault('started_at',record.get('timestamp'))
            if kind=='token_usage_record':
                u=p.get('usage')
                if not isinstance(u,dict):raise ValueError('Token usage record missing per-response usage')
                response=p.get('response_id')
                if not response:
                    stats['missing_response_ids']+=1
                    # Never manufacture a count without an authoritative response identity.
                    continue
                event='response:'+response
                if event in seen:continue
                seen.add(event)
                t=p.get('thread_id') or thread
                ctx=turnmap.get(p.get('turn_id'),current)
                values=[u.get(name) for name in TOKEN_NAMES]
                usage.append((event,response,stamp,host,t,p.get('session_id'),p.get('turn_id'),p.get('root_turn_id'),ctx.get('model'),ctx.get('effort'),ctx.get('service_tier'),*values,Jsonb({k:u.get(k) for k in TOKEN_NAMES}),str(path),position))
            if kind=='event_msg' and p.get('type')=='token_count':
                rates=p.get('rate_limits') or {}
                if p.get('info') is None:stats['token_count_without_info']+=1
                for window_kind in ('primary','secondary'):
                    w=rates.get(window_kind)
                    if not w:continue
                    reset=w.get('resets_at')
                    key=hashlib.sha256(json.dumps([host,thread,record.get('timestamp'),window_kind,rates],sort_keys=True).encode()).hexdigest()
                    allowance.append((key,stamp,host,thread,tid,rates.get('limit_id'),window_kind,w.get('used_percent'),w.get('window_minutes'),dt.datetime.fromtimestamp(reset,dt.timezone.utc) if reset is not None else None,rates.get('plan_type'),str(path),position))
    context={'meta':meta,'current':current,'turnmap':turnmap,'settings':settings,'parser_version':2}
    turnrows=[(host,t,tid,p.get('root_turn_id'),p.get('model'),p.get('effort'),p.get('service_tier'),timestamp(p.get('started_at')),timestamp(p.get('completed_at')),p.get('duration_ms')) for (t,tid),p in turns.items() if t]
    return offset,context,usage,allowance,turnrows,stats

UPSERT_TURN="""INSERT INTO public.codex_turns(host,thread_id,turn_id,root_turn_id,model,effort,service_tier,started_at,completed_at,duration_ms)
 VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(host,thread_id,turn_id) DO UPDATE SET
 root_turn_id=coalesce(excluded.root_turn_id,codex_turns.root_turn_id), model=coalesce(excluded.model,codex_turns.model),effort=coalesce(excluded.effort,codex_turns.effort),
 service_tier=coalesce(excluded.service_tier,codex_turns.service_tier),started_at=coalesce(excluded.started_at,codex_turns.started_at),completed_at=coalesce(excluded.completed_at,codex_turns.completed_at),duration_ms=coalesce(excluded.duration_ms,codex_turns.duration_ms)"""
INSERT_USAGE='INSERT INTO public.token_usage(event_key,response_id,observed_at,host,thread_id,session_id,turn_id,root_turn_id,model,effort,service_tier,input_tokens,cached_input_tokens,cache_write_input_tokens,output_tokens,reasoning_output_tokens,total_tokens,usage,source_file,source_offset) VALUES('+','.join(['%s']*20)+') ON CONFLICT(event_key) DO UPDATE SET service_tier=coalesce(excluded.service_tier,token_usage.service_tier)'
INSERT_ALLOWANCE='INSERT INTO public.session_allowance(event_key,observed_at,host,thread_id,turn_id,limit_id,window_kind,used_percent,window_minutes,resets_at,plan_type,source_file,source_offset) VALUES('+','.join(['%s']*13)+') ON CONFLICT(event_key) DO NOTHING'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from',dest='first',required=True)
    parser.add_argument('--to',dest='last',required=True)
    parser.add_argument('--root',type=Path,default=Path.home()/'.codex')
    args=parser.parse_args();start,end=bounds(args.first,args.last)
    host=socket.gethostname();total={'files_scanned':0,'files_changed':0,'response_records_seen':0,'missing_response_ids':0,'complete_lines':0}
    with connect() as c:
        with c.transaction():c.execute((ROOT/'sql/002_session_telemetry.sql').read_text())
        before=c.execute('SELECT count(*) FROM public.token_usage').fetchone()[0]
        files=sorted(set(args.root.joinpath('sessions').rglob('*.jsonl'))|set(args.root.joinpath('archived_sessions').rglob('*.jsonl')))
        for path in files:
            previous=c.execute('SELECT byte_offset,context FROM public.session_import_files WHERE host=%s AND source_file=%s AND range_start=%s AND range_end=%s',(host,str(path),start,end)).fetchone()
            offset,ctx=previous if previous else (0,{})
            if ctx.get('parser_version')!=2:offset,ctx=0,{}
            if path.stat().st_size<offset:raise RuntimeError(f'Source truncated: {path.name}; checkpoint retained')
            total['files_scanned']+=1
            if path.stat().st_size==offset:continue
            new,ctx,usages,rates,turns,stats=parse(path,offset,ctx,start,end,host)
            if new==offset:continue
            meta=ctx.get('meta',{})
            with c.transaction():
                if (usages or rates or turns) and meta.get('id'):
                    c.execute('INSERT INTO public.codex_sessions(host,thread_id,session_id,parent_thread_id,started_at,cwd,source,cli_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(host,thread_id) DO NOTHING',
                      (host,meta['id'],meta.get('session_id'),meta.get('parent_thread_id'),timestamp(meta.get('timestamp')),meta.get('cwd'),Jsonb(meta.get('source')),meta.get('cli_version')))
                with c.cursor() as cur:
                    if turns:cur.executemany(UPSERT_TURN,turns)
                    if usages:cur.executemany(INSERT_USAGE,usages)
                    if rates:cur.executemany(INSERT_ALLOWANCE,rates)
                c.execute('INSERT INTO public.session_import_files(host,source_file,range_start,range_end,byte_offset,context) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(host,source_file,range_start,range_end) DO UPDATE SET byte_offset=excluded.byte_offset,context=excluded.context,updated_at=now()', (host,str(path),start,end,new,Jsonb(ctx)))
            total['files_changed']+=1;total['response_records_seen']+=len(usages)
            for k in ('missing_response_ids','complete_lines'):total[k]+=stats[k]
            if total['files_scanned']%50==0:print('Scanned',total['files_scanned'],'of',len(files),flush=True)
        after=c.execute('SELECT count(*) FROM public.token_usage').fetchone()[0]
        total['new_responses']=after-before
        total['database_responses_in_range']=c.execute('SELECT count(*) FROM public.token_usage WHERE observed_at>=%s AND observed_at<%s',(start,end)).fetchone()[0]
    total.update({'host':host,'from':args.first,'through':args.last,'timezone':'America/New_York','source_root':str(args.root),'completed_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    report=ROOT/'data'/('session_import_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')
    report.parent.mkdir(parents=True,exist_ok=True)
    report.write_text(json.dumps(total,indent=2));print(json.dumps(total,indent=2))

if __name__=='__main__':main()
