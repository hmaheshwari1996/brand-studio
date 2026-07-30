# The daily loop

Two halves that meet at a directory on disk.

**Night** — `scripts/daily_run.py`, driven by `/brand-daily`. Takes the next queue item and builds
whatever the owner asked for: a reel, a 90-second film, five decks for five different requirements,
or any mix. Writes a review packet and stops.

**Morning** — `scripts/review.py`, driven by `/brand-review`. The owner watches, gives notes, and
every note is converted into something durable. Then it measures whether the notes are actually
getting fewer.

The second half is the one that matters. Fixing today's video is table stakes; **turning today's note
into a rule so it is never given again** is the whole design. If the note count is not falling, the
loop is decorative, and `review.py metrics` is built to say so out loud.

    content-queue.json ──► nightly run ──► daily/<date>/packet.json ──► morning review
            ▲                                                                │
            └──────────── LEARNED.md · rules.local.json · brand.json ◄────────┘

---

## The queue

`brands/<id>/content-queue.json`.

| Key | What it is |
|---|---|
| `defaults` | Per-kind build defaults — format, `durationSec`, pipeline, beats. A day's jobs inherit these. |
| `rules` | `reelWordBudget`, `videoWordBudget`, `noRepeatWithinDays`, `requireProof`. Enforced by the runner so a script cannot overrun its slot. |
| `queue` | The dated pipeline. Each item: `topic`, `angle`, `oneThing`, `proof`, `status`. |
| `evergreen` | Fallbacks used when the queue is empty. The packet is then marked `fallback: true`. |
| `history` | What has already run — the anti-repeat memory. |

**Keep 5–10 items ahead.** A starved queue does not fail loudly; it quietly falls back to evergreen,
and evergreen topics repeat, and repetition is what makes a daily feed look automated. Restock when
`queue` drops below five: the fastest source is the last week's review notes, because a note about
what was missing is a topic.

The queue is **not video-only**. A queue item can name any mix of artifacts for a day; the review
side reads whatever the packet actually contains and never assumes a video exists.

---

## The packet

    brands/<id>/daily/<YYYY-MM-DD>/
      packet.json      the manifest — the only file the review side treats as contract
      concept.md       the angle, in prose
      script.md        the words, as approved
      review.md        the human-readable briefing
      validation.json  validator output for the day
      run.log          what the nightly run did
      <jobId>.ir.json  the IR each artifact was built from   ← rebuilds edit THIS
      <jobId>.mp4 | .pptx   the artifact
      <jobId>.srt           captions, for films
      <jobId>.contact.png   contact sheet
      notes.json       written by the morning review

`packet.json.artifacts` is a **map keyed by job id** — `reel`, `video`, `deck-1`, `deck-2`, … Each
entry carries its own `kind` (`reel` | `video` | `deck`), `path`, `validation`, `error`, and either
`durationSec` or `slides`. Iterate it; never index it positionally, never assume two entries, never
assume one of them is a video.

`status` is `awaiting-review` until the morning review closes it, then `reviewed`. That is how
`review.py show` finds the right day with no arguments.

---

## The morning review

```sh
PY="$HOME/.cache/brand-studio/venv/bin/python"
"$PY" scripts/review.py show                       # the briefing, under 40 lines
"$PY" scripts/review.py note --text "…" --target reel
"$PY" scripts/review.py apply --rebuild
"$PY" scripts/review.py metrics --days 30          # weekly
"$PY" scripts/review.py accept                     # zero-note day
```

**`--target` is a scope, not a label.** It takes a job id (`deck-3`), a kind (`deck` = every deck
built that day), `both` (reel + video), `all`, or a comma-separated list. A note about a deck must
not reach a video: the wrong scope is how a rule learned from one artifact starts firing on
everything and gets switched off a fortnight later.

**Watch the artifacts.** The contact sheet exists to make a still comparison cheap, not to replace
playback. Pacing, a clipped voiceover, a caption landing a beat late and a transition that fights
the cut are all invisible in a grid of stills, and all four are things a reviewer notices in the
first ten seconds. Fifteen seconds of watching is cheaper than one wrong note applied to a rule.

---

## The three tiers

Every note gets tier 1. Tiers 2 and 3 are the ones that stop it recurring.

| Tier | Destination | Enforced by | Use for |
|---|---|---|---|
| 1 | `LEARNED.md` | a human reading it | **always** — plus judgement that no script can check |
| 2 | `rules.local.json` | `validate_deck.py` | a mechanically checkable preference |
| 3 | `brand.json` (or `content-queue.json`) | both validators, and the builders | a durable fact about what the brand *is* |

`apply` picks one destination for the machine-readable part and always writes tier 1. Order of
precedence: a **brand fact** beats a rule (change the fact and let the built-in checks enforce it —
a local rule that duplicates a brand fact double-reports).

**Tier 2 rules stay inside the seven kinds the validator implements** — `forbid_text`,
`require_text`, `forbid_color`, `min_font_size`, `max_font_size`, `forbid_font_size`, `regex`. Rule
ids are prefixed `LOCAL.` so learned rules stay distinguishable in validator output. An unknown kind
is not a crash, but it is not a check either: it is reported as an `info` violation and enforces
nothing. Never invent a kind.

Word-count notes ("the hook must be under eight words") become a `regex` rule, because there is no
length kind: `^\W*(?:\S+\s+){7,}\S+` matches any string of eight words or more, scoped to `title`.

### The coverage gap, stated plainly

`validate_deck.py` reads `learnedRules`. **`validate_video.py` does not.** A tier 2 rule is therefore
enforced on decks and nothing else. That would make "never say *solutions*" silently unenforced on
exactly the artifact the note was about — so when a `forbid_text` note applies to a day containing a
reel or a video, `apply` also writes the phrase into `brand.json` `voice.forbiddenPhrases`, which
**both** validators read, and says so in its tier line. The cost is that a deck may report the same
word twice, once per mechanism. That is the right trade: a double report is noise, an unenforced ban
is a note you will give again next week. `--no-mirror` opts out.

Video-only concerns — pacing, loudness, caption timing — cannot be expressed as a local rule at all.
They belong in `brand.json` under `video`, or in `LEARNED.md` as guidance. Say which one you did.

---

## Rebuilds

`apply --rebuild` regenerates **only** what the notes touched, by kind:

- **deck** → edit `<jobId>.ir.json`, re-run `build_deck.py`, re-validate.
- **reel / video** → edit the IR, re-run `build_video.py`, re-validate.
- **caption text only** → rewrite the `.srt` in place. The mp4 is reused, not re-encoded. A wording
  fix in a caption is a text edit; paying for a full render to make it is the single most wasteful
  thing this system could do.

Everything else is reported as reused, by name. `review.py` derives edits deterministically and
**will not invent copy** — it acts on an explicit `replace "X" with "Y"`, a `… say "Y" instead`, or
an explicit hold in seconds. Anything else prints *nothing rebuilt* and says why. When a note needs
a rewrite the reviewer did not dictate, an author edits the IR and re-runs with `--force`.

Exit `2` means a rebuilt artifact now fails validation. Fix the IR; never patch the mp4 or the srt.

---

## Reading the metrics

`review.py metrics` writes `brands/<id>/daily/metrics.json` and prints five things.

1. **Notes per day**, as bars plus a sparkline. Days with a packet count; days without one are
   skipped, not counted as zero. A reviewed day with no notes counts as a real zero — that is the
   goal state and it must show up in the average.
2. **7-day rolling average vs the previous 7**, in words. *"3.1 notes/day, down from 5.4 — the loop
   is working"* or *"flat — notes are not becoming rules."* Report it as printed. A flat trend is
   the most useful output this system produces and softening it wastes the whole apparatus.
3. **By category**, with a last-7 vs prev-7 arrow. A category that never improves is a category
   whose notes are all landing in tier 1.
4. **By artifact kind.** *"Deck notes are falling but video notes are flat"* points straight at the
   coverage gap above — video notes mechanise less well, so they recur more.
5. **Enforcement and repeats.** How many notes became rules or brand facts versus ledger-only, the
   current rule count, and the notes given on two or more separate days. **A repeat is a rule you
   have not written yet**; the repeat table is the shortest path to a lower note count.

Under ~40% mechanised is the number to worry about. It is the arithmetic reason the same note keeps
coming back.

---

## When the nightly run fails

`packet.json` still exists with `status: failed`, or an artifact carries a non-null `error`. Then:

1. Read `run.log` first. A build failure names its own cause far better than a rerun will.
2. `show` still works — it prints `BUILD-FAIL` on the affected rows. Review whatever *did* build;
   half a packet is still worth an opinion.
3. Do not re-run the whole night to fix one artifact. Rebuild the one that failed from its IR.
4. If nothing built, the queue item is still marked pending — the runner only advances on success.
   Fix the cause, run it again, and leave the review for when there is something to watch.
5. A packet with no artifacts is not a review. Say the night failed, and stop.

---

## Honest limits

- **The classifier is keyword matching, and it will mis-file notes.** It is deterministic on purpose
  — the same note always lands in the same category, no model call, no drift — but "the opening felt
  flat" lands in *other*, and a note mixing two concerns lands under whichever scores higher.
  `--category` overrides it. Treat the category breakdown as a rough shape, not a measurement.
- **The metric is gameable, and the easiest way to game it is to say less.** Notes per day falls
  just as neatly when the reviewer gives up as when the output improves. Nothing in this repo can
  tell those apart. Guard it by watching the *enforcement* percentage next to the trend: a real
  improvement shows falling notes **and** a rising share of notes that became rules. Falling notes
  with flat enforcement usually means fatigue, not learning.
- **Mechanisability is conservative.** A note with no concrete value in it produces no rule, by
  design. Guessing a threshold the reviewer never stated creates a rule that fires on innocent work
  and gets disabled, which is worse than no rule. If something should be mechanised, ask for the
  number.
- **A rule nobody tested does not exist.** After `apply`, validate an artifact that still contains
  the mistake and confirm the `LOCAL.*` id appears. A wrong `scope` is the usual reason it does not.
- **Rules accumulate and nothing prunes them.** A `LOCAL.` rule is forever until someone deletes it.
  Read the list every few weeks; a rule that has not fired in a month is either won or wrong.
