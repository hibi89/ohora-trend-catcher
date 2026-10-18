"""Daily-first OHORA RAW v2 orchestrator (manual foreground, no autonomous Meta calls).

Policy: KST front pages for all eligible official accounts -> catchup gaps ->
historical 365-day cursor traversal. One existing Graph account per slot.
Use --live explicitly; CTRL+C stops the foreground loop.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sqlite3
import time
from zoneinfo import ZoneInfo

import collector

KST = ZoneInfo('Asia/Seoul')
DAILY_LIMIT = 4
BATCH_SIZE = 5
MAX_BATCHES = 30
STOP_USAGE = 85
MAX_FRONT_ATTEMPTS = 2


def today_kst():
    return datetime.now(KST).date().isoformat()


def select_daily_tasks(db, day, max_tasks=294):
    """Select exactly one stage at a time; do not backfill while front is pending.

    A failed account is retried after yet-unattempted accounts, max twice a day.
    Blocked or exhausted retry accounts are reported as exceptions, never successes.
    """
    accounts=[dict(row) for row in db.execute('SELECT * FROM accounts ORDER BY ordinal')]
    states={row['account_key']:dict(row) for row in db.execute(
        'SELECT * FROM daily_account_state WHERE day_kst=?',(day,))}
    front=[]
    for account in accounts:
        if account['blocked']:
            continue
        state=states.get(account['account_key'])
        if state and (state['status'] in ('success','needs_review','excluded') or
                      state['attempts'] >= MAX_FRONT_ATTEMPTS):
            continue
        attempts=state['attempts'] if state else 0
        front.append((attempts,account['ordinal'],'front',account))
    if front:
        # Explicit roster partition: complete 1..150 before selecting 151..294.
        first=[item for item in front if item[1]<=150]
        front=first if first else [item for item in front if item[1]>150]
        front.sort(key=lambda item:(item[0],item[1]))
        return [(phase,account) for _,__,phase,account in front[:max_tasks]]

    # Avoid an endlessly failing catchup/history account starving all other
    # work. Retry it on the next KST day and keep its cursor unchanged.
    failures={(r['account_key'],r['phase']):r['n'] for r in db.execute(
        """SELECT account_key,phase,COUNT(*) AS n FROM request_log
           WHERE status='failed' AND phase IN ('catchup','history')
             AND date(finished_at,'+9 hours')=?
           GROUP BY account_key,phase""",(day,))}
    catchup=[('catchup',a) for a in accounts if not a['blocked'] and a['catchup_cursor']
             and failures.get((a['account_key'],'catchup'),0)<MAX_FRONT_ATTEMPTS]
    if catchup:
        return catchup[:max_tasks]

    history=[a for a in accounts if not a['blocked'] and a['latest_id'] and not a['backfill_done']
             and not a['catchup_cursor'] and failures.get((a['account_key'],'history'),0)<MAX_FRONT_ATTEMPTS]
    history.sort(key=lambda a:(a['last_history'] or '',a['ordinal']))
    return [('history',a) for a in history[:max_tasks]]


def inspect_store(base, source, digest):
    """Read scheduler status while holding the same cross-process lock as collector."""
    with collector.process_lock(base):
        db=collector.connect(Path(base)/'collection.sqlite',source,digest)
        try:
            day=today_kst()
            # Adopt verified front observations already made earlier today by the
            # legacy collector; no extra API requests, no old rows updated.
            with db:
                db.execute("""INSERT OR IGNORE INTO daily_account_state(
                    day_kst,account_key,status,attempts,last_request_id,last_error,updated_at)
                    SELECT ?,account_key,'success',1,MAX(request_id),NULL,MAX(finished_at)
                    FROM request_log
                    WHERE phase='front' AND status='success'
                      AND date(finished_at,'+9 hours')=?
                    GROUP BY account_key""",(day,day))
            choices=select_daily_tasks(db,day)
            success=db.execute(
                "SELECT COUNT(*) FROM daily_account_state WHERE day_kst=? AND status='success'",(day,)).fetchone()[0]
            failures=db.execute(
                "SELECT COUNT(*) FROM daily_account_state WHERE day_kst=? AND status!='success'",(day,)).fetchone()[0]
            blocked=db.execute('SELECT COUNT(*) FROM accounts WHERE blocked=1').fetchone()[0]
            pending_gaps=db.execute("SELECT COUNT(*) FROM accounts WHERE catchup_cursor IS NOT NULL AND blocked=0").fetchone()[0]
            latest=db.execute('SELECT * FROM slots ORDER BY slot_id DESC LIMIT 1').fetchone()
            ready=collector.parse_date(latest['ready_at']) if latest and latest['status']!='active' else None
            return {'day':day,'tasks':choices,'stage':choices[0][0] if choices else 'done',
                    'success':success,'exceptions':failures,'blocked':blocked,'pending_gaps':pending_gaps,
                    'ready':ready,'slot_status':latest['status'] if latest else None}
        finally:
            db.close()


def parse_args(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--live',action='store_true',help='Opt in to real Meta API calls')
    parser.add_argument('--once',action='store_true',help='Execute at most one collection run, never sleep')
    return parser.parse_args(argv)


def main(argv=None):
    options=parse_args(argv)
    rows,digest=collector.load_accounts(options.source)
    if not options.live:
        print('DRY RUN: daily-front -> catchup -> history; 294 accounts, 4 posts, batch 5,',
              '150 unique accounts/slot, stop 85%, 63-minute cooldown.')
        print('NO database writes, credentials read, or Meta API calls.')
        return 0

    if not options.env_file:
        raise ValueError('--env-file is required for --live')
    # Fail before touching the DB on invalid credentials or unsafe output location.
    collector.load_env(options.env_file)
    base=options.output_dir.resolve()
    if base==collector.ROOT or base.is_relative_to(collector.ROOT/'kq3_294_collection') or base.is_relative_to(collector.ROOT/'experiments'):
        raise ValueError('Output must not overwrite legacy stores')
    base.mkdir(parents=True,exist_ok=True)
    args=argparse.Namespace(
        source=options.source,env_file=options.env_file,output_dir=base,
        live=True,mode='daily',batch_size=BATCH_SIZE,max_batches=MAX_BATCHES,
        limit=DAILY_LIMIT,window_days=7,history_days=365,stop_usage=STOP_USAGE,
        extended_fields=False,cycle_minutes=63)
    print('OHORA daily-first collection: KST daily front -> catchup -> history')
    print('Profile: 294 accounts, 4 posts, batch 5, up to 150 per slot, 85% usage, 63min after finish')
    print('No Windows scheduled task installed. Stop anytime with CTRL+C.')
    try:
        while True:
            snap=inspect_store(base,rows,digest)
            print(f"[{snap['day']}] daily front success={snap['success']}/294, "
                  f"blocked={snap['blocked']}, other exceptions={snap['exceptions']}; stage={snap['stage']}")
            if snap['stage']=='done':
                print(f"No currently eligible tasks; outstanding catchup gaps={snap['pending_gaps']}. "
                      'Deferred failures are retried on the next KST day. Exiting.')
                return 0
            ready=snap['ready']
            if ready is not None:
                remaining=(ready-datetime.now(ready.tzinfo)).total_seconds()
                if remaining>0:
                    print(f'Cooling down until {ready.isoformat()} UTC; {int(remaining)+1}s remaining')
                    if options.once:return 0
                    while remaining>0:
                        time.sleep(min(30,max(1,remaining)))
                        remaining=(ready-datetime.now(ready.tzinfo)).total_seconds()
                    continue
            selected_day=snap['day']
            result=collector.run(
                args,task_selector=lambda db,n:select_daily_tasks(db,selected_day,n),
                daily_day=selected_day)
            after=inspect_store(base,rows,digest)
            print(f"Run finished: status={result}; today's front={after['success']}/294; "
                  f"next stage={after['stage']}")
            if options.once:return result
            # A paused slot may be immediately continued. A closed slot is
            # handled by persisted ready_at on the next loop iteration.
    except KeyboardInterrupt:
        print('Stopped by user. Database and checkpoints retained.')
        return 130


if __name__=='__main__':
    raise SystemExit(main())
