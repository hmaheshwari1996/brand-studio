# Brand registry and `brand.json` schema

Reference for `brand-kit` STEP 5 and for anyone editing a profile by hand.

Three parts:

1. [Directory layout](#directory-layout)
2. [`brand.json` field by field](#brandjson-field-by-field)
3. [Registry format](#registry-format) and [handing a brand to a teammate](#handing-a-brand-to-a-teammate)

Everything here is read through `scripts/lib/brandlib.py`. Import it; do not parse `brand.json`
yourself in a script.

---

## Directory layout

```
brands/
  _registry.json                 optional index; absent -> brandlib scans the directory
  <id>/                          directory name MUST equal brand.json "id"
    brand.json                   the profile — the only machine-read file
    GUIDELINES.md                human-readable design system (prose, never parsed)
    LEARNED.md                   dated log of corrections, append-only
    rules.local.json             machine-checkable learned rules
    assets/
      logos/                     png/svg logo variants
      fonts/                     ttf/otf, when licensing allows shipping them
      tokens.source.json         optional raw token dump the profile was derived from
    video/                       intro/outro/music/reference media
```

`brandlib.load_brand("<id>")` returns the parsed `brand.json` plus two keys it adds:

| Added key | Value |
|---|---|
| `learnedRules` | `rules.local.json` deep-merged in; always present, `{}` when there is no local file. A bare JSON array in that file lands at `learnedRules.rules`. |
| `_dir` | Absolute path of `brands/<id>`. Use it to resolve every relative asset path. |

The directory name is what `brand_dir()` builds from, so **`brands/acme/brand.json` must contain
`"id": "acme"`**. A mismatch resolves and then fails to load.

---

## `brand.json` field by field

Required-ness is as the builder and validator need it: **[req]** must exist, **[rec]** should exist
or downstream output degrades, **[opt]** is optional.

### Top level

| Field | Type | | Notes |
|---|---|---|---|
| `$schema` | string | [opt] | `"../_schema.json"`. Documentation only; nothing validates against it today. |
| `id` | string | [req] | Lowercase slug. Must equal the directory name. |
| `name` | string | [req] | Display name, used in footers and the confirmation card. |
| `aliases` | string[] | [rec] | Everything a user might type. Feeds fuzzy resolution — a missing alias is the usual cause of a wrong match. |
| `kind` | string | [opt] | `"house"` or `"client"`. |
| `description` | string | [rec] | One sentence: what the brand does and who the audience is. Copy generation reads this. |
| `version` | string | [rec] | Semver of the profile, not the brand. Bump on every durable edit. |
| `updated` | string | [rec] | `YYYY-MM-DD`. Shown in the confirmation card and in `list_brands()`. |
| `provenance` | object | [rec] | Free-form `{tokens, geometry, resolution}` strings saying where values came from and who ruled on conflicts. This is what separates a brand fact from a guess six months later. |
| `color` | object | [req] | See below. |
| `colorRules` | object | [req] | See below. |
| `type` | object | [req] | See below. |
| `logo` | object | [req] | See below. |
| `voice` | object | [req] | See below. |
| `video` | object | [rec] | Required before any video task. |
| `referenceDecks` | string[] | [opt] | Paths (relative to `_dir`) of approved sample decks. |
| `learned` | object | [opt] | `{file: "LEARNED.md", rules: "rules.local.json"}` — names the learning files. |

### `color`

| Field | Type | | Notes |
|---|---|---|---|
| `color.brand.<token>` | hex | [req] | The named identity colours: `blue`, `navy`, `mint`, … Whatever the brand calls them. |
| `color.<ramp>.<step>` | hex | [rec] | Ramps keyed `50`…`950`. Needed for hover, borders, washes, dark surfaces. Mark as derived in `GUIDELINES.md` when you interpolated them. |
| `color.neutral.<step>` | hex | [req] | Include `"0": "#FFFFFF"`. Backgrounds and text greys. |
| `color.semantic.<role>` | hex | [rec] | `success`, `warning`, `danger`, `info` and their `.bg` variants. |
| `color.gradient.<name>` | object | [opt] | `{stops: [hex, hex], angle: number}`. |
| `color.superseded.note` | string | [opt] | Why these are recorded. |
| `color.superseded.map` | `{hex: hex}` | [rec] | Retired hex → canonical replacement. The validator reports `COLOR.SUPERSEDED` with the replacement. |

Two things `palette_index()` deliberately **excludes**, so do not expect them back from it:
`color.superseded` (an unapproved hex must never win a nearest-token lookup) and
`colorRules.forbiddenText`. Use `superseded_map(brand)` and `forbidden_text_colors(brand)` instead.
Keys beginning with `$` are skipped everywhere, so `$comment` is safe to use.

On duplicate hexes the first path wins, so put the canonical name under `color.brand.*`:
`#0000FF` resolves to `brand.blue`, not `blue.600`.

### `colorRules`

| Field | Type | | Notes |
|---|---|---|---|
| `defaultText` | hex | [req] | Body text colour. Rarely `#000000`. |
| `secondaryText` | hex | [rec] | Captions, footers. |
| `forbiddenText` | `{hex: reason}` | [req] | Colours that must never carry text, each with the reason and the shade to use instead. State the measured contrast ratio in the reason. |
| `onSurface` | `{bgHex: fgHex}` | [req] | Text colour for each background the deck may use. `on_surface_text(brand, bg)` reads this. |
| `minContrastBody` | number | [req] | Usually `4.5`. |
| `minContrastLarge` | number | [req] | Usually `3.0`. |
| `largeTextPt` | number | [req] | Size at which "large" contrast applies. Usually `18.0`. |
| `gradientTextForbidden` | bool | [rec] | |
| `maxGradientsPerSurface` | int | [rec] | |
| `chartSeries` | hex[] | [rec] | Series colours **in order**. The chart builder indexes into this. |
| `chartGradientFillForbidden` | bool | [opt] | |

### `type`

| Field | Type | | Notes |
|---|---|---|---|
| `family` | string | [req] | Primary typeface. |
| `fallback` | string[] | [rec] | Stack for machines without the font. |
| `weightToPptxFamily` | `{"400": str, …}` | [req] | **The critical field.** PowerPoint has no weight axis — a semibold run is the family `"Poppins SemiBold"` with `bold = False`. Keys are stringified weights. `weight_to_family()` raises `ValueError` for any weight not listed. |
| `approvedWeights` | int[] | [req] | Must match the keys of `weightToPptxFamily`. |
| `forbiddenWeights` | int[] | [rec] | Documents the rejection reason in validator output. |
| `italicsAllowed` | bool | [rec] | |
| `syntheticBoldAllowed` | bool | [rec] | Keep `false`. Setting `bold = True` on a non-bold family is `TYPE.SYNTHETIC_BOLD`. |
| `deckScalePt.<role>` | object | [req] | `{size, leading, weight, tracking}`, plus `case` where it differs. Roles the grammar uses: `cover`, `section`, `title`, `subtitle`, `cardTitle`, `label`, `body`, `bodySmall`, `caption`, `eyebrow`, `footer`, `statNumber`. `type_role()` raises `KeyError` (listing available roles) for anything else. |
| `minBodyPt` | number | [req] | Floor for body copy. |
| `negativeTrackingAbovePt` | number | [opt] | Size above which negative tracking is allowed. |

### `logo`

| Field | Type | | Notes |
|---|---|---|---|
| `placement` | string | [req] | e.g. `"top-left"`. |
| `placementRationale` | string | [opt] | Useful when it contradicts an old template. |
| `variants.<id>` | object | [req] | `{file, use, aspect}`. `file` is relative to `_dir`. `aspect` is width/height — measure it with the logo probe, never guess, or the builder will stretch the mark. |
| `variantForBackground` | object | [req] | Maps `light`/`dark`/`photo`/`gradient` to a variant id. |
| `deckSizeIn` | `{w,h}` | [req] | On-slide size in header chrome. |
| `coverSizeIn` | `{w,h}` | [rec] | On-slide size on the cover. |
| `minWidthIn` | number | [rec] | |
| `clearSpaceRatio` | number | [rec] | With `clearSpaceBasis` describing what it is a ratio of. |
| `forbidden` | string[] | [rec] | Treatments that are never allowed. |
| `vectorAvailable` | bool | [rec] | With `vectorNote` for the caveat. |

### `voice`

| Field | Type | | Notes |
|---|---|---|---|
| `case` | string | [req] | `"sentence"` or `"title"`. |
| `caseExceptions` | string[] | [rec] | Roles exempt from `case`, typically `["eyebrow"]`. |
| `tense`, `voice` | string | [opt] | e.g. `"present"`, `"active"`. |
| `forbiddenChars` | string[] | [req] | `["!"]` for most brands. |
| `forbiddenPhrases` | string[] | [req] | Placeholder strings that must never ship. Seed from the brand's own template. `find_forbidden_phrases()` matches these. |
| `guidance` | string | [rec] | One paragraph the copy generator reads. |

### `video`

| Field | Type | | Notes |
|---|---|---|---|
| `resolution` | `{w,h}` | [req] | |
| `fps`, `container`, `vcodec`, `acodec` | | [req] | |
| `safeMarginPct` | number | [rec] | Title-safe margin. |
| `intro`, `outro` | object | [rec] | `{type: "generated"\|"file", durationSec, style, file}`. `file: null` means generate. |
| `music` | object | [rec] | `{enabled, file, targetLufs, duckUnderVoiceDb, fadeInSec, fadeOutSec, mood[], avoid[]}`. |
| `voiceover` | object | [opt] | `{enabled, engine, voice, rateWpm, targetLufs}`. |
| `captions` | object | [req] | `{enabled, required, burnIn, sidecar, font, sizePt, color, background, backgroundOpacity, position, bottomMarginPct, maxCharsPerLine, maxLines, minDurationSec}`. |
| `transition` | object | [rec] | `{type, durationSec}`. |
| `slideHoldSec` | `{min,default,max}` | [rec] | Bounds on scene `holdSec`. |
| `storyline` | object | [rec] | `{required, arc[], rules[]}`. `arc` values become scene `role`s in the video IR: `hook`, `problem`, `approach`, `proof`, `outcome`, `call-to-action`. |
| `referenceVideos` | string[] | [rec] | Paths relative to `_dir`. Empty array is a valid answer — but only after you have asked. |

### `rules.local.json`

Not part of `brand.json`; merged into `learnedRules` at load. Canonical shape is an object with a
`rules` array (a bare array also works and lands in the same place):

```json
{
  "rules": [
    {
      "id": "LOCAL.NO_MINT_TEXT",
      "kind": "forbid_color",
      "scope": "title",
      "value": "#41E7AB",
      "severity": "error",
      "rule": "Mint is decorative only; never a text colour.",
      "fix": "Use mint.700 #1B7A74, or navy #0F0A6C.",
      "added": "2026-07-30",
      "source": "user correction, session 2026-07-30"
    }
  ]
}
```

`kind` ∈ `forbid_text`, `require_text`, `forbid_color`, `min_font_size`, `max_font_size`,
`forbid_font_size`, `regex`. `scope` ∈ `any`, `title`, `body`, `eyebrow`. `severity` ∈ `error`,
`warn`, `info`. Prefix every `id` with `LOCAL.` so learned rules are distinguishable from built-in
ones in validator output. Full semantics are in [Learn protocol — full detail](#learn-protocol--full-detail).

---

## Registry format

`brands/_registry.json` is an **optional** index. When it is absent or malformed, `list_brands()`
scans `brands/*/brand.json` and synthesises the same rows, so the plugin works without it. When it
exists and yields at least one row with an `id`, it **wins** — a brand missing from a present registry
is invisible to resolution even if its directory exists.

Both shapes parse. Prefer the object form so you can carry a comment:

```json
{
  "$comment": "Index of brand profiles. Regenerate after adding or renaming a brand.",
  "version": "1.0.0",
  "brands": [
    {
      "id": "example",
      "name": "Example Brand",
      "aliases": ["channel play", "cp", "example technologies"],
      "updated": "2026-07-30"
    }
  ]
}
```

```json
[
  { "id": "example", "name": "Example Brand", "aliases": ["cp"], "updated": "2026-07-30" }
]
```

- The array may live under `brands`, `entries` or `items`.
- Rows without an `id` are dropped silently.
- Missing `name` falls back to `id`; a string `aliases` is wrapped into a list; missing `updated`
  becomes `""`.
- Only `id`, `name`, `aliases`, `updated` are read. Anything else is ignored — do not duplicate
  palette or type data here, it will drift.

**Keep it in sync.** After adding, renaming or deleting a brand, either regenerate the registry or
delete it and let the scan take over. A stale registry is worse than none: it hides new brands.
Regenerate with:

```sh
"$PY" - <<'EOF'
import sys, os, json, glob
ROOT = os.environ["ROOT"]
rows = []
for p in sorted(glob.glob(os.path.join(ROOT, "brands", "*", "brand.json"))):
    b = json.load(open(p))
    rows.append({"id": b.get("id") or os.path.basename(os.path.dirname(p)),
                 "name": b.get("name", ""),
                 "aliases": b.get("aliases", []),
                 "updated": b.get("updated", "")})
out = {"$comment": "Index of brand profiles. Regenerate after adding or renaming a brand.",
       "version": "1.0.0", "brands": rows}
path = os.path.join(ROOT, "brands", "_registry.json")
open(path, "w").write(json.dumps(out, indent=2) + "\n")
print("wrote %s (%d brands)" % (path, len(rows)))
EOF
```

Verify after writing — every brand must still resolve:

```sh
"$PY" -c "import sys;sys.path.insert(0,'$ROOT/scripts/lib');import brandlib as B;print([b['id'] for b in B.list_brands()])"
```

---

## Handing a brand to a teammate

A brand profile is portable if and only if `brands/<id>/` is self-contained.

**Checklist before you hand it over**

1. **No absolute paths anywhere in `brand.json`.** Every `file` value is relative to the brand
   directory. Grep for `/Users/`, `C:\` and `~` and fix what you find.
2. **Every referenced file exists.** Logos, fonts, intro/outro, music, reference media:

   ```sh
   "$PY" - <<'EOF' example
   import sys, os, json
   sys.path.insert(0, os.path.join(os.environ["ROOT"], "scripts", "lib"))
   import brandlib as B
   b = B.load_brand(sys.argv[1]); d = b["_dir"]; missing = []
   def check(p):
       if isinstance(p, str) and p and not os.path.isabs(p):
           if not os.path.exists(os.path.join(d, p)): missing.append(p)
       elif isinstance(p, str) and os.path.isabs(p):
           missing.append("ABSOLUTE PATH: " + p)
   for v in (b.get("logo", {}).get("variants") or {}).values(): check(v.get("file"))
   vid = b.get("video", {})
   for k in ("intro", "outro"): check((vid.get(k) or {}).get("file"))
   check((vid.get("music") or {}).get("file"))
   for p in vid.get("referenceVideos", []) or []: check(p)
   for p in b.get("referenceDecks", []) or []: check(p)
   for f in b.get("type", {}).get("fontFiles", []) or []: check(f)
   print("MISSING:", missing or "none")
   EOF
   ```
3. **Fonts.** If the licence does not allow redistribution, leave `assets/fonts/` empty, keep the
   family names and `type.fallback` in the profile, and say so in `provenance`. Do not ship licensed
   fonts to make a build work on someone else's machine.
4. **`LEARNED.md` travels with it.** It is the reason the profile looks the way it does. A profile
   without its learning history gets re-litigated within a week.
5. **`provenance` is filled in** — where the tokens came from, where the geometry came from, and who
   ruled on any conflict, with a date.
6. **Registry.** Either regenerate `_registry.json` on the receiving side or delete it and let the
   directory scan work.

**Smoke test on the receiving machine**

```sh
export ROOT=/path/to/brand-studio
"$PY" "$ROOT/scripts/brand_resolve.py" "<the name people type>"; echo "exit=$?"     # expect 0
"$PY" "$ROOT/scripts/lib/brandlib.py" --brand <id> | head -20                       # palette dumps
```

Exit 0 plus a palette dump means the profile is live. Anything else, work back through the checklist
above before building anything with it.

**What must not be copied between brands**

`grammar/deck-grammar.json`, `grammar/icons.json` and the icon set are shared and brand-agnostic —
they ship with the plugin, not with a brand. If a brand appears to need different geometry, that is a
plugin-owner conversation, recorded in `LEARNED.md` in the meantime. Never fork the grammar per brand.

---

## Writing a new profile (brand-kit STEP 5)

Write the collected intake answers to a scratch JSON file shaped like a **partial `brand.json`** —
same field names, same nesting. That file is the payload; keep it even if the CLI flags differ.

```sh
"$PY" "$ROOT/scripts/new_brand.py" --help                     # match the actual flags once
"$PY" "$ROOT/scripts/new_brand.py" --answers <scratch>/intake-<id>.json --id <id>
```

If the script is unavailable, write `brands/<id>/` by hand from the same payload — the schema above
is the contract, not the script. Never skip the profile and build from values held only in the
conversation.

Then:

1. Copy supplied logo, font and video assets into `brands/<id>/assets/` and `brands/<id>/video/`,
   and make every path in `brand.json` **relative to the brand directory**. No absolute paths — the
   profile has to survive being handed to a teammate.
2. Re-load and show the same summary card as STEP 3.
3. Sanity-check before you claim success:
   - `load_brand("<id>")` succeeds and `resolve_brand("<the name the user typed>")` returns
     `match: "exact"`.
   - Every `logo.variants[*].file` exists on disk.
   - Every text colour clears contrast on its intended background —
     `brandlib.passes_contrast(fg, bg, pt, bold)` returns `(passes, required, actual)` against WCAG
     AA defaults. If the brand set its own `colorRules.minContrastBody` / `minContrastLarge`, compare
     `brandlib.contrast_ratio(fg, bg)` against those instead.
   - `type.weightToPptxFamily` covers every approved weight.
4. Get final confirmation. If the user changes something now, it is an edit to `brand.json` (a
   durable fact), not a learned rule — see the decision table below.

---

## Learn protocol — full detail

Used by every skill in this plugin. **Whenever the user corrects the output** — a colour, spacing,
wording, structure, pacing, an icon choice, a scene length, anything — persist it *before* you
continue. The same correction must never be needed twice.

**1. Append a dated entry to `brands/<id>/LEARNED.md`.** Always. Even when you also do 2 or 3.

```markdown
## 2026-07-30 — Mint is not a heading colour

- **Correction:** user rejected mint `#41E7AB` on a slide title over white.
- **Why:** 1.6:1 contrast. Mint is decorative only; mint-family text uses `mint.700 #1B7A74`.
- **Scope:** example, all decks and videos, title and body text.
- **Persisted as:** `rules.local.json` rule `LOCAL.NO_MINT_TEXT`.
```

**2. If the correction is mechanically checkable, add a rule to `brands/<id>/rules.local.json`.**
Canonical shape is in [`rules.local.json`](#ruleslocaljson) above — an object with a `rules` array,
so brandlib's merge and a bare-array file agree. Rule semantics:

| `kind` | `value` | Fires when |
|---|---|---|
| `forbid_text` | string or list of strings | the string appears in text in scope (case-insensitive) |
| `require_text` | string | the string is absent everywhere in scope |
| `forbid_color` | hex | the colour is used in scope |
| `min_font_size` | number (pt) | text in scope is smaller |
| `max_font_size` | number (pt) | text in scope is larger |
| `forbid_font_size` | number or list | text in scope uses exactly that size |
| `regex` | pattern (+ `mode: "forbid"` \| `"require"`) | pattern matches / fails to match in scope |

`scope` is one of `any`, `title`, `body`, `eyebrow`. `severity` is `error`, `warn` or `info` — use
`error` only for things that must block a build. `id` must start with `LOCAL.`.

**3. If it is a durable brand fact rather than a preference, edit `brand.json` directly.**
"Our logo moved to top-left", "azure is retired", "body minimum is 11pt now" are facts. Bump
`version`, set `updated`, and add a line to `provenance` saying who ruled and when.

**4. Tell the user in one line which of the three you did.**

> Learned: mint is never a text colour — logged in `LEARNED.md` and added as `LOCAL.NO_MINT_TEXT`
> (error, scope title).

### Which of the three?

| The correction is… | Destination |
|---|---|
| A one-off wording change for this deck only | `LEARNED.md` only |
| A preference that a script can check ("never say Submit") | `LEARNED.md` + `rules.local.json` |
| A change to what the brand *is* (palette, logo placement, min size, fps) | `LEARNED.md` + `brand.json` |
| A disagreement between guidelines and a reference video | `LEARNED.md` + whichever of the other two the user's ruling implies |
| A layout/geometry complaint that applies to all brands | `LEARNED.md`, and say plainly that grammar changes need the plugin owner — do not edit `deck-grammar.json` |

After writing `rules.local.json`, re-load the brand to confirm it parses. A malformed local rules file
makes `load_brand` raise and takes every downstream skill with it:

```sh
"$PY" -c "import sys;sys.path.insert(0,'$ROOT/scripts/lib');import brandlib;b=brandlib.load_brand('<id>');print(len(b['learnedRules'].get('rules',[])),'learned rules ok')"
```

---

## Environment, non-negotiables and failure modes

```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"     # 3.9.6 + python-pptx, pillow, lxml
# ffprobe, ffmpeg, soffice are on PATH.
```

`scripts/lib/brandlib.py` is the loader, palette maths, contrast and report types. Import it; never
re-implement it. `brands/<id>/GUIDELINES.md` is human-readable prose and is never machine-read.

**Non-negotiables**

- **Never assume Example Brand.** It is the house brand, not the default. "Make a deck" with no brand
  named is ambiguous — ask.
- **Never proceed on an unconfirmed profile.** Resolution is a lookup, not consent.
- **Never auto-accept a fuzzy match** (exit 3). The guess is often wrong when one client's name is
  near another's: prefix hits floor at 0.9 and substring at 0.8, so "sam" scores the same against
  Samsung and Samvardhana. Treat exit 3 as a question, never as an answer.
- **Never edit `grammar/deck-grammar.json`** to accommodate a brand. Geometry is brand-agnostic and
  shared; brand differences live in `brands/<id>/brand.json`.
- **Always ask about reference videos and reference decks** — for existing brands too. This is the
  single most common source of "that isn't how our videos look".
- **Every correction gets persisted** before you move on.

**Failure modes**

| Symptom | Cause | Action |
|---|---|---|
| `BrandNotFound` on a brand you just wrote | directory name ≠ `id` in `brand.json` | rename the directory to match the id |
| `resolve_brand` returns `none` for the obvious name | alias missing | add it to `aliases`, re-resolve |
| Two brands both fuzzy-match at ~0.9 | shared prefix | never pick — show both and ask |
| Colours look right but validator reports `COLOR.SUPERSEDED` | asset was built from an old template | that is the point; replace with the canonical hex, do not add the old hex to the palette |
| User supplies fonts you cannot redistribute | licensing | store the family *name* and fallback stack in `brand.json`, leave `assets/fonts/` empty, and note it in `provenance` |
| Reference video is 4:5 or 9:16 | brand does vertical | that is a real brand fact — capture it, do not force 16:9 |
