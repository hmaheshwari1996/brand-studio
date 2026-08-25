# Channelplay — learning ledger

The dated record of every decision, correction and exception that shaped this brand profile.
`brand.json` says what the brand *is*; this file says **why**, and who ruled on it. A profile
without its ledger gets re-litigated within a week.

**What goes in here.** Anything a person decided that the files alone do not explain: a correction
to generated output, a ruling between two conflicting sources, an exception granted for one client,
a preference about pacing or wording. One entry, one decision, one reason.

**How entries are added — the three-tier protocol.** Whenever the user corrects the output, persist
it *before* continuing. The same correction must never be needed twice. You may use more than one
tier, and tier 1 is not optional.

- **Tier 1 — this file. Always.** Append a dated entry, newest at the bottom. Do this even when you
  also do tier 2 or tier 3.
- **Tier 2 — `rules.local.json`,** when the correction is mechanically checkable ("never say Submit",
  "mint is never a text colour"). `brandlib.load_brand()` merges that file into `learnedRules` and
  both validators enforce every rule in it. Prefix the rule id with `LOCAL.`
- **Tier 3 — `brand.json`,** when the correction is a durable fact about what the brand *is*
  (palette, logo placement, minimum body size). Edit the profile, bump `version`, set `updated`, and
  add a line to `provenance` naming who ruled and when.
- **Then tell the user, in one line, which tier you used.** Every time.

A layout complaint that would apply to every brand is *not* a tier 3 edit. `grammar/deck-grammar.json`
is shared and brand-agnostic; record the complaint here and say plainly that geometry changes need
the plugin owner.

**How it is read back.** `scripts/brand_resolve.py` parses every `##` heading in this file and shows
the three most recent entries in the brand confirmation card, before anything is authored. The
`brand-kit`, `brand-deck` and `brand-video` skills read the whole file when the profile is loaded.
Two consequences worth respecting:

1. **`##` means "entry".** Do not use `##` (or `###`…) for section headings, or the parser will
   surface them as lessons. Structural prose goes in paragraphs like this one; code examples are
   indented four spaces rather than fenced, so a `##` inside an example is not mistaken for a heading.
2. **Every entry carries an ISO date in its heading.** Entries sort newest-first by that date. An
   undated entry drops the whole file back to file order.

Entry format:

    ## YYYY-MM-DD — One-line statement of the decision

    - **Scope:** deck | video | both
    - **Changed:** what is different now
    - **Why:** the reason, in one or two sentences
    - **Tier:** which tier(s) it was persisted at, naming the exact field or rule id
    - **Ruled by:** who decided

<!-- entries below this line -->

## 2026-07-30 — The design system palette wins over the master deck template's palette

- **Scope:** both
- **Changed:** `color.brand` is now the design system's set — blue `#0000FF`, navy `#0F0A6C`, mint
  `#41E7AB`, teal `#29AFA7`, sky `#0194DD`, azure `#2F80ED`, tint `#EBF6F9`. The template's
  `#0000D5` family (`#0000D5`, `#0029E3`, `#0036AA`, `#0F237B`, `#00006B`, `#272525`, `#08F8B9`,
  `#0094DE`, `#E90C29`) is recorded in `color.superseded.map` against its canonical replacement.
- **Why:** the two sources disagreed. The design system is maintained and versioned; the deck
  template is a document that drifted. Superseded is not the same as forbidden — those hexes are
  real, they exist in circulated files, and the validator has to be able to name them and say what
  they should have been. Deleting them would have made an old deck merely "off-palette" instead of
  "using the retired template blue, which is `#0000FF`".
- **Tier:** 3 — `brand.json` `color.brand` and `color.superseded.map`; `provenance.resolution`
  records the ruling. Reported by the validator as `COLOR.SUPERSEDED`.
- **Ruled by:** brand owner.

## 2026-07-30 — The master deck template supplies geometry only

- **Scope:** both
- **Changed:** `grammar/deck-grammar.json` was extracted from Channelplay Deck Template 3.pptx and
  carries **no colour and no font family** — only positions, sizes, spacing, capacities and
  structural rules. The template's Office theme (Aptos, the stock Office accent colours) was
  discarded, not imported. `build_deck.py` paints every visual property explicitly on every shape so
  that nothing is ever inherited from a theme.
- **Why:** the template's theme is what PowerPoint put there, not what the brand decided. Treating
  it as brand would have made "Aptos" and the Office accent ramp into Channelplay assets. Geometry
  is the part of that file worth keeping, and geometry is the part that is not brand-specific — which
  is what lets one layout engine serve every client.
- **Tier:** 3 — `provenance.geometry` and `provenance.resolution` in `brand.json`. Consequence for
  new brands: a new brand starts from this same shared geometry with its own tokens layered on. It
  does not get a copy of the grammar, and the grammar is never edited to accommodate a brand.
- **Ruled by:** brand owner.

## 2026-07-30 — The logo sits top-left, overriding the master template

- **Scope:** both
- **Changed:** `logo.placement` is `top-left`. The header chrome in the grammar places the mark at
  x 0.869in, y 0.300in. The master deck template had it top-right; that placement is not used.
- **Why:** section 3 of the design system rules the logo top-left in headers, and top-left is where
  the eye starts on a left-aligned layout — the template's top-right mark competed with the eyebrow
  for the same corner. One placement, applied everywhere, is worth more than the merits of either.
- **Tier:** 3 — `brand.json` `logo.placement`, with the reason recorded in `logo.placementRationale`
  so this is not re-argued from the template.
- **Ruled by:** brand owner.

## 2026-07-30 — Confirm the brand, and ask about reference material, before authoring anything

- **Scope:** both
- **Changed:** every session must resolve the brand and get the user to confirm the resolved profile
  before a single slide or line of script is written, and must ask whether sample or reference
  videos exist — for brands that already have a profile, not only for new ones. A fuzzy match is
  never auto-accepted. `video.referenceVideos` is `[]` for Channelplay: that is a recorded answer to
  a question nobody has asked yet, not a statement that none exist.
- **Why:** Channelplay owns this repository, which makes it the easiest wrong answer in the plugin —
  a deck built *for* a client is often governed by the client's guidelines, not the agency's.
  Resolution is a lookup, not consent. And the most common complaint about a generated film is
  "that isn't how our videos look", which is nearly always a reference video that existed and was
  never asked for.
- **Tier:** 1 — this file, plus the workflow itself: `brand-kit` STEP 3 (confirm) and STEP 4g
  (reference material), restated in `brand-deck` and `brand-video`.
- **Ruled by:** brand owner.

## 2026-07-30 — The opening line is too long, cut it to under eight words

- **Scope:** reel (reel)
- **Changed:** added `LOCAL.TITLE_MAX_7_WORDS`
- **Why:** morning review note on the 2026-07-30 packet, category *copy*: “the opening line is too long, cut it to under eight words”
- **Tier:** 2 — `rules.local.json` rule `LOCAL.TITLE_MAX_7_WORDS` (regex, scope title, warn)
- **Ruled by:** brand owner, morning review 2026-07-30

## 2026-07-30 — Never use the word solutions

- **Scope:** deck, reel, video (all)
- **Changed:** added `LOCAL.NO_SOLUTIONS`; mirrored 'solutions' into `voice.forbiddenPhrases`
- **Why:** morning review note on the 2026-07-30 packet, category *copy*: “never use the word solutions”
- **Tier:** 2 — `rules.local.json` rule `LOCAL.NO_SOLUTIONS` (forbid_text, scope any, warn); 3 — `brand.json` `voice.forbiddenPhrases`. Video coverage: validate_video.py does not read learnedRules, so the tier 2 rule alone would be enforced on decks only.
- **Ruled by:** brand owner, morning review 2026-07-30

## 2026-08-25 — Learned rules are enforced on video, not decks only

- **Scope:** both
- **Changed:** `validate_video.py` now reads `learnedRules` and checks the text kinds
  (`forbid_text`, `require_text`, `regex`) across `title`, `eyebrow`, `body` and `any`, using the
  same rule vocabulary as the deck validator. `forbid_color` and the font-size kinds are reported as
  `info` on video rather than skipped — colour is already covered by `VIDEO.OFF_PALETTE` from sampled
  frames, and a film exposes no type sizes to inspect. `build_video.py` now writes each scene's
  template `data` into the timeline sidecar, without which a rule scoped to `title` or `eyebrow`
  would have inspected nothing on a rendered film.
- **Why:** the 2026-07-30 `LOCAL.NO_SOLUTIONS` entry recorded this gap in its own Tier line and
  nothing acted on it. Every tier 2 correction made for a reel or an explainer was inert: written
  down, reported to the user as enforced, and never checked. That is the precise failure the
  three-tier protocol exists to prevent.
- **Tier:** plugin code, not a brand tier — `scripts/validate_video.py`, `scripts/build_video.py`.
- **Ruled by:** brand owner, session 2026-08-25

## 2026-08-25 — A learned rule can name the formats it applies to

- **Scope:** both
- **Changed:** `rules.local.json` rules accept an optional `formats` list — `deck` or `video` for the
  artifact kind, or a delivery format from `brand.video.formats` (`landscape`, `square`, `vertical`).
  Omitted means the rule applies everywhere, so every rule written before the field is unaffected. A
  misspelled value is reported as `info` and the rule stays active everywhere: a typo must never
  silently disable a rule. `LOCAL.TITLE_MAX_7_WORDS` is now `"formats": ["vertical"]`.
- **Why:** the rule was recorded on 2026-07-30 with **Scope: reel**, but the schema had no way to
  express that, so switching video enforcement on applied a reel rule to deck titles. It warned on
  two slide titles that read perfectly well at eight and nine words. The ledger had already scoped
  the rule correctly; the schema simply could not carry it.
- **Tier:** 2 — `rules.local.json` (`LOCAL.TITLE_MAX_7_WORDS`), plus the shared
  `brandlib.rule_applies()` both validators consult.
- **Ruled by:** brand owner, session 2026-08-25

## 2026-08-25 — The forbid_text coverage mirror is retired

- **Scope:** both
- **Changed:** `review.py` no longer copies a `forbid_text` ban into `brand.json`
  `voice.forbiddenPhrases` when the note is about a film. The `--no-mirror` flag and the
  `mirror_forbidden_phrase()` function are gone. Existing entries in `voice.forbiddenPhrases` —
  including `solutions`, mirrored on 2026-07-30 — are **left exactly as they are**: they record a
  ruling that was actually made, and retiring the mechanism is not a reason to rewrite the record.
- **Why:** three reasons, only the first of which is redundancy. (a) The mirror existed solely
  because `validate_video.py` could not read `learnedRules`, which it now can. (b) It reported a
  voice ban under `CONTENT.PLACEHOLDER`, an id that means template scaffolding leaked into a client
  artifact — so the report said something untrue about what went wrong. (c) It quietly promoted a
  `warn` rule to an `error` and wrote a tier 3 brand fact from a tier 2 decision, neither of which
  anyone ruled on. A ban that should block a build is a rule with `"severity": "error"`, which video
  now honours.
- **Known consequence:** `solutions` is still banned at both tiers, so a film using it reports twice
  — `LOCAL.NO_SOLUTIONS` at warn and `CONTENT.PLACEHOLDER` at error. One legacy phrase, and no new
  ones can appear now the mirror is gone; deduplicating for a single historical case was judged not
  worth the code.
- **Tier:** plugin code — `scripts/review.py`.
- **Ruled by:** brand owner, session 2026-08-25
