---
description: Run the unattended nightly producer now, or set it up to run at 2am so the day's videos and decks are waiting in the morning.
argument-hint: "[--dry-run] [--only reel|video|deck|both|all] [--date YYYY-MM-DD] [--queue-item ID] [--force]"
---

# /brand-daily

The **night half** of the daily loop: takes the next pending day off `brands/<id>/content-queue.json`,
builds every job it asks for, and leaves a review packet in `brands/<id>/daily/<date>/`.
`/brand-review` is the morning half.

```bash
~/.cache/brand-studio/venv/bin/python "${CLAUDE_PLUGIN_ROOT}/scripts/daily_run.py" $ARGUMENTS
```

`daily_run.py` **never prompts.** It runs with nobody watching: every decision comes from the queue
and the brand profile, every external call has a timeout, and a run that cannot produce anything
still writes a packet saying why. Do not wrap it in a confirmation step.

## Check tomorrow's output tonight

```bash
~/.cache/brand-studio/venv/bin/python "${CLAUDE_PLUGIN_ROOT}/scripts/daily_run.py" --dry-run
```

Prints the item it would pick, every script with word counts, the template per beat and the computed
timeline — and writes nothing. Run it after editing `content-queue.json`.

## Schedule it

Cron has almost no `PATH`, so give it one — the pipeline needs ffmpeg, Chrome and LibreOffice:

```cron
0 2 * * * PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin ~/.cache/brand-studio/venv/bin/python ~/Documents/GitHub/brand-studio/scripts/daily_run.py >> ~/Library/Logs/brand-daily.log 2>&1
```

One line per night lands in that log:
`2026-07-31 example: reel ok (14.5s), video ok (28.9s), 0 violations, 9m04s, intro+outro reused [ok]`

Claude Code's own scheduled-task tooling runs it just as well — ask for a scheduled task running the
command above daily at 02:00 if you want the run in a session rather than a log file. On a laptop
that sleeps prefer `launchd` with `StartCalendarInterval`: it catches up a missed run, cron does not.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | every job delivered and passed validation |
| 1 | internal failure — nothing was delivered |
| 2 | delivered, but a job failed to build or failed validation |
| 3 | nothing to build — the queue is empty and evergreen is exhausted |

0 and 2 leave a reviewable packet and mark the item done. 1 and 3 leave it **pending** to retry.

## What to do with the result

Report the one-line summary, the packet path, and anything in `packet.json` with a non-null `error`
or a `notes` entry. Do not re-narrate `concept.md` or `script.md` — the human reads those. On exit 3
the queue has run dry: show what is left and offer `/brand-brainstorm`. On exit 1 read `run.log` in
the packet directory and report the cause.

## Notes

- A queue entry is a **day** holding any mix of `reel`, `video` and `deck` jobs. They build
  concurrently (`--jobs`, default 4; films capped tighter by `--video-jobs`, default 2), and one job
  failing never sinks the day — everything that succeeded is still delivered.
- `--force` rebuilds an item already done, ignores `noRepeatWithinDays`, and overwrites the packet.
- The item is marked done, and `history` appended, only when something was delivered **and** the
  whole day was built — so testing one job with `--only` never consumes the rest.
