# Agent Context Survival

**Where long agent conversations actually die — measured, not guessed.**

## Your agent didn't "forget" — it hit a wall you can't see

Every long AI session dies the same way. The context window fills up, the agent starts
forgetting things it definitely saw, and then — with no warning — the API refuses the
request outright. People blame the "1M window".

**It isn't.** In a proxied setup there are three separate limits, and the smallest one
is the one that kills you. We crashed conversations on purpose and measured all three:

| What you think is the limit | What actually refuses your request |
|---|---|
| 1,048,576 tokens (the model window) | **792,576 tokens** — the messages budget, i.e. the window minus the 256,000 completion reserve |
| "I still have 1M tokens of room" | **2.60 MiB of outbound bytes** — at 3.44 bytes/token, measured across 3,126 real requests |
| "Compaction is keeping up" | **a fold that reclaims ~2%** — looks healthy, saves nothing, dies anyway |

A request at **1.10x** the window is refused exactly like one at 2.76x. There is no
graceful degradation and no partial answer. **It is a cliff, not a slope.**

## The numbers we measured

| Quantity | Value | How we got it |
|---|---|---|
| Model window | 1,048,576 tokens | [6 real refusals](docs/how-we-measured.md#the-six-refusals) |
| Completion reserve | 256,000 tokens | [constant in all 6](docs/death-formula.md#0-the-scale-of-the-evidence) |
| **Proxy messages budget** | **792,576 tokens** | [3 fail-fast 502s](docs/how-we-measured.md#1-the-two-hard-numbers) |
| **Bytes per token** | **3.44** | [3,126 paired requests](docs/how-we-measured.md#2-bytes-to-tokens-the-344) |
| Folding reclaim per pass | 9.9% / 12.7% / 24.7% | [108 fold passes](docs/death-formula.md#4-the-part-nobody-warns-you-about-folding) |
| Growth per turn | ~800–1,400 tokens | [6 sessions](docs/how-we-measured.md#0-the-scale-of-the-evidence) |

## One caveat before you use the 792,576 number

**792,576 is the budget of the deployment we measured** — the window minus the
completion reserve, then whatever policy the proxy applies on top. It is *not* a
universal constant: within this same setup we have also seen a session whose
budget was **1,000,000**.

So the formula is portable, the number is not:

```
death line (bytes) = your_budget * your_bytes_per_token
```

Your proxy already prints the budget. Look for a line like:

```
usage=40% (315973/792576)
```

That single line gives you the numerator, the denominator, and the percentage —
no estimation needed.

## The consequence

```
death line (bytes) = 792,576 x 3.44 ≈ 2.60 MiB of outbound messages
```

If your outbound crosses that line, the request is rejected — regardless of how much
"1M window" you think you have left.

And a second, subtler result: **folding quality decides who survives.** One session
folded 32 passes and reclaimed ~2% each time — it grew to 2.5x the window and died.
Another reclaimed 10–25% per pass and ran all day. Same model, same config. The
difference was whether folding *actually* reclaimed anything.

## Why this repo exists

Because "use less context" is not advice, and "1M tokens" is not a budget. We publish
the measurements so you can compute **your** death line instead of guessing, and check
whether your folding is silently failing.

- [How we measured it](docs/how-we-measured.md) - the source of 3.44 bytes/token (3,126 paired requests)

## Tools

### `tools/diagnose.py` — how close is this conversation to dying?

```bash
python tools/diagnose.py my_session.jsonl.zstd
python tools/diagnose.py ./sessions/          # rank every session by risk
```

It decompresses the session, counts only the text that would actually be sent back
(skipping streaming replay), converts to tokens, and tells you where you stand against
the 792,576 messages budget — plus the `1.9x + 15,000` figure to use for a session that
has never been folded.

Standard library only; `pip install zstandard` if your sessions are `.zstd`.

### `tools/transplant.py` — the conversation is already too big, now what?

```bash
python tools/transplant.py hollow    session.jsonl --keep-turns 8 --apply
python tools/transplant.py summarize session.jsonl --keep-turns 2 --apply --force-schema
```

`hollow` blanks the bulk text of old tool output and assistant messages while keeping
every event and every turn. `summarize` replaces distant turns with a single summary
event. Both write a `.bak.<timestamp>` first and do nothing without `--apply`.

## What's here

- `docs/death-formula.md` — the full measurement: samples, method, formula, caveats
- `docs/how-we-measured.md` — how each number was produced, including what we got wrong
- `tools/` — small cross-platform scripts (diagnose a session, shrink it safely)

## Scope and honesty

- The measurements come from **one** proxy implementation. The *shape* of the formula is
  general (window − reserve = usable messages; bytes/token is a constant per language
  mix), but **the constants are ours** — measure yours.
- We do not publish other people's data. Every sample referenced is synthetic or our own,
  and the crash messages are quoted as the API returned them.

## License

MIT
