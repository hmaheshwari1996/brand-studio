---
description: Tell the skill what to build tonight — queue tomorrow morning's decks, videos and reels
argument-hint: "[what you want, e.g. '5 decks for the FMCG pitch and one 90s video on coverage']"
---

This is where the day's work gets specified. The overnight run (`/brand-daily`) builds whatever is
queued here; `/brand-review` is how you review it in the morning. Your job in this command is to turn
what the user says into a **queue entry precise enough to build unattended at 2am**.

## 1. Brand

Resolve and confirm the brand (**brand-kit**), unless the user already named it and it is the one from
earlier in this session. Keep it to the compact card.

## 2. Capture the work

Read `$ARGUMENTS`. A day is a list of **jobs**, each of kind `deck`, `video` or `reel`. The user may
give you anything from "the usual" to a detailed brief for eight decks.

Fill the gaps with **AskUserQuestion**, but only where the answer genuinely changes the build. Ask in
one batch, not one at a time.

| Job kind | Must know before it can build unattended |
|---|---|
| `deck` | purpose (pitch / QBR / readout / capability / training), audience, the one decision you want from them, rough slide count, and the substance — the claims and numbers it must carry |
| `video` | runtime, format, the one thing it must land, and the proof behind it |
| `reel` | runtime, the hook, and the single payoff — a reel carries one idea only |

Two things to insist on, because the nightly run cannot ask:

- **The one thing.** Every job needs `oneThing` — what should change in the viewer's head. Without it
  the run produces something shaped like a deliverable with nothing to say.
- **Real substance.** Numbers, claims, client names, the actual argument. If the user does not supply
  it, ask where it comes from, or mark the job `needsInput` so the morning packet says so plainly
  rather than the run inventing figures. **Never let it fabricate proof.**

"The usual" is a legitimate answer — repeat yesterday's shape with the next queue topic, and say what
you assumed.

## 3. Write the entry

Append a day to `brands/<id>/content-queue.json` under `queue`:

```json
{ "id": "2026-08-01", "status": "pending", "jobs": [
  { "kind": "reel",  "durationSec": 15, "format": "vertical",  "topic": "...", "angle": "contrast",
    "oneThing": "...", "proof": { "claim": "...", "source": "..." } },
  { "kind": "video", "durationSec": 30, "format": "landscape", "topic": "...", "oneThing": "..." },
  { "kind": "deck",  "purpose": "pitch", "title": "...", "audience": "...", "slides": 12,
    "oneThing": "...", "brief": "...", "mustCarry": ["...", "..."] }
] }
```

Keep the existing file's `defaults`, `rules`, `evergreen` and `history` intact. Validate the JSON
before you finish — a malformed queue means the nightly run produces nothing and the morning is wasted.

## 4. Confirm, concretely

Show what is queued for that date: one line per job, and the estimated build time. Then say plainly
when it will run and that `/brand-review` is the morning step.

If the queue has fewer than three days of work ahead, say so. A starved queue falls back to evergreen
topics, which is a worse morning than a planned one.

## Also useful here

- Reordering or dropping a queued day — edit the same file.
- `--dry-run` tonight to see tomorrow's scripts before trusting them:
  ```bash
  ~/.cache/brand-studio/venv/bin/python "${CLAUDE_PLUGIN_ROOT}/scripts/daily_run.py" --dry-run
  ```
- If the user is describing a *type* of film they have not made before, that is a brainstorm, not a
  queue entry — use `/brand-brainstorm` first and queue the concept that wins.
