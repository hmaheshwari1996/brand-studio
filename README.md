# brand-studio

A Claude Code plugin that builds and **validates** brand-compliant PowerPoint decks and videos.

Two halves, and the second half is the point:

- **Build.** A skill writes an intermediate JSON document — what the deck says, or what the film
  says — and a Python builder turns it into a native, editable `.pptx` or a rendered `.mp4` plus
  captions. Nothing is a screenshot; every shape in the deck is a real shape.
- **Validate.** A second script opens the finished artifact and measures it against the brand's own
  profile: colour, type, logo, contrast, layout overflow, copy rules, structure, captions, loudness.
  It emits machine-readable violations and a non-zero exit code. A hook runs it automatically, so
  the loop closes whether or not anyone remembers to check.

A personal project by [hmaheshwari1996](https://github.com/hmaheshwari1996) — built to make
AI genuinely useful for graphic design work, rather than a source of things a designer then has to
check by hand. Not a Channelplay product; Channelplay is one of the brands it ships a profile for.

Many brands, one engine. The layout engine is shared; the brand tokens are per-brand. Adding a brand
is a data change, not a code change.

---

## Requirements

| | Why | Install |
|---|---|---|
| macOS or Linux | `say` voiceover is macOS-only; everything else is portable | — |
| Python 3.9+ with working `xml.parsers.expat` | `python-pptx` | usually `/usr/bin/python3` |
| `python-pptx`, `pillow` | deck build and validation, image probing | `scripts/bootstrap.sh` |
| `ffmpeg`, `ffprobe` | video render and inspection | `brew install ffmpeg` |
| `soffice` (LibreOffice) | deck previews, slide frames for deck-videos | `brew install --cask libreoffice` |
| Google Chrome / Chromium | headless rendering of icons and motion scenes | `brew install --cask google-chrome` |
| Poppins TTFs | shipped in `brands/channelplay/assets/fonts/` | installed by `bootstrap.sh` |

No third-party Python beyond `python-pptx` and `pillow`. Everything runs from a dedicated virtualenv
at `~/.cache/brand-studio/venv` so it cannot collide with a project's own environment.

---

## Install

This repository **is** a plugin marketplace — `.claude-plugin/marketplace.json` sits beside
`plugin.json` — so a teammate installs it with two commands and updates it with `git pull`.

### Sharing it with the team (recommended)

Each teammate runs these **in the Claude Code terminal** — start it with `claude` in a shell. `/plugin`
is a terminal-only command; in the desktop or web app it answers *"/plugin isn't available in this
environment."*

```
/plugin marketplace add hmaheshwari1996/brand-studio
/plugin install brand-studio
```

> **Two names, and they are not the same.** You *add* the marketplace by its **repo path**
> (`hmaheshwari1996/brand-studio`). Once added you *refer* to it by the **name in its manifest**,
> which is `channelplay` — so updates are `/plugin marketplace update channelplay`. Typing
> `channelplay/brand-studio` fails, because no such repo exists.

and once, in a terminal:

```sh
~/.claude/plugins/marketplaces/channelplay/scripts/bootstrap.sh
```

To ship an update, commit and push. Teammates pick it up with `/plugin marketplace update channelplay`.

> **Why a shared remote beats passing a zip around.** This plugin learns: `LEARNED.md` and
> `rules.local.json` accumulate every correction anyone gives it. With copies, each person's learning
> stays on their laptop and the same note gets given five times. With a shared remote, one review
> becomes a commit and the brand gets sharper for everyone.

### From a local folder (no remote)

Good for trying it before publishing, and it works from a shared drive:

```
/plugin marketplace add /path/to/brand-studio
/plugin install brand-studio
```

### From a zip (last resort)

Only when the recipient has no git access. They lose updates and have to repeat this every time:

```sh
git archive --format=zip -o brand-studio.zip HEAD   # 3.7 MB, excludes build output
# recipient: unzip somewhere permanent, then /plugin marketplace add <that path>
```

> **On size.** The working folder reads ~28 MB in Finder, but ~23 MB of that is one rendered test
> video under `examples/out/`, which `.gitignore` excludes. The actual payload is **3.7 MB across 172
> files**. Piper voice models (~120 MB) and the Python venv live in `~/.cache/brand-studio/` and are
> deliberately outside the repo — `bootstrap.sh` fetches what each machine needs.

### Then set up the machine

`bootstrap.sh` is idempotent and safe to re-run:

```sh
/path/to/brand-studio/scripts/bootstrap.sh
```

`bootstrap.sh` probes every candidate interpreter by importing what the plugin actually needs rather
than trusting a version number, creates the venv, installs the two dependencies, checks `ffmpeg`,
`ffprobe`, `soffice` and headless Chrome, installs the bundled Poppins faces, and prints a PASS/FAIL
table. It exits non-zero if anything essential failed. `--force` rebuilds the venv, `--no-fonts`
skips the font install.

Confirm the install:

```
/brand-check
```

That reports the environment, the brands it can resolve, and — given a file — validates it. If it
comes back clean, you are ready.

---

## What else comes with it

Installing brand-studio also brings **Task Observer**, and offers two more
plugins alongside it. The split is deliberate.

**Bundled — arrives enabled, no extra step**

| | |
|---|---|
| `skills/task-observer` | Watches how you work and turns friction into durable skill improvements. Vendored unchanged from [rebelytics/one-skill-to-rule-them-all](https://github.com/rebelytics/one-skill-to-rule-them-all), by Eoghan Henn, under CC BY 4.0. See `skills/task-observer/ATTRIBUTION.md`. |

It is bundled rather than referenced because it is a **skill**, not a plugin —
there is no `.claude-plugin/` in its repository, so a marketplace entry cannot
point at it. Do not edit the vendored copy: fixes belong upstream, and a local
change diverges silently from a source still being maintained.

Point it at a stable observation-log path in your `CLAUDE.md` before relying on
it. A path resolved from the working directory dies with the first git worktree
you delete.

**Referenced — one command each, not installed for you**

| | |
|---|---|
| `claude-mem` | Memory across sessions. [thedotmack/claude-mem](https://github.com/thedotmack/claude-mem) |
| `claude-code-setup` | Anthropic's plugin that reads a codebase and recommends the hooks, skills, subagents and MCP servers worth adding. |

```
/plugin install claude-mem@channelplay
/plugin install claude-code-setup@channelplay
```

These are **referenced, never copied**. Both are full plugins with their own
release cadence, their own authors and their own licences; a vendored copy would
fork them the day it was made and quietly stop receiving fixes. Referencing also
means installing stays *your* decision — a marketplace entry is an offer, not an
install.

## Use

### Skills

Skills are invoked by Claude automatically, from what you ask for. You do not name them.

| Skill | Triggers on | What it does |
|---|---|---|
| **brand-kit** | any deck, video or brand-asset request; "add a new brand"; "update our colours" | Resolves and **confirms** which brand profile governs the work. Runs first, always. Nothing else proceeds on an unconfirmed brand. |
| **brand-deck** | "build a deck", "make slides", "a QBR / proposal / readout", "brand-check this deck" | Storyline → Deck IR → `.pptx` → validate → look at it. Loops until zero errors. |
| **brand-video** | "make a video / explainer / showreel", "add voiceover or captions" | Script → Video IR → `.mp4` + `.srt` → validate → watch it. Loops until zero errors. |

`brand-deck` and `brand-video` both invoke `brand-kit` first. That is not ceremony: a deck built
*for* a client is often governed by the client's guidelines, not the agency's, and copy written for
32pt navy titles does not survive being re-skinned to a brand whose titles are 24pt. The brand has to
be settled before the first slide is written.

### Commands

| Command | Use it for |
|---|---|
| `/brand-studio [section]` | Explain the plugin — what it does, how a job runs, what the validator enforces, which brands exist. Reads live state, so it never drifts from the code. `brief` for ten lines, `features` for what it does well. |
| `/brand-check [file]` | Validate a `.pptx` or `.mp4` on demand, or — with no file — report the environment and the brands available. This is also the post-install smoke test. |
| `/brand-learn <correction>` | Persist a correction so it is enforced from now on. See [How it learns](#how-it-learns). |
| `/brand-new [name]` | Start intake for a brand that does not exist yet. See [Adding a brand](#adding-a-brand). |

**The daily loop** — the plugin is built to run a production day unattended:

| Command | Use it for |
|---|---|
| `/brand-queue [what you want]` | Evening: say what to build tonight — decks, videos, reels, any mix. |
| `/brand-daily` | Night: builds every queued job, validates, leaves a review packet. `--dry-run` to see tomorrow's output tonight. |
| `/brand-review` | Morning: what was made, your notes, applied and rebuilt — then it learns. |

**Making things**

| Command | Use it for |
|---|---|
| `/brand-brainstorm [what it's for]` | Think through a film, reel or animation *before* there is a brief — concepts, arcs, openings. |
| `/brand-texture` | Turn a reference photo (a swatch, a fabric, a paint) into a display type treatment. The letterforms stay the brand face; only the fill and edges come from the photo. Display only, never body. |
| `/brand-cgi` | Render a branded 3D product shot or film. CPU rendering — a hero still is cheap, a full film is an overnight job. |
| `/brand-audio` | Generate a copyright-free music bed, or a voiceover in another language. |

To render a deck to PNGs and actually look at it, run `scripts/render_preview.sh <file.pptx>`.

Everything a command does is also a script you can run directly — see
[Scripts](#scripts-the-layer-underneath). The commands exist so the common paths are one line.

---

## The enforcement loop

Compliance is not a review step at the end. It is wired into the tool loop.

```
        write .pptx / .mp4
                │
                ▼
      PostToolUse hook ──► scripts/validate.py ──► validate_deck.py
                │                                  validate_video.py
                ▼
        exit 0 │ exit 2 │ exit 1
         pass  │  errors│ internal failure
                │
                ▼
      Stop gate: the turn cannot end while the
      most recent artifact still has errors
```

**PostToolUse.** Any time a `.pptx`, `.potx`, `.mp4`, `.mov` or `.m4v` is written, the hook runs
`scripts/validate.py` against it. The dispatcher picks the right specialist by extension, attaches
any sidecars it finds on disk (`<name>.ir.json`, `<name>.timeline.json`, `<name>.srt`) after probing
the child's `--help` to confirm it accepts them, and proxies the child's stdout and exit code
verbatim. Anything that is not a deck or a video exits 0 silently, so unrelated work is never
blocked.

**Stop gate.** The turn does not end while the artifact just produced still reports errors. This is
what stops the familiar failure: a deck is generated, a violation is mentioned in passing, and the
conversation moves on.

**Severities.** The distinction is the whole design:

| Severity | Exit | Meaning |
|---|---|---|
| `error` | 2 | Blocking. The artifact does not ship. Fix the IR and rebuild. |
| `warn` | 0 | Not blocking, but every warning left in place needs a stated reason. "The client's legal name really is title case" is a reason. "It is only a warning" is not. |
| `info` | 0 | Context — an unverified brand field, a malformed learned rule. |

Never hand-edit the `.pptx` or the `.mp4` to clear a violation. The IR is what the artifact is
regenerated from; the next build overwrites the fix and the violation returns.

**Overriding it.** In order of preference:

1. **Fix it.** Almost always cheaper than the alternative.
2. **Keep the warning, and say why.** Warnings never block. Justified warnings get named in the
   final report. A warning you keep repeatedly is not a warning, it is a learned rule — promote it
   (see [How the plugin learns](#how-the-plugin-learns)).
3. **Turn the hooks off for the session** via Claude Code's `/hooks` menu, or by disabling the
   plugin. There is deliberately no per-file bypass flag: a bypass flag becomes the default within
   a fortnight, and then the validator is decoration.

An `error` that is genuinely wrong for this brand is a bug in the brand profile, not a reason to
override. Correct `brand.json` and record why in `LEARNED.md`.

---

## Adding a brand

```
/brand-new
```

Intake asks in groups of at most four questions, echoing back what it captured after each group.
It prefers extraction over interrogation: hand it a guidelines PDF or deck and it pulls the values
out and turns the interview into a confirmation.

| Group | It asks for |
|---|---|
| a. Identity | Display name, lowercase id, aliases people actually type, one sentence on what the brand does and who the audience is, tone, house or client |
| b. Colour | Primary / secondary / accent **hexes**, default text colour, surfaces, gradients, which colours may carry text and which are decorative only, hexes that appear in old files but are not approved, chart series order |
| c. Type | Typeface, approved weights, the exact PPTX family name per weight, the size scale, minimum body size |
| d. Logo | Variant files, placement, on-slide sizes, clear space, what is forbidden, whether vector exists |
| e. Voice | Sentence or title case, banned characters, placeholder phrases that must never ship, one paragraph of guidance |
| f. Video | Resolution and fps, intro/outro, music and loudness, captions, the storyline arc — **and whether reference videos exist** |

What lands on disk:

```
brands/<id>/
  brand.json          the profile — the only machine-read file
  GUIDELINES.md       human-readable design system (prose, never parsed)
  LEARNED.md          dated ledger of decisions, seeded with a header
  rules.local.json    {"rules": []} — machine-enforceable learned rules
  assets/logos/       drop the logo files here
  assets/fonts/       drop the TTF/OTF files here (only if licensing allows)
  video/              intro/outro plates, music beds, reference cuts
brands/_registry.json upserted, never rewritten from scratch
```

Two things worth knowing before you start.

**Unanswered questions become neutral defaults stamped `"$unverified": true`, not invented rules.**
The validator then reports "this profile is incomplete" instead of confidently enforcing something
nobody agreed to. Inventing a brand rule is worse than having none. `brand_resolve.py` surfaces
every unverified field in the confirmation card.

**Colour is checked, not trusted.** Every value must parse as a hex; every foreground/background pair
is measured against WCAG AA and failures are reported. If the profile's own body text fails contrast
on its own background, `new_brand.py` refuses to write it at all — that is not a warning, it is a
defect that would poison every deck built from the brand.

`brands/_schema.json` is the field-by-field schema, with the required-ness and the reasoning on every
property. Read it before editing a profile by hand.

---

## How the plugin learns

When you correct the output, the correction is persisted *before* the work continues. The same
correction must never be needed twice. Three tiers, and more than one may apply.

| Tier | File | Use when |
|---|---|---|
| 1 | `brands/<id>/LEARNED.md` | **Always.** A dated entry: what changed, why, scope, which tier it was persisted at. |
| 2 | `brands/<id>/rules.local.json` | The correction is mechanically checkable. Merged into `learnedRules` at load; both validators enforce every rule in it. |
| 3 | `brands/<id>/brand.json` | The correction is a durable fact about what the brand *is*. Bump `version`, set `updated`, add a line to `provenance` naming who ruled. |

Then it tells you, in one line, which tier it used.

A layout complaint that would apply to every brand is **not** a tier 3 edit. `grammar/deck-grammar.json`
is shared; geometry changes go through the plugin owner, and the complaint is recorded in `LEARNED.md`
in the meantime.

### A correction becoming a permanent rule

> **You:** the heading on slide 4 is mint. We never set text in mint.

**Tier 1** — an entry appended to `brands/channelplay/LEARNED.md`:

```markdown
## 2026-07-30 — Mint is not a heading colour

- **Scope:** both
- **Changed:** mint #41E7AB and teal #29AFA7 are decorative only, never text.
- **Why:** 1.6:1 and 2.7:1 on white. Mint-family text uses mint.700 #1B7A74.
- **Tier:** 1 here, 2 as LOCAL.NO_MINT_TEXT.
- **Ruled by:** user correction, session 2026-07-30.
```

**Tier 2** — a rule appended to `brands/channelplay/rules.local.json`:

```json
{
  "id": "LOCAL.NO_MINT_TEXT",
  "kind": "forbid_color",
  "scope": "title",
  "value": ["#41E7AB", "#29AFA7"],
  "severity": "error",
  "rule": "Mint and teal are decorative only; neither carries text.",
  "fix": "Use mint.700 #1B7A74, or navy #0F0A6C.",
  "added": "2026-07-30",
  "source": "user correction, session 2026-07-30"
}
```

**Tier 3** — because this is a fact about the brand rather than a preference, `colorRules.forbiddenText`
in `brand.json` gains both hexes with the measured ratio as the reason, `version` is bumped and
`updated` is set.

**The result.** Every future build for this brand fails with
`LOCAL.NO_MINT_TEXT / error / slide 4 title / found #41E7AB / expected any approved brand colour`
before anyone sees the deck. The correction is now infrastructure. The rule ids are prefixed
`LOCAL.` so a learned rule is always distinguishable from a built-in one in the report.

`rules.local.json` ships with a `$comment` documenting every supported rule kind and a `$examples`
array with a worked example of each. The validator ignores anything under a `$` key.

---

## Architecture

```
grammar/deck-grammar.json          brands/<id>/brand.json
  geometry, archetypes,              colour, type, logo,
  capacities, structure              voice, video kit
  BRAND-AGNOSTIC                     BRAND-SPECIFIC
            │                                │
            └───────────────┬────────────────┘
                            ▼
                   Deck IR / Video IR          ← what to say (authored by a skill)
                            │
                            ▼
              build_deck.py / build_video.py    ← the only thing that writes artifacts
                            │
                            ▼
                    .pptx  /  .mp4 + .srt
                            │
                            ▼
            validate_deck.py / validate_video.py ← reads the same two inputs
```

**Three inputs, and the builder owns none of them.**

| Input | Answers | Lives in |
|---|---|---|
| Grammar | *Where does it sit?* Canvas, grid, 18 archetypes, region rectangles, capacities, structural rules | `grammar/deck-grammar.json` — one file, every brand |
| Brand profile | *What does it look like, and how does it read?* | `brands/<id>/brand.json` |
| IR | *What does it say?* | authored per job |

**Why grammar and brand are separate files.** One layout engine serves every client brand. The
geometry was extracted from a real master deck and then stripped of everything brand-specific — it
carries no colour and no font family at all. That means a new client is a `brand.json` and a folder
of logos, not a fork of the layout code; a fix to the icon-grid layout reaches every brand at once;
and the same deck can be re-skinned by swapping one argument. It also means the validator can check
two independent things — "is this on-brand?" from the profile, "is this well-made?" from the grammar
— and tell you which one you broke.

The corollary is a rule: **the grammar is never edited to accommodate a brand.** If a brand appears
to need different geometry, that is a plugin-owner conversation.

**Nothing is inherited from the Office theme.** `build_deck.py` paints every visual property
explicitly on every shape on every run. The theme inside the default `python-pptx` template is not
anyone's brand, and a value that arrives by inheritance is a value nobody chose.

### Repository layout

```
.claude-plugin/     plugin manifest
commands/           slash commands
hooks/              PostToolUse validation, Stop gate
skills/             brand-kit, brand-deck, brand-video (+ their reference docs)
scripts/            builders, validators, brand tooling, bootstrap
  lib/brandlib.py   loader, palette maths, contrast, report types — import it, never re-implement
grammar/            deck-grammar.json, icons.json, 86 tintable SVG icons
brands/
  _schema.json      JSON Schema for brand.json
  _registry.json    index; delete it and a directory scan takes over
  channelplay/      the house brand
templates/video/    intro.html, outro.html, scene.html — brand-agnostic motion templates
examples/           worked Deck IR and Video IR
```

### Scripts, the layer underneath

Everything is runnable by hand with `--help`. `PY=~/.cache/brand-studio/venv/bin/python`.

| Script | Does |
|---|---|
| `bootstrap.sh` | Environment setup and the PASS/FAIL table |
| `brand_resolve.py` | Name → profile. Exit `0` exact, `3` fuzzy (must be confirmed), `4` no match |
| `new_brand.py` | Intake answers → `brands/<id>/` |
| `build_deck.py` | Deck IR → `.pptx` |
| `build_video.py` | Video IR → `.mp4` + `.srt` + timeline sidecar |
| `validate.py` | Dispatcher by extension; what the hook calls |
| `validate_deck.py` | `.pptx` → violations. Pass `--ir` to make the structure checks authoritative |
| `validate_video.py` | `.mp4` → violations, sampling frames for the pixel checks |
| `render_preview.sh` | `.pptx` → one PNG per slide |
| `render_icon.py` | Tinted PNG from the shared icon set, cached per icon+colour+size |
| `asset_cache.py` | Inspect or clear a brand's generated intro/outro. `--list`, `--invalidate <kind>` |
| `explain.py` | What `/brand-studio` prints. `--brief`, `--section <name>`, `--json` |

Validator output is a frozen contract:

```json
{ "target": "deck.pptx", "brand": "channelplay", "kind": "deck", "pass": false,
  "counts": { "error": 1, "warn": 3, "info": 0 },
  "violations": [ { "id": "COLOR.SUPERSEDED", "severity": "error", "where": "slide 4 / title",
                    "found": "#0000D5", "expected": "#0000FF",
                    "rule": "Template colours are superseded by the design system palette.",
                    "fix": "Use brand.blue #0000FF." } ] }
```

`--format human` prints the same thing readably. Violation ids are namespaced `COLOR`, `TYPE`,
`LOGO`, `LAYOUT`, `CONTENT`, `VOICE`, `STRUCTURE`, `A11Y`, `VIDEO`, `AUDIO`, `CAPTION`, plus `LOCAL.`
for learned rules.

---

## Known limitations

Read this section before promising anything to a client.

| Limitation | What it means in practice |
|---|---|
| **The validator checks rules, not taste.** | It cannot see that slide 7 argues the opposite of slide 4, that the chart does not support the claim in its title, or that the deck is boring. A clean report is a floor, not an endorsement. Look at the rendered slides. Watch the film. |
| **Channelplay logos are raster only.** | 1982px wide PNGs, no SVG or EPS. Fine to 1920px and for on-screen decks; request vector from the brand owner before print or large format. |
| **`say` voiceover is a review voice.** | The macOS synthesiser fluffs Indian place names, retail jargon and unfamiliar acronyms. Good enough to agree pacing and script; not good enough to deliver. Budget a human or commercial TTS read, and label `say` cuts so nobody circulates one by accident. |
| **Photographic frames trip the palette check.** | `VIDEO.OFF_PALETTE` samples pixels, and a photograph of a real store is full of colours no brand owns. Expect warnings on photo-heavy films, triage them rather than ignoring them, and say in the report that you did. The same applies to full-bleed photo slides in a deck. |
| **No music ships with the plugin.** | `music.enabled` is true and `music.file` is null for Channelplay: the brand wants a bed and has none cleared. Films render voice-only until a licensed track is supplied. |
| **No reference videos are recorded.** | `video.referenceVideos` is `[]` because nobody has been asked, not because none exist. This is the most common cause of "that isn't how our videos look". |
| **Font rendering depends on the local machine.** | The deck names families like `Poppins SemiBold`. On a machine without them installed, PowerPoint substitutes, and metrics shift. `bootstrap.sh` installs the bundled faces; a recipient's machine is not covered. |
| **Text fit is estimated, not measured.** | Overflow checks use per-family glyph-width averages, not a real text engine. A tolerance is applied, and a borderline block can pass here and still look tight in PowerPoint. |
| **macOS-first.** | Voiceover needs `say`. Everything else runs on Linux, but that path is less travelled. |
| **A stale registry hides brands.** | If `brands/_registry.json` exists and lists at least one brand, it wins over the directory scan — a brand missing from it is invisible even though its folder is there. Regenerate it after adding or renaming a brand, or delete it and let the scan work. |
