---
description: Run the full brand-kit intake for a new brand and write a validated brand profile.
argument-hint: "<brand name>"
---

# /brand-new

Create a new brand profile from scratch: interview, write, verify, confirm. Everything the answers
cover becomes an enforced rule for every deck and video built for this brand, so the interview is the
product — the script is just the typist.

```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"
```

Brand: `$ARGUMENTS`

Use the **brand-kit** skill for this. It owns the intake, and `skills/brand-kit/references/intake.md`
has the full question bank with good and bad example answers. This command is the entry point and the
checklist; the skill is the method. If `$ARGUMENTS` is empty, ask for the brand name first.

---

## 1. Make sure it is actually new

```sh
"$PY" "$ROOT/scripts/brand_resolve.py" "$ARGUMENTS"; echo "exit=$?"
```

| Exit | Meaning | Do this |
|---|---|---|
| `0` | Exact hit — the brand already exists | Show the summary card and ask whether they want to **update** it (that is `/brand-learn`, tier 3) rather than create a duplicate |
| `3` | Fuzzy hit | Name the candidate and its score. "Samsung India" vs "Samsung" is a real distinction — make the user choose, never auto-accept |
| `4` | No match | Genuinely new. Continue |

Two profiles for one brand is the worst outcome here: half the decks get validated against the wrong
one and nobody notices for a quarter.

## 2. Settle identity, then interview

Agree the **id slug** before anything else — lowercase, hyphenated, stable, and short:
`acme-retail`, not `Acme Retail Pvt Ltd`. It becomes the directory name, the CLI argument and the
token prefix in every artifact from here on. Renaming it later means rewriting every deck's brand
property.

Then run the intake in **small `AskUserQuestion` batches — at most four questions per call**, in this
order, echoing back what you captured after each group so mistakes surface early. Never send one wall
of questions.

**If the user has a brand guidelines deck or PDF, take it.** Extraction beats interrogation: read it,
fill in what you can, and confirm the values back rather than asking blind.

| Group | Must come out of it |
|---|---|
| **a) Identity** | Display name · id slug · aliases people will actually type · what the brand does · audience · tone in three adjectives · house brand or client brand |
| **b) Colour** | Primary, secondary, accent, text, surface **as hex** · gradients (stops + angle) · colours explicitly *not* approved, and what each became (these become `color.superseded.map`) · which colours may carry text and which are decorative only |
| **c) Type** | Typeface · approved weights · **the PPTX family name each weight maps to** (600 → "Poppins SemiBold", with `bold` staying False) · italics allowed or not · minimum body pt · fallback stack · whether font files can go in `assets/fonts/` |
| **d) Logo** | Files for light, dark and photographic backgrounds · placement · minimum width · clear space · what is forbidden (recolour, stretch, effects, redraw) · vector or raster |
| **e) Voice** | Sentence or title case · where case is exempt (eyebrows are usually upper) · banned words and placeholder strings that must never ship · **whether exclamation marks are allowed** · tense and active/passive · one line of guidance |
| **f) Video kit — mandatory** | Intro/outro (files, or the style and duration to generate) · music (tracks, mood, what to avoid, target LUFS, duck depth) · captions (burned-in or sidecar, position, font, size, colours, max chars per line) · the narrative arc the brand's videos follow |
| **g) Reference material — always ask** | "Do you have sample or reference videos?" and "Do you have sample or reference decks?" Record paths in `video.referenceVideos` and `referenceDecks` |

Do not skip **f** because today's task is a deck. The profile is shared, and the next request will be
a video.

Get **hexes, not colour names**. "Our blue" is not a value. If you are handed a swatch image or a
PDF, extract the values and read them back for confirmation.

If reference material is supplied, analyse it — the procedure is in the brand-kit skill under
*Analysing reference material* (ffprobe for container/fps/loudness, sampled frames mapped onto the
palette, a frame with on-screen text for captions). Report every place the reference disagrees with
the stated guidelines, in one table, and ask which wins. Record the ruling either way; the same
conflict reopens next month otherwise.

**Anything you were not told stays unanswered.** The writer marks unanswered fields
`"$unverified": true` so the validator can warn "this profile is incomplete" instead of confidently
enforcing a rule nobody agreed to. Inventing a brand rule is worse than having none — never fill a
gap with a plausible guess.

## 3. Write the profile

Write the answers to a scratch JSON file shaped like a partial `brand.json` — same field names, same
nesting — then hand it over. Check the flags once; they are the contract, not this document:

```sh
"$PY" "$ROOT/scripts/new_brand.py" --help
"$PY" "$ROOT/scripts/new_brand.py" --id <slug> --from-json <scratch>/intake-<slug>.json
echo "exit=$?"
```

Add `--dry-run` first if you want to see what it would write without touching disk.

| Exit | Meaning |
|---|---|
| `0` | Written |
| `1` | Bad usage or internal failure |
| `2` | **The answers were refused** — a colour did not parse as hex, the weights are unusable, or the brand's own body text fails contrast on its own background. Fix the answer with the user; do not force it through |

Exit 2 is the script doing its job. A profile whose body text fails contrast on its own surface
would poison every deck built from it.

It creates:

```
brands/<slug>/brand.json
brands/<slug>/LEARNED.md          seeded with the entry format
brands/<slug>/rules.local.json    {"rules": []}
brands/<slug>/assets/logos/       drop logo files here
brands/<slug>/assets/fonts/       drop font files here
brands/<slug>/video/              music beds, intro/outro plates, reference cuts
brands/_registry.json             upserted, never rewritten
```

## 4. Assets and paths

Copy every supplied logo, font and video asset into `brands/<slug>/assets/` and `brands/<slug>/video/`,
then make every path in `brand.json` **relative to the brand directory**. No absolute paths — the
profile has to survive being handed to a teammate or committed to the repo.

## 5. Verify before claiming success

Run all four. A profile that loads is not the same as a profile that works.

```sh
"$PY" -c "import sys;sys.path.insert(0,'$ROOT/scripts/lib');import brandlib;\
b=brandlib.load_brand('<slug>');\
r=brandlib.resolve_brand('$ARGUMENTS');print(r['match'], r.get('score'))"
```

1. `load_brand("<slug>")` succeeds, and `resolve_brand("<the name the user typed>")` returns
   `match: "exact"`. If the user's own words do not resolve to their brand, add the alias now.
2. Every `logo.variants[*].file` exists on disk. A `[MISSING]` variant means a deck archetype that
   needs it cannot be built — say so before approval, and either get the file or record the
   constraint in `LEARNED.md`.
3. Every text colour clears contrast on its intended background —
   `brandlib.passes_contrast(fg, bg, pt, bold)`. If the brand set its own
   `colorRules.minContrastBody` / `minContrastLarge`, compare `brandlib.contrast_ratio(fg, bg)`
   against those instead.
4. `type.weightToPptxFamily` covers every approved weight, and no mapping relies on synthetic bold.

Then build one throwaway deck and validate it. It is the only honest proof the profile is usable:

```sh
"$PY" "$ROOT/scripts/build_deck.py" --ir <tiny-ir.json> --out /tmp/<slug>-smoke.pptx
"$PY" "$ROOT/scripts/validate.py" /tmp/<slug>-smoke.pptx --format human; echo "exit=$?"
```

A three-slide IR (cover, title-body, closing) is enough — decks under three slides fail
`STRUCTURE.TOO_FEW_SLIDES` on their own.

## 6. Confirm and hand off

Show the summary card the resolver prints — brand and aliases, hexes with measured contrast, chart
series, superseded count, type family with its PPTX weight names, logo placement with an on-disk
check per variant, the video kit, the storyline arc, the learned-rule count:

```sh
"$PY" "$ROOT/scripts/brand_resolve.py" <slug>
```

Ask for approval explicitly. "Yes" or an amendment are the only acceptable answers — silence or
moving straight to deck content is not approval. Anything the user changes now is an edit to
`brand.json` (a durable fact), not a learned rule.

Close in one line, then continue:

> Active brand profile: **Acme Retail** (`acme-retail`), confirmed. Ready for brand-deck.

Pass the **id**, never the name or the profile contents, to `brand-deck` or `brand-video`. Both
re-load from disk, so an id stays correct if the profile changes mid-session.
