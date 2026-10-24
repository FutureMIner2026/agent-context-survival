# How we measured it

Every number in `docs/death-formula.md` came from log lines, not from estimation.
This page shows exactly which lines, how many, and where the method breaks.

If you want to check us, this is the page to attack.

---

## 1. The two hard numbers

A request to the API is billed as:

```
requested_tokens = messages_tokens + completion_tokens
```

Two constants fell out of the error messages themselves, because the API echoes
them back when it refuses a request:

```
maximum context length is 1048576 tokens
you requested 2891642 tokens (2635642 in the messages, 256000 in the completion)
```

* `1048576` — the window.
* `256000` — the completion reservation. **Constant**, not proportional: it
  appears unchanged in all six refusals we captured, with `messages` ranging
  from 1,150,966 to 2,891,642.
* `792576` — what is actually left for messages: `1048576 - 256000`.

That subtraction is the single most useful line on this page. Most people
budget against 1,048,576 and then die at 792,576.

### The six refusals

| # | messages | completion | requested | multiple of window |
|---|---|---|---|---|
| 1 | 2,380,049 | 256,000 | 2,636,049 | 2.51x |
| 2 | 2,022,371 | 256,000 | 2,278,371 | 2.17x |
| 3 | 2,000,134 | 256,000 | 2,256,134 | 2.15x |
| 4 | 1,854,209 | 256,000 | 2,110,209 | 2.01x |
| 5 | 894,966 | 256,000 | 1,150,966 | 1.10x |
| 6 | 2,635,642 | 256,000 | 2,891,642 | 2.76x |

Note #5: **1.10x**. A request barely over the line is refused exactly like one
at 2.76x. There is no graceful degradation and no partial answer. Session #5 is
the one that convinced us this is a cliff, not a slope.

---

## 2. Bytes to tokens: the 3.44

Token counts only exist server-side. On disk you have bytes. So we needed a
conversion, and we refused to guess it.

**Method.** A local proxy sits in front of the API and logs, for every request:

```
[acp-usage] input=<tokens the provider reported> outbound=<bytes we sent>
```

For each request we paired `outbound` with `input`. Both describe the same
payload, so `bytes / tokens` is measurable per request, with no assumptions.

**Sample.** 3,126 paired requests across several weeks.

**Result.**

```
mean       3.44 bytes/token
range      3.08 - 3.98
```

The spread is not noise. It tracks content: JSON-heavy payloads (lots of
`{`, `"`, `:`) sit low, prose sits high. English also differs from CJK text.
**3.44 is the mean of our traffic, and our traffic is mixed Chinese/English with
a lot of tool output.** If your payloads are pure English prose, expect closer
to 4; pure JSON, expect under 3.4.

We publish the ratio rather than a magic constant because **the method transfers
and the constant does not.** Run the same pairing on your own logs and you will
get your own number in an afternoon.

### From ratio to a death line

```
death_line_bytes = 792576 * 3.44 = 2,726,461 bytes = 2.60 MiB
```

Above that, on a session that has never been compacted, the next request is
refused. Below it, you are still alive.

Two corrections we had to make, both downward and both from being wrong:

1. We first used 3.5 MiB. That was **35% too high** — we had divided by the full
   window instead of the messages budget.
2. We then used 2.80 MiB, still too high, because we had plugged in a rounded
   ratio instead of the measured mean.

---

## 3. Why sessions die *while* they look healthy

A session that is compacting correctly never reaches the death line: every
compaction removes more than the turns since the last one added. A session that
compacts badly reaches it in a few hours.

We logged 108 compaction attempts across our sessions and split the recovery:

```
recovered  9.9%   (worst group)
          12.7%
          24.7%   (best group)
```

**The 15% line is where it starts to be dangerous and 25% is where it becomes
impossible.** The arithmetic: to survive a payload of `P` against a budget of
792,576 you need a recovery rate of at least `1 - 792576/P`.

That is why we say the disease is **compaction failure, not conversation
length**. The worst-offending session in our logs folded 32 times and recovered
2% each time; it was not writing unusually much, it was folding uselessly.

---

## 4. The estimate used by `diagnose.py`

Two estimates, because sessions differ:

```
naive    = chars / 3.44
unfolded = naive * 1.9 + 15000
```

* **naive** applies to a session that is already being compacted. It matches
  observed payloads closely.
* **unfolded** applies to a session that has never been compacted. The `1.9`
  came from one case where the file measured 1.13 MB, the naive estimate said
  136,000 tokens, and the provider reported 816,580 — a factor of 6. That was a
  transplant body carrying a summary plus a lot of reasoning blocks. After
  calibrating against two more bodies we settled on 1.9 + 15,000 as a
  deliberately **conservative** number.
* `+15000` is fixed overhead: tool schemas and system preamble, measured once at
  46,836 bytes / 13,615 tokens for our 52-tool setup.

**Known error direction:** on an old, heavily-compacted session the unfolded
estimate over-predicts, sometimes by 3x, because compaction has already removed
most of what the factor assumes is there. Over-predicting is the safe direction.
We would rather you run a test on a copy than trust the number.

---

## 5. What we do not claim

* **One provider, one model family.** The 256,000 completion reservation and the
  1,048,576 window are that provider's. Others differ; the *method* is what
  carries over.
* **The byte ratio is ours.** Re-measure it.
* **The six refusals are not a controlled experiment.** They are production
  accidents we read afterwards.
* **We did not verify the fold percentages' cause.** We observed recovery
  rates; we inferred the mechanism from the fold logs.

---

## 6. Reproducing this on your own logs

You need one thing: lines that pair **tokens reported by the provider** with
**bytes sent**. If your client can log both for the same request, you can
reproduce every number here.

```python
pairs = [(input_tokens, outbound_bytes), ...]   # per request
ratio = sum(b for _, b in pairs) / sum(t for t, _ in pairs)
budget = window - completion_reservation
death_line = budget * ratio
```

Then run `tools/diagnose.py` against a session and see how close you are.

If your ratio comes out very different from 3.44, that is not a contradiction —
it is the point. **The constant is local; the method is portable.**
