---
description: Validate a deck or video against its brand profile and explain every violation with the fix.
argument-hint: "[path to .pptx or .mp4] (defaults to the most recently modified artifact)"
---

# /brand-check

Run the brand validator over an artifact and turn its output into something a human can act on.
This is the read-only twin of the PostToolUse hook: same validator, same verdict, but it explains
rather than blocks.

```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"
```

Target: `$ARGUMENTS`

---

## 1. Find the artifact

If `$ARGUMENTS` names a file, use it. Quote it — deck paths routinely contain spaces.

If it is empty, find the most recently modified artifact under the current directory and **say which
one you picked** before validating it. Never validate silently against a file the user did not name.

```sh
find . -type f \( -name '*.pptx' -o -name '*.potx' -o -name '*.mp4' -o -name '*.mov' -o -name '*.m4v' \) \
  -not -path '*/.git/*' -not -path '*/node_modules/*' -not -name '~$*' \
  -exec ls -t {} + 2>/dev/null | head -5
```

The first line is the target; the rest are shown so the user can correct you in one word. If nothing
is found, say so and stop — do not go hunting outside the working directory.

## 2. Validate

```sh
"$PY" "$ROOT/scripts/validate.py" "<artifact>" --format human; echo "exit=$?"
```

`validate.py` is only a dispatcher. It routes `.pptx`/`.potx` to `validate_deck.py` and
`.mp4`/`.mov`/`.m4v` to `validate_video.py`, and it attaches sidecars it finds beside the artifact
(`<name>.ir.json`, `<name>.timeline.json`, `<name>.srt`) automatically.

**Read the exit code, not the prose.**

| Exit | Meaning |
|---|---|
| `0` | No errors. Warnings and info may still be present and still worth reporting. |
| `2` | At least one error. The artifact is not shippable as it stands. |
| `1` | The validator itself failed — a corrupt file, a missing brand, a bad sidecar. That is a bug to fix, not a verdict about the deck. |

Two flags are worth adding by hand when they apply:

- `--ir deck-ir.json` on a deck. Without it the validator infers each slide's archetype from
  geometry and hedges its findings; with it, layout and content checks are exact.
- `--brand <id>` when the artifact carries no brand property or the wrong one.

If the user wants the raw contract instead of prose, re-run with `--format json`.

## 3. Explain, do not paste

Do not dump the validator output and stop. Go through it and produce:

1. **The verdict in one line.** "3 errors, 6 warnings — not shippable" or "clean, 4 info notes."
2. **Errors first, grouped by violation id**, each with: where it is, what was found, what was
   expected, and the concrete fix. Every violation already carries `rule` and `fix` fields — use
   them, but say them in your own words and name the actual slide or timestamp.

   **Group BEFORE you quote anything.** On any report with more than a handful of errors, the first
   action is to aggregate — by rule code plus the distinguishing values (shape name or role,
   archetype, the found value itself) — sort by count, and show the user that table rather than the
   raw list. Print the TOTAL beside the groups and check they sum to it: a grouped summary carries no
   internal evidence of its own completeness, so a filter that quietly dropped a whole class looks
   exactly like one that matched everything.

   **A group's SIZE tells you where the cause lives.** In a generated artifact the violation count is
   a function of how widely the defective code is reused, not of how many mistakes were made. A big
   group means one shared helper or emitter — find it in the generator. A group of one means a call
   site. Treating the count as a work estimate is what leads to fixing the same symptom fifty times.
3. **Warnings**, condensed. Say which ones are worth fixing and which are noise for this artifact.
4. **Info**, as a count only, unless something in it is genuinely interesting.

`skills/brand-deck/references/troubleshooting.md` maps every violation id to its real cause and its
fix. Read it before speculating about a code you have not seen before.

The id namespace tells you which part of the pipeline is at fault:

| Namespace | Fix it in |
|---|---|
| `COLOR`, `TYPE`, `LOGO` | the brand profile, or the build code that painted the wrong value |
| `LAYOUT` | the IR — too much copy for the box, or the wrong archetype for the content |
| `CONTENT`, `VOICE` | the copy: placeholder text, exclamation marks, title case, banned phrases |
| `STRUCTURE` | the storyline: missing cover/closing, too few slides, wrong section rhythm |
| `A11Y` | contrast or minimum type size — a real accessibility failure, not a preference |
| `VIDEO`, `AUDIO`, `CAPTION` | the render settings or the Video IR |
| `LOCAL.*` | a learned rule from `brands/<id>/rules.local.json` — someone corrected this before |

`COLOR.SUPERSEDED` is special: it names an old template colour and the canonical replacement. The
replacement is not a suggestion; it is what the design system says the colour became.

## 4. Before fixing: which of three things is broken?

When a check fails, exactly one of three things is wrong — the ARTIFACT, the RULE, or the CHECKER's
implementation of the rule. Remediation tooling only ever offers the first, so a correct diagnosis of
the second or third still arrives at a suggested fix for the artifact. Decide which it is before you
touch anything.

**A finding makes two claims, and only one of them is printed as an instruction.** Every violation
asserts *what this object is* and *what to do about it*. Applying the `fix:` line silently ratifies
the classification. So before applying it, confirm the finding identified the object correctly —
above all for rules that classify: this picture IS the logo, this text IS a title, this shape IS a
card. The cheap test: find the predicate that fired in the checker's source. A rule that matched on a
NAME or a heuristic is the one most likely to have matched the wrong object.

Where the classification is wrong, the fix is to correct what the classifier reads — never to reshape
the object so a rule that should not apply to it passes.

Two hard rules:

- **Never change an artifact in a way that makes it worse for its actual audience** — alt text,
  reading order, contrast, semantics — in order to clear a rule. Rewriting an honest alt text to dodge
  a name match degrades accessibility to satisfy a bug.
- **Never let `/brand-learn` or `/brand-review` persist a checker workaround as a brand rule.** That
  freezes the tool's bug into the brand profile, where nobody will ever connect the two again.

If the checker is at fault, stop: tell the user, name the file and the predicate, leave the artifact
alone, and move on to the real findings.

## 5. Offer the next step, do not take it

End with the smallest real fix, and ask before doing it. Typical shapes:

- **Errors in the copy or layout** → edit the Deck IR and rebuild:
  `"$PY" "$ROOT/scripts/build_deck.py" --ir <ir.json> --out <deck.pptx>`, then re-run this check.
- **Errors in colour or type** → the build code or the brand profile is wrong. Do not hand-patch the
  `.pptx`; the next rebuild would undo it.
- **A violation the user says is deliberate** → that is a brand fact or a documented exception. Run
  `/brand-learn` so it is recorded, and only then, if the user explicitly asks, offer the session
  override: `"$ROOT/hooks/session-gate.sh" --override`. Never override on your own initiative.

Want to look at it as well as measure it?
`"$ROOT/scripts/render_preview.sh" "<deck.pptx>"` writes one PNG per slide.
