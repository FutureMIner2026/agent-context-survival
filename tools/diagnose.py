#!/usr/bin/env python3
"""diagnose.py - estimate how close a long agent conversation is to death.

Why this exists
---------------
Compressed session files lie. We measured a 6x compression ratio on a session
that was 1.13 MB on disk but 6.89 MB when decompressed, and cost ~816,000 tokens
on its first request. This tool measures the thing that actually matters:
how much text would be sent back to the model.

Usage
-----
    python diagnose.py <file-or-dir> [--json]
    python diagnose.py my_session.jsonl
    python diagnose.py my_session.jsonl.zstd
    python diagnose.py ./sessions/            # every *.jsonl* under it

No dependencies beyond the standard library (zstandard is optional, only needed
for .zstd inputs).

Numbers used (measure your own; these are ours)
-----------------------------------------------
    model window            1,048,576 tokens
    completion reserve        256,000 tokens
    proxy messages budget     792,576 tokens   <- the one that usually bites
    bytes per token                 3.44       (mixed CJK + English + JSON)
    death line                     2.60 MiB    = 792,576 * 3.44
    never-folded multiplier         1.9x  + 15,000 tokens fixed overhead
"""
import argparse
import glob
import io
import json
import os
import sys

WINDOW = 1048576
RESERVE = 256000
BUDGET = 792576
BYTES_PER_TOKEN = 3.44
FRESH_FACTOR = 1.9
FIXED_OVERHEAD = 15000
DEATH_LINE = int(BUDGET * BYTES_PER_TOKEN)


def read_text(path):
    """Return the session's JSONL text, transparently handling .zstd."""
    if path.endswith('.zstd'):
        try:
            import zstandard as zstd
        except ImportError:
            try:
                import zstd  # older name
            except ImportError:
                raise SystemExit(
                    'zstandard not installed; run: pip install zstandard\n'
                    'or decompress the file first')
        with io.open(path, 'rb') as f:
            with zstd.ZstdDecompressor().stream_reader(f) as r:
                return r.read().decode('utf-8', 'replace')
    with io.open(path, encoding='utf-8', errors='replace') as f:
        return f.read()


def sent_chars(text):
    """Characters that would actually be re-sent to the model.

    Skips streaming replay payloads (data.stream) - they are not sent back.
    Counts message text, plain content blocks and tool-call arguments.
    """
    total = 0
    events = 0
    turns = set()
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if not isinstance(e, dict):
            continue
        events += 1
        t = e.get('turn')
        if not isinstance(t, int):
            _d = e.get('data')
            if isinstance(_d, dict):
                t = _d.get('turn')
        if isinstance(t, int):
            turns.add(t)
        d = e.get('data')
        if not isinstance(d, dict):
            continue
        msg = d.get('message')
        if isinstance(msg, dict):
            for b in (msg.get('content') or []):
                if isinstance(b, dict) and isinstance(b.get('text'), str):
                    total += len(b['text'])
        blocks = d.get('content')
        if isinstance(blocks, list):
            for b in blocks:
                if isinstance(b, dict) and isinstance(b.get('text'), str):
                    total += len(b['text'])
        if isinstance(d.get('arguments'), (dict, list)):
            total += len(json.dumps(d['arguments'], ensure_ascii=False))
    return total, events, len(turns)


def report(path, as_json=False):
    text = read_text(path)
    chars, events, turns = sent_chars(text)
    naive = int(chars / BYTES_PER_TOKEN)
    fresh = int(naive * FRESH_FACTOR + FIXED_OVERHEAD)
    disk = os.path.getsize(path)
    ratio = (chars / disk) if disk else 0

    def pct(tok, base=BUDGET):
        return 100.0 * tok / base

    out = {
        'file': os.path.basename(path),
        'disk_bytes': disk,
        'sent_chars': chars,
        'events': events,
        'turns': turns,
        'compression_ratio': round(ratio, 2),
        'naive_tokens': naive,
        'fresh_session_tokens': fresh,
        'pct_of_naive_budget': round(pct(naive), 1),
        'pct_of_fresh_budget': round(pct(fresh), 1),
        'death_line_bytes': DEATH_LINE,
    }
    if as_json:
        print(json.dumps(out, indent=2))
        return out

    print('file        %s' % out['file'])
    print('on disk     %.2f MB' % (disk / 1048576.0))
    print('sent text   %s chars (%.2f MB decompressed, %.1fx the file on disk)'
          % (format(chars, ','), chars / 1048576.0, ratio))
    print('events      %s   turns %s' % (format(events, ','), format(turns, ',')))
    print('')
    print('naive       %s tok  (%.0f%% of the %s messages budget)'
          % (format(naive, ','), pct(naive), format(BUDGET, ',')))
    print('if unfolded %s tok  (%.0f%%)  <- use this for a session that never folded'
          % (format(fresh, ','), pct(fresh)))
    print('death line  %s bytes (%.2f MiB)' % (format(DEATH_LINE, ','), DEATH_LINE / 1048576.0))
    print('')

    worst = max(pct(naive), pct(fresh))
    if worst >= 100:
        print('VERDICT     over budget - a request in this state is likely rejected')
        print('NEXT        this is the case for structural surgery: hollow old tool')
        print('            output, or replace distant history with a summary, and keep')
        print('            a rollback copy before touching anything.')
    elif worst >= 85:
        print('VERDICT     close to the wall - next few turns may cross it')
        print('NEXT        shrink now, while the conversation can still talk to itself.')
    elif worst >= 60:
        print('VERDICT     getting tight - worth acting before it becomes an incident')
    else:
        print('VERDICT     healthy')
    return out


def main():
    ap = argparse.ArgumentParser(description='Estimate how close a conversation is to its context death line.')
    ap.add_argument('path', help='session file (.jsonl / .jsonl.zstd) or a directory')
    ap.add_argument('--json', action='store_true', dest='as_json')
    a = ap.parse_args()

    if os.path.isdir(a.path):
        files = []
        for pat in ('**/*.jsonl', '**/*.jsonl.zstd'):
            files.extend(glob.glob(os.path.join(a.path, pat), recursive=True))
        files = sorted(set(files))
        if not files:
            print('no session files found under %s' % a.path)
            return 1
        rows = [report(f, True) for f in files]
        rows.sort(key=lambda r: -r['pct_of_fresh_budget'])
        print('%-40s %10s %8s' % ('file', 'fresh tok', '% budget'))
        for r in rows:
            print('%-40s %10s %7.0f%%' % (r['file'][:40], format(r['fresh_session_tokens'], ','),
                                          r['pct_of_fresh_budget']))
        return 0

    report(a.path, a.as_json)
    return 0


if __name__ == '__main__':
    sys.exit(main())
