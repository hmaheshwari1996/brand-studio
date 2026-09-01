# Notice — what the MIT licence does and does not cover

`LICENSE` is verbatim MIT and covers **the code in this repository**: the Python
builders and validators under `scripts/`, the skills and commands authored here,
the deck and video grammar, and the documentation.

One thing here belongs to someone else.

---

## Task Observer — CC BY 4.0

`skills/task-observer/`
Copyright Eoghan Henn / [rebelytics.com](https://rebelytics.com)
Canonical source: <https://github.com/rebelytics/one-skill-to-rule-them-all>

Vendored unchanged under CC BY 4.0 — share and adapt freely, with credit. Its
own `LICENSE.txt` and `ATTRIBUTION.md` ship with it. Do not edit the vendored
copy: fixes belong upstream, or this copy diverges silently from a source that
is still maintained.

---

## Brand profiles are their owners' property

`brands/example/` is **synthetic** — a neutral profile that exists so the
builders, the validator and the test suite have something to run against out of
the box. Its palette, its placeholder mark and its rules are invented. Nothing
in it belongs to anyone.

Any brand you add is a different matter. A brand profile carries logos,
wordmarks and colour systems belonging to the organisation it names, and no
licence granted by this repository can grant rights over them. The MIT grant
does not extend to any trademark or brand system placed under `brands/`. Add
your own with `/brand-new`; if you fork a repository containing someone else's
brand, remove it.

Fonts are the same. A brand that ships font files must ship their licence
alongside them — an OFL font, for example, requires the licence to travel with
the font.

---

## Referenced, not bundled

`claude-mem` and `claude-code-setup` are listed in
`.claude-plugin/marketplace.json` by URL. No part of either is copied into this
repository, and each keeps its own author, licence and release cadence. A
marketplace entry is an offer to install, not a redistribution.

## Third-party Python packages

`scripts/bootstrap.sh` installs `python-pptx` and `pillow` into a virtualenv at
run time. They are not vendored here and keep their own licences.
