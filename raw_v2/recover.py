#!/usr/bin/env python3
"""Explicit offline recovery of saved Meta batch responses; never calls the API.

Dry-run by default. Requires an independently created consistent SQLite backup
before --apply. A transmitted request without an archived response remains
UNKNOWN and is NEVER automatically retried.
"""
from __future__ import annotations

import argparse
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import sqlite3
import sys

import collector as col


def examine(base):
    base=Path(base).resolve()
    target=base/'collection.sqlite'
    if not target.is_file():raise ValueError('Existing database not found')
    with sqlite3.connect(target.as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory=sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='batch_attempts'").fetchone():
            return []
        rows=[]
        for b in db.execute("SELECT * FROM batch_attempts WHERE status NOT IN ('complete','aborted_before_send') ORDER BY started_at"):
            row=dict(b)
            archive=base/'runs'/row['run_id']/'raw'/f"batch_{row['batch_no']:04}.json"
            row['archive_exists']=archive.is_file()
            row['resolution']='REPLAYABLE' if row['archive_exists'] else 'UNKNOWN_REMOTE_OUTCOME'
            rows.append(row)
        return rows


def recover(base, source, backup, apply=False):
    base=Path(base).resolve()
    entries=examine(base)
    summary=[{'run_id':b['run_id'],'batch_no':b['batch_no'],
              'status':b['status'],'resolution':b['resolution']} for b in entries]
    if not apply:
        return {'mode':'READ_ONLY_PREVIEW','batches':summary}
    if not entries:return {'mode':'APPLY','batches':[],'replayed':0}
    if any(b['resolution']!='REPLAYABLE' for b in entries):
        raise ValueError('Unknown remote outcome; no automatic retry. Resolve missing batch archives manually.')
    backup=Path(backup).resolve() if backup else None
    if backup is None or not backup.is_file() or backup.samefile(base/'collection.sqlite'):
        raise ValueError('A separately located, verified --backup-file is mandatory for --apply')
    with sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True) as check:
        if check.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('Backup failed integrity check')
        with sqlite3.connect((base/'collection.sqlite').as_uri()+'?mode=ro',uri=True) as live:
            for table in ('posts','post_observations','request_log','batch_attempts'):
                copied=check.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                current=live.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                if copied!=current:
                    raise ValueError('Backup is not a current snapshot: '+table)
    accounts,digest=col.load_accounts(source)
    count=0
    with col.process_lock(base):
        db=col.connect(base/'collection.sqlite',accounts,digest)
        try:
            for entry in entries:
                run=entry['run_id'];number=entry['batch_no'];slot=entry['slot_id']
                archive=base/'runs'/run/'raw'/f'batch_{number:04}.json'
                body=json.loads(archive.read_text(encoding='utf-8'))
                plans=json.loads(entry['request_json'])
                if not isinstance(body,list) or len(body)!=len(plans):
                    raise ValueError('Archived batch and plans disagree; manual review required')
                oldrun=db.execute('SELECT * FROM runs WHERE run_id=?',(run,)).fetchone()
                if not oldrun:raise ValueError('Batch run record absent; cannot safely replay')
                if (base/'runs'/run/'run_manifest.json').is_file():
                    raise ValueError('Prior run manifest exists: avoid overwriting immutable evidence')
                for i,(plan,part) in enumerate(zip(plans,body)):
                    key=plan['account_key'];phase=plan['phase']
                    a=db.execute('SELECT * FROM accounts WHERE account_key=?',(key,)).fetchone()
                    if not a or a['ordinal']!=plan['ordinal']:
                        raise ValueError('Recovery account roster mismatch')
                    req=f'{run}_{number:04}_{i:02}_{a["ordinal"]:03}_{phase}'
                    if db.execute('SELECT 1 FROM request_log WHERE request_id=?',(req,)).fetchone():
                        continue
                    if not isinstance(part,dict):
                        raise ValueError('Invalid archived subresponse: manual review required')
                    try:payload=json.loads(part.get('body') or '{}')
                    except (ValueError,TypeError) as exc:raise ValueError('Invalid archived JSON') from exc
                    if not isinstance(payload,dict):raise ValueError('Invalid archived payload object')
                    http=int(part.get('code') or 0)
                    error=payload.get('error') if isinstance(payload.get('error'),dict) else {}
                    error_code=str(error.get('code','')) if error else ''
                    ts=entry['started_at']   # Preserve original observation time, not recovery time.
                    raw=col.persist_response(base,run,req,payload,'')
                    response_status='success' if http==200 and not error else 'failed'
                    def log_account(status,items,more):
                        if status!='success':
                            db.execute("""UPDATE accounts SET attempts=attempts+1,last_attempt=?,last_error=?,
                                blocked=CASE WHEN ?='110' THEN 1 ELSE blocked END WHERE account_key=?""",
                                (ts,error_code,error_code,key))
                        db.execute("""INSERT INTO request_log(
                            run_id,request_id,account_key,phase,started_at,finished_at,http_status,status,
                            records_received,has_next_page,stop_reason,error_code,error_subcode,error_message,
                            raw_response_path,requested_fields,usage_json)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (run,req,key,phase,ts,ts,http,status,items,
                             'true' if more else 'false' if status=='success' else 'unknown',
                             'recovered_archived_response',error_code,str(error.get('error_subcode','')),
                             str(error.get('message',''))[:300],str(raw.relative_to(base)),
                             col.BASE_PROFILE+','+col.MEDIA_FIELDS,
                             json.dumps({'recovered_from_batch':number,'requested_post_limit':4})))
                        db.execute("""UPDATE runs SET requests=requests+1,successes=successes+?,
                            failures=failures+? WHERE run_id=?""",
                            (int(status=='success'),int(status!='success'),run))
                        db.execute("INSERT INTO slot_accounts(slot_id,account_key,request_id) VALUES (?,?,?)",
                                   (slot,key,req))
                        if oldrun['mode']=='daily' and phase=='front':
                            observed_day=(col.parse_date(ts)+timedelta(hours=9)).date().isoformat()
                            db.execute("""INSERT INTO daily_account_state
                                (day_kst,account_key,status,attempts,last_request_id,last_error,updated_at)
                                VALUES (?,?,?,?,?,?,?)
                                ON CONFLICT(day_kst,account_key) DO UPDATE SET
                                status=excluded.status,attempts=daily_account_state.attempts+1,
                                last_request_id=excluded.last_request_id,last_error=excluded.last_error,
                                updated_at=excluded.updated_at""",
                                (observed_day,key,status,1,req,error_code or None,ts))
                    if response_status=='success':
                        try:
                            col.insert_success(db,dict(a),phase,payload,run,req,ts,365,limit=4,
                                               commit_hook=lambda new,items,more:log_account('success',items,more))
                        except ValueError as exc:
                            raise ValueError('Archived response is invalid; manual reconciliation required: '+str(exc)) from exc
                    else:
                        with db:log_account('failed',0,False)
                    count+=1
                with db:
                    db.execute("UPDATE batch_attempts SET status='complete' WHERE run_id=? AND batch_no=?",(run,number))
                rest=db.execute("""SELECT COUNT(*) FROM batch_attempts
                    WHERE run_id=? AND status NOT IN ('complete','aborted_before_send')""",(run,)).fetchone()[0]
                if rest==0:
                    with db:
                        db.execute("UPDATE runs SET finished_at=COALESCE(finished_at,?),status='recovered' WHERE run_id=?",
                                   (col.now(),run))
                    col.export_run(db,base,run,source)
                active=db.execute("SELECT status FROM slots WHERE slot_id=?",(slot,)).fetchone()
                if active and active[0]=='active':
                    col.close_slot(db,slot,'recovered_batch_conservative_cooldown',65)
            return {'mode':'APPLY','batches':summary,'replayed':count}
        finally:
            db.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--backup-file',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    if args.apply and (not args.source or not args.backup_file):
        parser.error('--apply requires --source and --backup-file')
    try:
        result=recover(args.output_dir,args.source,args.backup_file,args.apply)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError,sqlite3.Error,json.JSONDecodeError) as exc:
        print('RECOVERY_BLOCKED',str(exc),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
