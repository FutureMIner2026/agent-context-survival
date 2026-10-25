# The death formula for long agent conversations

> All numbers below are measured from real traffic, not estimated from docs.

## 0. The scale of the evidence

Nothing here is a single observation dressed up as a law. This is what the
numbers rest on, and where each sample came from:

| What | Sample size | How it was collected |
|---|---|---|
| Refusals that expose the window and the reserve | **6** | production incidents, error text quoted verbatim |
| Fail-fast rejections that expose the messages budget | **3** | proxy-generated `502`, payload never forwarded |
| Paired (bytes sent -> tokens billed) requests | **3,126** | proxy usage log, one pair per request |
| Folding passes classified by reclaim ratio | **108** | proxy fold ledger, across every session we ran |
| Sessions measured for per-turn growth | **6** | contiguous `input` deltas in the usage log |
| Sessions run through the survival calculation | **7** | every session in the library with a real token reading |
| Bodies rebuilt end-to-end (transplant) | **4** | deliberate, each verified with a ten-gate check |
| Crash targets cloned on purpose to reproduce the failure | **2** | deliberate, then frozen |

Two honest qualifications, because they affect how you should read this:

1. **The refusals are not a controlled experiment.** They are accidents we read
   afterwards. What makes them usable is that all six agree on the same two
   constants — that agreement is the evidence, not the sample count.
2. **The deliberate experiments are ours alone.** Two crash clones, four
   transplants, twenty-six rollback backups. Small numbers, but every one of
   them was run to answer a specific question, and every claim in this document
   that says "we tested" points at one of them.

What we did *not* do: change one variable at a time under a fixed everything
else. This is field data from a real system, and it is presented that way.

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

### A caveat on the 792,576 number

This is the budget of **the deployment we measured**, not a universal constant.
Within the same setup we have also observed a session with a budget of
**1,000,000** (`usage=40% (404238/1000000)`).

The formula still holds:

    budget = window - completion_reserve        (policy is applied on top)
    death_line_bytes = budget * bytes_per_token

but **the budget must be read per session, not assumed**. Most proxies print it
directly, e.g. `usage=40% (315973/792576)`.

## 2. Samples

Six refusals, captured as the API returned them. The columns are what the error
text contained, not a reconstruction:

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
