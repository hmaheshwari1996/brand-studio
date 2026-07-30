---
description: Explain what brand-studio is and how it works — the full picture, compactly
argument-hint: "[brief | flow | archetypes | rules | brands | files | commands]"
---

Explain this plugin to the user by running its own introspection script, which reads
the live registry, grammar and validators — so the explanation cannot drift from the code.

```bash
~/.cache/brand-studio/venv/bin/python "${CLAUDE_PLUGIN_ROOT}/scripts/explain.py"
```

Argument handling — `$ARGUMENTS`:

| Argument | Run instead |
|---|---|
| *(none)* | the command above — the full picture |
| `brief`, `desc`, `description` | add `--brief` for a ten-line summary with the capability list |
| `features` | add `--section features` — what the plugin does well and why it matters |
| `flow`, `archetypes`, `rules`, `brands`, `files`, `commands` | add `--section <that>` |
| anything else | the full output, then answer their question from it |

**Present the script's output as-is.** It is already written to be read by a person and is
deliberately compact. Do not paraphrase it, do not expand it into prose, and do not repeat
it back in your own words — that is pure token cost for no gain.

Add your own words only when:

- the venv is missing (`~/.cache/brand-studio/venv` absent) — then tell them to run
  `scripts/bootstrap.sh` first, and say nothing else;
- the user asked a specific question the output does not answer — answer just that, briefly,
  after the output;
- the output shows something actionable, such as zero brands registered (point them at
  `/brand-new`) or a cached asset marked `MISSING ON DISK`.

If they want to know what the plugin has generated and cached for a brand:

```bash
~/.cache/brand-studio/venv/bin/python "${CLAUDE_PLUGIN_ROOT}/scripts/asset_cache.py" --brand <id> --list
```
