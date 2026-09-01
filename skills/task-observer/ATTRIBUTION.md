# Task Observer — vendored, not authored here

This skill is **not Channelplay's work**. It is bundled unchanged so that a
clone of brand-studio arrives with it already enabled, rather than leaving
every new user to discover and install it separately.

- **Author:** Eoghan Henn / [rebelytics.com](https://rebelytics.com)
- **Canonical source:**
  <https://github.com/rebelytics/one-skill-to-rule-them-all>
- **Licence:** CC BY 4.0 — see `LICENSE.txt`. Share and adapt freely, with
  credit to the author.

## Keep it unmodified

Fixes and improvements belong **upstream**, not in this copy. A local edit
here is invisible to everyone else using the skill, and it silently diverges
from a source that is still being maintained — the same volatile-copy trap
the skill itself warns about. If something needs changing, open an issue or
PR against the canonical repository and re-vendor afterwards.

## Re-vendoring

    rm -rf skills/task-observer
    git clone --depth 1 https://github.com/rebelytics/one-skill-to-rule-them-all \
      /tmp/task-observer
    mkdir -p skills/task-observer
    cp -R /tmp/task-observer/SKILL.md /tmp/task-observer/references \
          /tmp/task-observer/scripts /tmp/task-observer/LICENSE.txt \
          skills/task-observer/
    # then restore this file

## One workspace, not one per clone

The skill keeps an observation log on a stable absolute path. It must NOT be
resolved from the working directory — a cwd inside a git worktree or a
temporary clone is torn down and takes the log with it. Pin the path in your
`CLAUDE.md`; the skill's `references/environments.md` explains the setup.
