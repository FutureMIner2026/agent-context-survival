# The death formula for long agent conversations

> All numbers below are measured from real traffic, not estimated from docs.

## 1. What actually kills a conversation

Three separate limits exist, and people routinely confuse them:

1. **Model window** — 1,048,576 tokens.
2. **Completion reserve** — 256,000 tokens, subtracted from the window before
   your messages are even counted.
3. **Proxy messages budget** — 792,576 tokens, enforced *before* the request is
   forwarded. This is the one that usually bites first.

The reported error looks like this:

```
This model's maximum context length is 1048576 tokens.
However, you requested 2636049 tokens (2380049 in the messages, 256000 in the completion).
```

Note the structure: `requested = messages + 256000`. The reserve is constant, so
the only variable you control is the messages budget.

## 2. Samples

| Session | Requested tokens | Ratio to window | Outcome |
|---|---|---|---|
| A | 2,636,049 | 2.51x | rejected |
| B | 2,278,371 | 2.17x | rejected |
| C | 2,256,134 | 2.15x | rejected |
| D | 2,110,209 | 2.01x | rejected |
| E | 1,150,966 | 1.10x | rejected |
| F | 2,891,642 | 2.76x | rejected |

Separately, three fail-fast `502` responses were generated **by the proxy
itself** (the payload was never forwarded) when messages exceeded **792,576**.
That number is the hard budget, not a heuristic.

## 3. Bytes per token

To act *before* the request fails, you need a conversion from on-disk size to
tokens. We paired outbound byte counts with upstream token counts over 3,126
requests:

```
bytes per token = 3.44   (range 3.08 - 3.98, mixed CJK + English + JSON)
```

So:

```
death line (bytes) = 792,576 x 3.44 = 2,726,461 bytes = 2.60 MiB
```

If your serialized messages exceed 2.60 MiB, you are at the wall.

## 4. The part nobody warns you about: folding

Long conversations survive only because something folds old turns into
summaries. We measured how much each fold reclaims:

| Session | Folds | Reclaimed |
|---|---|---|
| healthy | many | 9.9% / 12.7% / 24.7% per pass |
| dying | 32 passes | **~2% per pass** |

A fold that reclaims 2% while you add ~1,000 tokens per turn is not folding —
it is bookkeeping. The session looks healthy (every fold "succeeds") and dies.

**Actionable:** log the reclaim ratio of every fold. If it falls below ~5% on a
session that is actively growing, treat it as an incident. Absence of an error
message is not evidence of health.

## 5. Estimating a session before you open it

Compressed session files lie: we measured a **6x compression ratio** on a
session that was 1.13 MB on disk but 6.89 MB when decompressed, costing ~816,000
tokens on its first request.

Rules that survived contact with reality:

- Never estimate from compressed size.
- Skip streaming replay payloads (they are not sent back to the model).
- For a session that has **never been folded**, multiply the naive estimate by
  ~1.9 and add ~15,000 tokens of fixed overhead (system prompt + tool schemas).
- For a session that **has** been folded, the on-disk content is much larger
  than what is actually sent — trust the proxy's own counters instead.

## 6. Reproducing this on your setup

You need three things: your proxy's request log (bytes and token counts), the
session files, and the error messages. The three measurements that matter:

1. Find the **messages budget**: the point where requests stop being forwarded.
2. Find **bytes per token** for your language mix.
3. Track **fold reclaim ratio** over time.

If any of the three is missing, you are guessing.

## 7. Caveats

- Constants here are implementation-specific. The formula is general; the
  numbers are ours.
- Byte/token ratios shift with content type (code vs prose vs JSON).
- A healthy fold ratio is not a guarantee — long sessions still need periodic
  structural surgery (hollow old tool output, replace distant history with
  summaries) and a rollback path.
