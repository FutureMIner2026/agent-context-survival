# Agent Context Survival

**Where long agent conversations actually die — measured, not guessed.**

Most people believe the wall is the model's context window. It usually isn't.
In a proxied setup, the wall comes earlier: a **messages budget** enforced by the
proxy, plus a fixed per-token byte cost. We crashed conversations on purpose and
measured both.

## The numbers we measured

| Quantity | Value | How we got it |
|---|---|---|
| Model window | 1,048,576 tokens | 6 real `CONTEXT_WINDOW_EXCEEDED` responses |
| Completion reserve | 256,000 tokens | constant in every error |
| **Proxy messages budget** | **792,576 tokens** | 3 fail-fast 502 responses |
| **Bytes per token** | **3.44** | 3,126 paired (outbound bytes ↔ upstream input) |
| Folding reclaim per pass | 9.9% / 12.7% / 24.7% | proxy fold ledgers |
| Growth per turn | ~800–1,400 tokens | 6 sessions, median positive delta |

## The consequence

```
death line (bytes) = 792,576 x 3.44 ≈ 2.60 MiB of outbound messages
```

If your outbound crosses that line, the request is rejected — regardless of how
much "1M window" you think you have left.

And a second, subtler result: **folding quality decides who survives.**
One session folded 32 passes and reclaimed ~2% each time — it grew to 2.5x the
window and died. Another reclaimed 10–25% per pass and ran all day. Same model,
same config. The difference was whether folding *actually* reclaimed anything.

## Why this repo exists

Because "use less context" is not advice, and "1M tokens" is not a budget.
We publish the measurements so you can compute **your** death line instead of
guessing, and check whether your folding is silently failing.

## Tools

### `tools/diagnose.py` — how close is this conversation to dying?

```bash
python tools/diagnose.py my_session.jsonl.zstd
python tools/diagnose.py ./sessions/          # rank every session by risk
```

It decompresses the session, counts only the text that would actually be sent
back (skipping streaming replay), converts to tokens, and tells you where you
stand against the 792,576 messages budget — plus the `1.9x + 15,000` figure to
use for a session that has never been folded.

Standard library only; `pip install zstandard` if your sessions are `.zstd`.

## What's here

- `docs/death-formula.md` — the full measurement: samples, method, formula, caveats
- `tools/` — small cross-platform scripts (diagnose a session, estimate outbound)

## Scope and honesty

- The measurements come from **one** proxy implementation. The *shape* of the
  formula is general (window − reserve = usable messages; bytes/token is a
  constant per language mix), but **the constants are ours** — measure yours.
- We do not publish other people's data. Every sample referenced is synthetic or
  our own, and the crash messages are quoted as the API returned them.

## License

MIT
