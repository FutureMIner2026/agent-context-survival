#!/usr/bin/env python3
"""transplant.py - shrink a JSONL conversation log without losing its shape.

Two operations, both reversible (a .bak file is written first, always):

  hollow     Blank out the bulk text of OLD tool/assistant payloads, keeping every
             event, every turn and every user message. Cheapest possible saving:
             the conversation still "is" the same conversation.

  summarize  Replace old turns with ONE summary event. Keeps recent turns intact,
             so the model still has verbatim context for what it is doing now.

Both print the token estimate before and after, using the same numbers as
diagnose.py. Nothing is written without --apply.

Examples
--------
    python transplant.py hollow  session.jsonl          # dry run
    python transplant.py hollow  session.jsonl --apply
    python transplant.py summarize session.jsonl --keep-turns 12 --apply

Why the order matters
---------------------
Hollow first: it never changes event count or turn numbers, so a schema-aware
consumer cannot notice anything but shorter strings.

Summarize second: it DOES change event count and turn numbers. If your format
enforces dense sequence numbers or forward-only references, that is where you
must renumber and re-point references - this tool will refuse to run unless you
pass --force-schema, precisely so you do not corrupt a log by accident.

No dependencies beyond the standard library (zstandard optional, for .zstd).
"""
import argparse
import datetime
import io
import json
import os
import shutil
import sys

BYTES_PER_TOKEN = 3.44
FRESH_FACTOR = 1.9
FIXED_OVERHEAD = 15000


# ---------------------------------------------------------------- io helpers

def read_lines(path):
    if path.endswith('.zstd'):
        try:
            import zstandard as zstd
        except ImportError:
            raise SystemExit('zstandard not installed; run: pip install zstandard')
        with io.open(path, 'rb') as f:
            with zstd.ZstdDecompressor().stream_reader(f) as r:
                text = r.read().decode('utf-8', 'replace')
    else:
        with io.open(path, encoding='utf-8', errors='replace') as f:
            text = f.read()
    return [l for l in text.split('\n')]


def parse(lines):
    out = []
    bad = 0
    for l in lines:
        s = l.strip()
        if not s:
            continue
        try:
            out.append(json.loads(s))
        except Exception:
            bad += 1
    return out, bad


def turn_of(e):
    t = e.get('turn')
    if isinstance(t, int):
        return t
    d = e.get('data')
    if isinstance(d, dict):
        t = d.get('turn')
        if isinstance(t, int):
            return t
    return None


def text_len(e):
    n = 0
    d = e.get('data')
    if not isinstance(d, dict):
        return 0
    msg = d.get('message')
    if isinstance(msg, dict):
        for b in (msg.get('content') or []):
            if isinstance(b, dict) and isinstance(b.get('text'), str):
                n += len(b['text'])
    blocks = d.get('content')
    if isinstance(blocks, list):
        for b in blocks:
            if isinstance(b, dict) and isinstance(b.get('text'), str):
                n += len(b['text'])
    if isinstance(d.get('arguments'), (dict, list)):
        n += len(json.dumps(d['arguments'], ensure_ascii=False))
    return n


def total_chars(events):
    return sum(text_len(e) for e in events)


def est(chars):
    naive = int(chars / BYTES_PER_TOKEN)
    return naive, int(naive * FRESH_FACTOR + FIXED_OVERHEAD)


def backup(path):
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    bak = path + '.bak.%s' % stamp
    shutil.copy2(path, bak)
    return bak


def write(path, events):
    body = '\n'.join(json.dumps(e, ensure_ascii=False) for e in events) + '\n'
    if path.endswith('.zstd'):
        try:
            import zstandard as zstd
        except ImportError:
            raise SystemExit('zstandard not installed; run: pip install zstandard')
        with io.open(path, 'wb') as f:
            f.write(zstd.ZstdCompressor(level=10).compress(body.encode('utf-8')))
    else:
        with io.open(path, 'w', encoding='utf-8') as f:
            f.write(body)
    return len(body.encode('utf-8'))


# ---------------------------------------------------------------- operations

def op_hollow(events, keep_turns, min_chars):
    turns = [t for t in (turn_of(e) for e in events) if isinstance(t, int)]
    if not turns:
        raise SystemExit('no turn numbers found; hollow needs turns to know what is "old"')
    cut = max(turns) - keep_turns
    touched = 0
    saved = 0
    for e in events:
        t = turn_of(e)
        if not isinstance(t, int) or t > cut:
            continue
        d = e.get('data')
        if not isinstance(d, dict):
            continue
        for holder in (d.get('message'), d):
            if not isinstance(holder, dict):
                continue
            blocks = holder.get('content')
            if not isinstance(blocks, list):
                continue
            for b in blocks:
                if isinstance(b, dict) and isinstance(b.get('text'), str) and len(b['text']) > min_chars:
                    saved += len(b['text'])
                    b['text'] = '[hollowed: %d chars removed to save context]' % len(b['text'])
                    touched += 1
    return events, touched, saved, cut


def op_summarize(events, keep_turns, max_chars):
    turns = [t for t in (turn_of(e) for e in events) if isinstance(t, int)]
    if not turns:
        raise SystemExit('no turn numbers found; summarize needs turns')
    last = max(turns)
    cut = last - keep_turns
    if cut <= 1:
        raise SystemExit('nothing old enough to summarize (keep-turns=%d)' % keep_turns)

    # tool/result events often carry no turn number, yet they sit between turns.
    # So cut by POSITION: everything from the first turn>=2 event up to the first
    # turn>cut event is 'old', regardless of whether an event has a turn field.
    start = None
    end = None
    for i, e in enumerate(events):
        t = turn_of(e)
        if not isinstance(t, int):
            continue
        if start is None and t >= 2:
            start = i
        if t > cut:
            end = i
            break
    if start is None or end is None:
        raise SystemExit('cannot locate the boundary between old and recent turns')
    old = events[start:end]
    kept = events[:start] + events[end:]

    lines = []
    for e in old:
        ty = e.get('type') or ''
        d = e.get('data') or {}
        txt = ''
        msg = d.get('message')
        if isinstance(msg, dict):
            for b in (msg.get('content') or []):
                if isinstance(b, dict) and isinstance(b.get('text'), str):
                    txt += b['text']
        blocks = d.get('content')
        if isinstance(blocks, list):
            for b in blocks:
                if isinstance(b, dict) and isinstance(b.get('text'), str):
                    txt += b['text']
        txt = ' '.join(txt.split())
        if not txt:
            continue
        if ty == 'user/message':
            lines.append('* user: ' + txt[:220])
        elif ty == 'assistant/message' and len(txt) > 24:
            lines.append('* assistant: ' + txt[:160])
        if sum(len(x) for x in lines) > max_chars:
            break

    body = ('[summary of turns 2-%d, %d events, original kept in the .bak file]\n'
            % (cut, len(old))) + '\n'.join(lines)
    summary = {
        'type': 'memory/summary',
        'turn': 2,
        'data': {'text': body[:max_chars], 'sourceTurns': [2, cut], 'sourceEvents': len(old)},
    }
    # insert right after the first turn's events
    out = []
    inserted = False
    for e in kept:
        out.append(e)
        if not inserted and turn_of(e) == 1:
            pass
    # put summary at the very front of the kept stream after any leading non-turn events
    i = 0
    while i < len(kept) and turn_of(kept[i]) is None:
        i += 1
    out = kept[:i] + [summary] + kept[i:]
    return out, len(old), body


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description='Shrink a JSONL conversation log, reversibly.')
    ap.add_argument('op', choices=('hollow', 'summarize'))
    ap.add_argument('path')
    ap.add_argument('--keep-turns', type=int, default=8)
    ap.add_argument('--min-chars', type=int, default=400, help='hollow: only blank text longer than this')
    ap.add_argument('--max-chars', type=int, default=4000, help='summarize: summary budget in chars')
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--force-schema', action='store_true',
                    help='summarize changes event count; pass this if you know your reader tolerates it')
    a = ap.parse_args()

    if not os.path.exists(a.path):
        raise SystemExit('no such file: %s' % a.path)

    lines = read_lines(a.path)
    events, bad = parse(lines)
    if bad:
        print('warning: %d unparsable line(s) skipped' % bad)
    if not events:
        raise SystemExit('no events parsed')

    before = total_chars(events)
    print('file        %s' % os.path.basename(a.path))
    print('events      %s' % format(len(events), ','))
    print('sent text   %s chars -> %s tok naive'
          % (format(before, ','), format(est(before)[0], ',')))

    if a.op == 'hollow':
        events, touched, saved, cut = op_hollow(events, a.keep_turns, a.min_chars)
        desc = 'hollowed %s text block(s), %s chars removed, turns <= %d' % (
            format(touched, ','), format(saved, ','), cut)
    else:
        if not a.force_schema:
            raise SystemExit(
                'summarize changes event count and turn numbers.\n'
                'If your log enforces dense sequence numbers or strict references,\n'
                'you must renumber them afterwards. Re-run with --force-schema once\n'
                'you are sure your reader tolerates it (and keep the .bak).')
        events, removed, body = op_summarize(events, a.keep_turns, a.max_chars)
        desc = 'summarized %d old event(s) into %d chars' % (removed, len(body))

    after = total_chars(events)
    n1, f1 = est(before)
    n2, f2 = est(after)
    print('')
    print('%s' % desc)
    print('after       %s chars -> %s tok naive (%.0f%% of before)'
          % (format(after, ','), format(n2, ','), 100.0 * after / max(before, 1)))
    print('unfolded    %s tok -> %s tok' % (format(f1, ','), format(f2, ',')))
    print('events      %s -> %s' % (format(len(events), ','), format(len(events), ',')))

    if not a.apply:
        print('')
        print('dry run - nothing written. add --apply to write (a .bak is created first).')
        return 0

    bak = backup(a.path)
    size = write(a.path, events)
    print('')
    print('backup      %s' % os.path.basename(bak))
    print('written     %s bytes' % format(size, ','))
    return 0


if __name__ == '__main__':
    sys.exit(main())
