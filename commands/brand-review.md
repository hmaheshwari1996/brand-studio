---
description: The morning review. Watch last night's artifacts, give notes, and turn every note into an enforced rule so the same note is never given twice.
argument-hint: "[date YYYY-MM-DD] [brand id]"
---

# /brand-review

Last night built the work. This morning decides whether tomorrow is cheaper. Applying today's note
is the easy half; **converting it into something the validator enforces is the point.**
```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"
```

## 1. Briefing
```sh
"$PY" "$ROOT/scripts/review.py" show          # add --date / --brand from $ARGUMENTS
```
One line per artifact — a day can be one reel, or eight decks, or a mix. Exit 1 means no packet: say
so and stop; never invent a review.

## 2. Watch them. Actually watch them.

Open every contact sheet **and** every mp4/pptx under `files`; Read the sheets yourself so you can
talk about what is on screen. **A contact sheet is not a substitute for watching a 15-second reel.**
It is a grid of stills: it cannot show pacing, a voiceover clipped mid-word, a caption a beat late,
or a transition that lands wrong — most of what a reviewer notices. Fifteen seconds of watching is
cheaper than one wrong note. Cannot play a file? Say so, and mark anything time-based unverified.

## 3. Collect the notes conversationally

Walk the numbered prompts from `show`. Let the user talk. Record **each** note as it arrives, in
their words, not your paraphrase:
```sh
"$PY" "$ROOT/scripts/review.py" note --text "the hook takes too long to land" --target reel
```
`--target` takes a job id (`deck-3`), a kind (`deck` = every deck that day), `both` (reel+video) or
`all`. **Scope it honestly** — a deck note must never be recorded against a video; when the user is
vague about which artifact, ask.

Each `note` prints its category and the tier it will land in. Mis-filed? Pass `--category
copy|pacing|visual|structure|audio|caption|other`. Reported *tier 1, judgement* where you expected a
rule? It carries no concrete value — ask for one ("under how many words?", "which phrase?").

## 4. Apply, and rebuild only what the notes touched
```sh
"$PY" "$ROOT/scripts/review.py" apply --rebuild
```
Only named artifacts rebuild; a caption-wording change rewrites the `.srt` and reuses the encode.
Read the rebuild lines back verbatim — rebuilt, reused, anything that returned errors. Exit `2` means
a rebuilt artifact fails validation: fix the IR and rerun, never ship it. `apply` will not invent
copy — a rewrite the user did not dictate means editing the packet IR yourself, then `--force`.

## 5. State the tier for every note, one line each, exactly as `apply` reported it

> `n1` pacing → tier 1, ledger only (judgement). `n2` copy → tier 2 `LOCAL.NO_SOLUTIONS`
> (forbid_text, any) **plus** tier 3 `voice.forbiddenPhrases`, because the video validator does not
> read learned rules.

Tier 1? Say why it could not be mechanised. Never claim enforcement you do not have. Nothing to
change is the goal state, and it is counted: `review.py accept`.

## 6. Weekly: is the burden falling?
```sh
"$PY" "$ROOT/scripts/review.py" metrics --days 30
```
Report the headline verdict **as printed, including when it is bad** — "flat, notes are not becoming
rules" is the most useful sentence this system produces. Then name the top repeat row and propose
mechanising it. Full system and honest limits: `skills/brand-video/references/daily-loop.md`.
