---
description: Generate a copyright-free music bed for a film, or a voiceover in English or a client's language.
argument-hint: "music 30s confident | voice 'the line to speak' [--lang hi] | voices"
---

# /brand-audio

The audio half of a film: a **music bed** synthesised from music theory, and a **voiceover** from a
local neural TTS. Both run offline, both write a sidecar JSON the video builder reads.

```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"
```

Request: `$ARGUMENTS`

---

## Flow A — a music bed

```sh
"$PY" "$ROOT/scripts/make_music.py" --duration 42 --brand example --out bed.wav --json
```

`--duration` is the **finished film length**; the bed lands on it to within 50 ms with the last chord
resolving on the root, so ask for the real number rather than rounding. Mood defaults from
`brand.video.music.mood`/`avoid`; override with `--mood confident|calm|urgent|warm|neutral|uplifting`,
browse with `--list-moods`. `--stems` writes pad/bass/arp/perc separately for a human to rebalance.
`--seed` makes it reproducible — same seed and parameters, byte-identical file.

**Say this once, when you first produce a bed:** it is generated from scratch — a key, a mode, a chord
progression and additive synthesis — so nothing is sampled, nothing is licensed, they own it outright.
It is also an **underscore bed, not a produced track**: no real instruments, no melody anyone hums.
Right under narration, wrong on its own. Do not oversell it.

## Flow B — a voiceover

```sh
"$PY" "$ROOT/scripts/make_voice.py" --text-file script.txt --brand example --out vo.wav --json
"$PY" "$ROOT/scripts/make_voice.py" --text-file script-hi.txt --lang hi --out vo-hi.wav --json
```

Default engine is **Piper** — local neural TTS, genuinely human-sounding, 49 languages. Each language
is a **one-time ~63 MB model download**, stored in `~/.cache/brand-studio/voices/`, never in the repo.
It never downloads on its own: without `--allow-download` a missing model fails with the exact install
command. Install deliberately:

```sh
"$PY" "$ROOT/scripts/make_voice.py" --list-voices
"$PY" "$ROOT/scripts/make_voice.py" --install-voice hi_IN-pratham-medium
```

macOS `say` is the fallback and it is already installed. It is lower quality, and it also covers seven
languages Piper has no model for at all — **Tamil, Kannada**, Japanese, Thai, Malay, Croatian,
Lithuanian. `--engine auto` picks Piper when a model is present, `say` when it is not, and prefers
`say` for `--lang en_IN` because Piper has no Indian-English model and the accent matters more.

Numbers are expanded before synthesis — "4,200" becomes "four thousand two hundred", "62%" becomes
"sixty two percent". Check `normalizedText` in the sidecar; that is what was actually spoken.
`--ssml-lite` honours `[pause 400]` and `[emph]...[/emph]`; without it those markers are stripped, not
read aloud.

## Then

Report the paths, the measured LUFS and duration from each sidecar, the mood and progression for
music, and the engine/voice/effective wpm for VO. **The VO sidecar's `duration` is the real one** —
scene holds and captions are timed from it, never from the word-count estimate.

Full reference, mood parameters, language coverage table and limits:
`skills/brand-video/references/audio.md`.
