---
description: Persist a brand correction so the same mistake is never made twice, then prove the new rule fires.
argument-hint: "<what the user corrected>"
---

# /brand-learn

A correction that lives only in the conversation is a correction you will need again next month.
This command routes one correction to the right of three places, then re-validates the most recent
artifact to prove the rule actually fires.

```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"
```

Correction: `$ARGUMENTS`

If that is empty, use the most recent correction the user made in this conversation and **state what
you understood it to be** before writing anything. Do not invent a rule from a preference the user
never expressed.

---

## 1. Which brand?

Learned rules are per brand. Get the id from the active session, or resolve it:

```sh
"$PY" "$ROOT/scripts/brand_resolve.py" --list
```

If the correction applies to a brand that has no profile yet, stop and run `/brand-new` first.

## 2. Which tier?

Pick exactly one destination for the machine-readable part. `LEARNED.md` is written in every case.

| The correction is… | Tier | Destination |
|---|---|---|
| A one-off wording change for this artifact only | 1 | `LEARNED.md` only |
| A preference a script can check ("never say Submit", "mint is never text") | 2 | `LEARNED.md` + `rules.local.json` |
| A change to what the brand *is* (palette, logo placement, min body size, fps, captions) | 3 | `LEARNED.md` + `brand.json` |
| A layout/geometry complaint that would apply to every brand | 1 | `LEARNED.md`, and say plainly that grammar changes need the plugin owner — **never edit `grammar/deck-grammar.json`** to suit one brand |

When it is genuinely both a fact and a checkable preference, tier 3 wins: change the fact, and let
the built-in validators enforce it. A local rule that duplicates a brand fact double-reports.

**NOT EVERY CORRECTION IS A BRAND FACT.** Before writing anything, ask what the correction is
actually correcting. When a check fails, one of three things is broken — the artifact, the rule, or
the CHECKER's implementation of the rule — and only the first two belong here. A workaround for a
checker bug recorded as a brand rule freezes that bug into the profile, where nobody will ever
connect the two again: the rule outlives the bug, and every future artifact is shaped by a defect
that was fixed years earlier. Symptoms to stop on: the correction makes the artifact worse for its
real audience (alt text, reading order, contrast, semantics), or it exists only to stop a specific
validator complaining rather than because someone looked at the output and disliked it. Report the
checker bug instead — name the file and the predicate — and record nothing.

## 3. Write it

### Tier 1 — always

Append to `brands/<id>/LEARNED.md`. Append; never rewrite the file.

```markdown
## 2026-07-30 — Mint is not a heading colour

- **Correction:** user rejected mint `#41E7AB` on a slide title over white.
- **Why:** 1.6:1 contrast. Mint is decorative only; mint-family text uses `mint.700 #1B7A74`.
- **Scope:** example, all decks and videos, title and body text.
- **Persisted as:** `rules.local.json` rule `LOCAL.NO_MINT_TEXT`.
```

Date, correction, reason, scope, where it was persisted. The reason is the part that matters — a
rule whose rationale is lost gets argued about again.

### Tier 2 — a machine rule

Add to `brands/<id>/rules.local.json`. Use the object form with a `rules` array; brandlib merges the
file into `learnedRules`, and a bare array lands at `learnedRules.rules`, so the object form is the
one where both agree.

```json
{
  "rules": [
    {
      "id": "LOCAL.NO_MINT_TEXT",
      "kind": "forbid_color",
      "scope": "title",
      "formats": ["deck"],
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

| `kind` | `value` | Fires when |
|---|---|---|
| `forbid_text` | string or list | the string appears in text in scope (case-insensitive) |
| `require_text` | string | the string is absent everywhere in scope |
| `forbid_color` | hex | the colour is used anywhere in the deck |
| `min_font_size` | number (pt) | text in scope is smaller |
| `max_font_size` | number (pt) | text in scope is larger |
| `forbid_font_size` | number or list | text in scope uses exactly that size |
| `regex` | pattern | the pattern matches text in scope |

- `scope`: `any`, `title`, `body`, `eyebrow`.
- `formats`: **optional**, and the field to reach for whenever the correction was about one kind of
  artifact. `deck` or `video` for the artifact kind, or a delivery format — `landscape`, `square`,
  `vertical`. **A reel is `vertical`.** Omit it and the rule applies to everything, which is the
  right default when the note is genuinely general. Get this wrong in the narrow direction and the
  rule fires on work it was never meant to govern; a note that says "on reels" and a rule with no
  `formats` is how a reel preference ends up warning on every deck title. A misspelled value is
  reported and the rule stays active everywhere — a typo never silently disables a rule.
- `severity`: `error`, `warn`, `info`. Use `error` only for something that must block a build —
  an error stops the session from ending until it is fixed.
- `id` **must** start with `LOCAL.` so learned rules stay distinguishable from built-in ones.
- Keep `rule` and `fix` populated. They are what the next agent reads when the rule fires.

**Know the limit before you promise enforcement.** Both validators read learned rules, but they do
not read the same kinds:

| | `validate_deck.py` | `validate_video.py` |
|---|---|---|
| `forbid_text`, `require_text`, `regex` | yes | yes — over narration, captions and on-screen template copy |
| `forbid_color` | yes | no — reported as `info`; colour on film is covered by `VIDEO.OFF_PALETTE` from sampled frames |
| `min_font_size`, `max_font_size`, `forbid_font_size` | yes | no — reported as `info`; a rendered film exposes no type sizes to inspect |

A correction about pacing, loudness or caption timing is not a local rule at all — it belongs in
`brand.json` under `video`, or in `LEARNED.md` as guidance. Say which one you did; do not claim a
correction is enforced when it is not.

Then prove the file still parses. A malformed local rules file makes `load_brand` raise and takes
every downstream skill with it:

```sh
"$PY" -c "import sys;sys.path.insert(0,'$ROOT/scripts/lib');import brandlib;\
b=brandlib.load_brand('<id>');print(len(b['learnedRules'].get('rules',[])),'learned rules ok')"
```

### Tier 3 — a brand fact

Edit `brands/<id>/brand.json` directly. Then, in the same edit:

- bump `version`,
- set `updated` to today,
- add a line to `provenance` naming who ruled and when.

A brand fact that arrives with no provenance is indistinguishable from a typo six months later.

Re-load to confirm the profile is still valid, and re-read the summary card:

```sh
"$PY" "$ROOT/scripts/brand_resolve.py" <id>
```

## 4. Prove it fires

A rule nobody tested is a rule that does not exist. Re-validate the artifact that triggered the
correction:

```sh
"$PY" "$ROOT/scripts/validate.py" "<the artifact>" --format human; echo "exit=$?"
```

- **Tier 2, the artifact still contains the mistake** → your new `LOCAL.*` id must appear in the
  output. If it does not, the rule is wrong (usually the wrong `scope`, or a hex that is not
  actually the one in the file). Fix the rule; do not declare victory.
- **The artifact was already corrected** → build a throwaway deck that still contains the mistake and
  validate that, or say plainly that the rule is untested and why.
- **Tier 3** → the relevant built-in check should now report against the new value.

If the correction was itself the fix for a violation, rebuild the artifact and confirm it now passes.
That also clears the pending marker the PostToolUse hook is holding.

## 5. Report in one line

Say which tier, in plain words, and nothing more:

> Learned: mint is never a text colour — logged in `LEARNED.md` and added as `LOCAL.NO_MINT_TEXT`
> (error, scope title). Confirmed it fires on the current deck.

If you could not persist it mechanically, say that too, and say why:

> Learned: intros should run 3s not 5s — logged in `LEARNED.md` and set as `video.intro.durationSec`
> in `brand.json`. Video rules are not machine-checked by the validator, so this one relies on the
> build reading the profile.
