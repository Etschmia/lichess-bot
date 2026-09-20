#!/usr/bin/env python3
"""One rated 5+0 bot/variant attempt per cron tick; durable at-most-once ledger."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone

import requests
import yaml

ROOT = Path(__file__).resolve().parent
MARKER = '# martuni-variant-sweep'
VARIANTS = ['antichess', 'kingOfTheHill', 'horde', 'racingKings', 'threeCheck']
FINAL = {'accepted', 'declined', 'timeout', 'expired', 'canceled', 'api_error', 'send_unknown'}


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, data):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def process_count():
    # Exact comm, not command-line substrings: excludes cron, shells and this runner.
    count = 0
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if (entry / 'comm').read_text().strip().lower() == 'martuni':
                count += 1
        except (FileNotFoundError, ProcessLookupError):
            pass
    return count


def comment_cron(text):
    return '\n'.join(('# completed ' + line) if MARKER in line and not line.lstrip().startswith('#') else line
                     for line in text.splitlines()) + '\n'


def disable_cron():
    old = subprocess.check_output(['crontab', '-l'], text=True)
    new = comment_cron(old)
    if old != new:
        save(ROOT / 'variant_sweep_crontab_completion_backup.json', {'at': now(), 'crontab': old})
        subprocess.run(['crontab', '-'], input=new, text=True, check=True)
    actual = subprocess.check_output(['crontab', '-l'], text=True)
    if any(MARKER in line and not line.lstrip().startswith('#') for line in actual.splitlines()):
        raise RuntimeError('Cron deactivation verification failed')


class API:
    def __init__(self):
        cfg = yaml.safe_load((ROOT / 'config.yml').read_text())
        self.s = requests.Session()
        self.s.headers.update({'Authorization': 'Bearer ' + cfg['token'], 'Accept': 'application/json'})
        self.base = 'https://lichess.org'

    def get(self, path):
        r = self.s.get(self.base + path, timeout=15)
        r.raise_for_status()
        return r.json()

    def post(self, path, data=None):
        return self.s.post(self.base + path, data=data, timeout=20)

    def status(self, cid):
        data = self.get('/api/challenge/' + cid + '/show')
        return data.get('challenge', data)


def initialize(bots):
    return {'created_at': now(), 'rated': True, 'clock': {'limit': 300, 'increment': 0},
            'wait_seconds': 600, 'attempts': [
                {'opponent': b['name'], 'variant': v, 'status': 'queued'}
                for v in VARIANTS for b in bots if b['name'].lower() != 'martuni']}


def finish(row, status, **extra):
    row.update(status=status, finished_at=now(), **extra)


def observe(api, row):
    c = api.status(row['challenge_id'])
    status = c.get('status')
    if status == 'accepted':
        finish(row, 'accepted', game_id=row['challenge_id'], game_url='https://lichess.org/' + row['challenge_id'])
    elif status == 'declined':
        finish(row, 'declined', reason=c.get('declineReason'), reason_key=c.get('declineReasonKey'))
    elif status in ('canceled', 'expired'):
        finish(row, 'timeout' if row.get('cancel_requested_at') else status)
    row['last_observed_status'] = status
    row['last_checked_at'] = now()
    return status


def monitor(api, row, state, path):
    # Recover pending attempts before checking capacity, including overdue cancellation.
    while row['status'] not in FINAL:
        try:
            status = observe(api, row)
            save(path, state)
            if row['status'] in FINAL:
                break
            if time.time() >= row['deadline_epoch']:
                # Only cancel an explicitly observed pending challenge; never a known game.
                if status not in ('created', 'offline'):
                    row['last_error'] = 'Unexpected challenge state; cancellation deferred'
                    save(path, state)
                    return
                row['cancel_requested_at'] = now()
                save(path, state)
                response = api.post('/api/challenge/' + row['challenge_id'] + '/cancel')
                row['cancel_http_status'] = response.status_code
                response.raise_for_status()
                observe(api, row)  # Read exact target back; never assume cancellation succeeded.
                save(path, state)
                return
        except requests.RequestException as exc:
            row['last_error'] = type(exc).__name__
            save(path, state)
            if time.time() >= row['deadline_epoch']:
                return  # Next tick retries reconciliation; no new challenge meanwhile.
        time.sleep(min(5, max(0.1, row['deadline_epoch'] - time.time())))


def run(api, state, path, dry=False):
    rows = state['attempts']
    if dry:
        print(json.dumps({'engine_processes': process_count(), 'total': len(rows),
                          'counts': {s: sum(r['status'] == s for r in rows) for s in sorted({r['status'] for r in rows})},
                          'next': next((r for r in rows if r['status'] == 'queued'), None)}, ensure_ascii=False))
        return
    assert api.get('/api/account')['id'] == 'martuni', 'Wrong account'
    pending = next((r for r in rows if r['status'] == 'pending'), None)
    if pending:
        monitor(api, pending, state, path)
    elif any(r['status'] == 'sending' for r in rows):
        # Crash after write-ahead reservation: never blindly send that pair twice.
        for row in rows:
            if row['status'] == 'sending':
                outgoing = api.get('/api/challenge').get('out', [])
                matches = [c for c in outgoing if c.get('destUser', {}).get('id') == row['opponent'].lower()
                           and c.get('variant', {}).get('key') == row['variant']
                           and c.get('rated') is True and c.get('timeControl', {}).get('limit') == 300
                           and c.get('timeControl', {}).get('increment') == 0]
                if len(matches) == 1:
                    row.update(status='pending', challenge_id=matches[0]['id'])
                    save(path, state)
                    monitor(api, row, state, path)
                else:
                    finish(row, 'send_unknown', reason='Interrupted send; not repeated to avoid duplicates')
                    save(path, state)
    elif all(r['status'] in FINAL for r in rows):
        disable_cron()
        state['completed_at'] = now()
        save(path, state)
        print('Sweep complete; own crontab line commented out')
        return
    elif time.time() < state.get('cooldown_until', 0):
        print('API cooldown; skipped')
        return
    elif process_count() >= 2:
        print('At least two Martuni processes; skipped')
        return
    else:
        # Additional safety: do not queue work into a disconnected bot or full account.
        if subprocess.run(['systemctl', 'is-active', '--quiet', 'lichess-bot']).returncode:
            print('Bot service inactive; skipped')
            return
        if api.get('/api/account/playing').get('nowPlaying', []):
            print('Active game in progress; skipped')
            return
        if api.get('/api/challenge').get('out'):
            print('Other outgoing challenge pending; skipped')
            return
        row = next(r for r in rows if r['status'] == 'queued')
        if process_count() >= 2:
            return
        row.update(status='sending', sent_at=now(), deadline_epoch=time.time() + 600)
        save(path, state)  # Reserve pair BEFORE POST.
        try:
            response = api.post('/api/challenge/' + row['opponent'], {
                'rated': 'true', 'clock.limit': '300', 'clock.increment': '0',
                'variant': row['variant'], 'color': 'random', 'keepAliveStream': 'false'})
        except requests.RequestException as exc:
            row['last_error'] = type(exc).__name__
            save(path, state)  # Leave sending for reconciliation next tick.
            return
        row['http_status'] = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = {'error': 'Non-JSON API response'}
        c = body.get('challenge', body)
        if response.status_code == 429:
            # Server rejected this request, no challenge exists; safe to retry after cooldown.
            seconds = body.get('ratelimit', {}).get('seconds', 1200)
            state['cooldown_until'] = time.time() + max(60, int(seconds))
            state.setdefault('technical_events', []).append({'at': now(), 'opponent': row['opponent'], 'variant': row['variant'], 'http_status': 429})
            row['status'] = 'queued'
        elif response.ok and c.get('id'):
            row.update(challenge_id=c['id'], status='pending')
            save(path, state)
            monitor(api, row, state, path)
        elif response.status_code >= 500:
            row['last_error'] = 'Server error; reconciliation required'
        else:
            finish(row, 'api_error', reason=body.get('error', 'Challenge creation rejected'))
        save(path, state)
    if all(r['status'] in FINAL for r in rows):
        disable_cron()
        state['completed_at'] = now()
        save(path, state)
    print(json.dumps({'completed': sum(r['status'] in FINAL for r in rows), 'total': len(rows)}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    with (ROOT / 'variant_challenge_sweep.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another sweep worker is active; skipped')
            return
        path = ROOT / 'variant_challenge_sweep_state.json'
        state = json.loads(path.read_text()) if path.exists() else initialize(json.loads((ROOT / 'variant_bots.json').read_text()))
        if not args.dry_run:
            save(path, state)
        run(None if args.dry_run else API(), state, path, args.dry_run)


if __name__ == '__main__':
    main()
