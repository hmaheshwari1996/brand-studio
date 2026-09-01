# Script and voiceover

How to write the narration, how to shape it into the brand's arc, how to set the voice engine, and how
to turn all of it into captions someone can read with the sound off.

- [The premise](#the-premise)
- [The arc, beat by beat](#the-arc-beat-by-beat)
- [Writing to be spoken](#writing-to-be-spoken)
- [Numbers, names and abbreviations](#numbers-names-and-abbreviations)
- [Length arithmetic](#length-arithmetic)
- [The voice engine](#the-voice-engine)
- [Caption authoring](#caption-authoring)
- [Script checklist](#script-checklist)

---

## The premise

**The script is the film.** Everything else is illustration, timing and encoding.

This matters more here than in a deck, because a deck is read at the reader's pace and a film is not.
A viewer cannot skim back over a sentence they missed, cannot pause on a number, cannot re-read a
clause that turned out to be load-bearing. Whatever the narration fails to land in the second it is
spoken is gone.

So the script is written and approved as **continuous prose**, in the order it will be heard, before
any IR is authored and before a single frame is rendered. Not bullets, not per-scene fragments,
not "I'll tighten it in the IR". Prose, read start to finish, out loud.

Three things this catches that nothing downstream can:

- **The argument does not connect.** Beat three does not follow from beat two, and reading it in one
  pass makes that obvious in a way eight separate `vo` fields never do.
- **The same claim is made twice.** In fragments it reads as emphasis. In prose it reads as padding.
- **It is too long.** 248 words is 90 seconds. You can count that on the page in ten seconds; you find
  it out after four minutes of rendering otherwise.

---

## The arc, beat by beat

`brand.video.storyline.arc` for Example Brand:

```
hook → problem → approach → proof → outcome → call-to-action
```

Every stage must appear at least once, and first appearances must be in this order — the validator
checks both (`STRUCTURE.STORYLINE`, warn). Repeats are normal and expected: two `problem` scenes and
two `approach` scenes is a healthy 8-scene film. What is not normal is skipping `proof`, or opening on
`approach` because the credentials were the easiest part to write.

The brand's own rules, verbatim from the profile, and they override anything below:

> - Open on the client's problem, never on the brand's credentials.
> - One idea per scene. If a scene needs two sentences of setup, it is two scenes.
> - Proof is specific and numeric wherever possible: stores, SKUs, states, headcount.
> - Close with a single, concrete next step.

### hook — earn the next eight seconds

**Job:** make the viewer recognise their own situation in the first sentence.

One or two scenes, 6–10 seconds total. A concrete image beats an abstraction, and a specific one beats
a general one. It is not a title card, it is not a mission statement, and it is not "we are a leading
provider of".

> **Good:** "A retail plan is agreed in a boardroom. It is executed by one person, standing in one
> store, on a Tuesday afternoon."
>
> **Bad:** "In today's competitive retail landscape, execution excellence has never been more critical."

The good one is a picture the viewer has stood in. The bad one is a sentence they have heard four
hundred times and stopped hearing.

**Fails when:** it describes the agency, states a category truth nobody disputes, or asks a rhetorical
question.

### problem — make the cost visible

**Job:** name what is going wrong and what it costs, in the client's terms.

One to three scenes. This is where you may be uncomfortable and specific. The strongest problem beats
are structural — the mechanism that makes the failure inevitable — not a list of symptoms.

> **Good:** "Compliance is self-reported, so nobody can check it. And the report arrives a month after
> the sale was lost."
>
> **Bad:** "There are several challenges in field execution today."

**Fails when:** it blames the client, lists more than three things, or hedges. Also fails when the
narration is the problem and the visual is already the solution — a mismatch nothing measures.

### approach — how, in a shape someone can hold

**Job:** explain the mechanism, not the offering.

One to three scenes. The test is whether a viewer could describe your approach to a colleague
afterwards. That means a shape — four steps, three checks, one loop — not a capability list.

> **Good:** "So we treat the person in the store as the product. Four steps, from in-app learning
> through field shadowing and classroom sessions, to a certification that is re-earned every quarter."
>
> **Bad:** "Our comprehensive suite of solutions spans training, audit, analytics and workforce
> management."

**Fails when:** it becomes a capability inventory, or when it needs a diagram the viewer cannot see.

### proof — the beat the whole film is for

**Job:** show that the approach did what the approach claims.

One or two scenes, and **numeric wherever possible: stores, SKUs, states, headcount, before and
after.** This is the beat that separates a film worth sending from a brochure with a voice.

Two rules, and both are absolute:

1. **Every number needs a source you can say out loud in the room.** The validator counts characters,
   not truth. A number you cannot defend is worse than no number.
2. **Give the baseline.** "Ninety two percent compliance" means nothing. "Ninety two percent, up from
   sixty one" means everything. A number without its before is decoration.

> **Good:** "Audited compliance rose from sixty one to ninety two percent, because the gap between the
> two stopped being invisible."
>
> **Bad:** "We delivered significant improvements in compliance across the programme."

**Fails when:** the number is unsourced, the baseline is missing, or the claim is directional
("improved", "enhanced", "optimised") rather than measured.

### outcome — the scale it runs at now

**Job:** establish that this is operating reality, not a pilot.

One scene, usually. Volume, geography, duration. It is the beat that answers "yes, but would it work
at our size?" before the question is asked.

> **Good:** "That model runs today across four thousand two hundred general trade stores, six hundred
> and eighty modern trade doors, and two thousand nine hundred telecom outlets."

**Fails when:** it repeats the proof beat with rounder numbers, or reaches for a total that quietly
double-counts.

### call-to-action — one concrete next step

**Job:** name the single, small, specific thing that happens next.

One scene, 6–9 seconds. Small enough that saying yes is easy; specific enough that saying yes means
something. Not "get in touch", not "let's talk", not "thank you".

> **Good:** "Pick one territory and one metric. We will run it for a quarter, against your current
> baseline."
>
> **Bad:** "Contact us today to learn how we can help transform your retail execution."

**Fails when:** it asks for a meeting rather than a decision, or offers three options. One.

---

## Writing to be spoken

**The test:** read the line out loud at a normal pace. If you run out of breath, stumble, or have to
glance back at the start of the sentence, rewrite it. Do this for every line. It takes two minutes for
a whole script and it catches almost everything.

### Sentence length

One clause is good, two is the maximum. A semicolon in narration is a full stop that has not admitted
it yet, and a subordinate clause in the middle of a sentence is a sentence the listener has to hold
open while you finish it.

> **Before:** "Because field staff turnover, which in most programmes exceeds sixty percent annually,
> occurs before training investment can be recouped, the programme never reaches steady state."
>
> **After:** "Field staff turn over before they are trained. In most programmes, more than sixty
> percent a year. The programme never reaches steady state."

Three sentences, 24 words, the same content, and every one of them survives being heard once.

### Front-load the subject

The listener commits to a subject in the first two words. Make them the right two.

> **Before:** "Across the fifteen states in which the programme currently operates, compliance is
> measured weekly."
>
> **After:** "Compliance is measured weekly, across fifteen states."

### No text that only works on the page

- No parentheses. A parenthetical is a thought the writer could not place; spoken, it is a detour with
  no signposts.
- No "as shown here", "see below", "the following", "as we discussed". The viewer sees one frame.
- No bulleted fragments read aloud. "Training. Audit. Analytics." is a list on a slide and a stutter in
  a voice.
- No headings. If your script has a line that is just a noun phrase, it belongs in `visual.data`, not
  in `vo`.

### Active voice, present tense

> **Before:** "A geo-stamp is captured by the field executive and is then verified against the
> planogram."
>
> **After:** "The executive captures a geo-stamp. We score it against the planogram."

### No exclamation marks

`VOICE.EXCLAMATION` is an **error** and it blocks the build. It is checked on the shipped caption text,
so it catches the mark wherever it survived. The brand voice is plain and operational; if a line needs
emphasis, make the claim specific instead — a number does the work an exclamation mark pretends to.

### Never a forbidden phrase

`brand.voice.forbiddenPhrases` exists because template scaffolding reaches clients more often than
anyone admits. `CONTENT.PLACEHOLDER` is an **error**. For Example Brand the list includes `Lorem ipsum`,
`Click here`, `Person Name`, `Chapter Name Goes Here` and `This is placeholder copy`, among others.

### One thought per sentence, one point per scene

If you need "and also", start a new scene. If you need "but first", the scenes are in the wrong order.

---

## Numbers, names and abbreviations

`say` reads the characters it is given. Anything that relies on a reader's eye to disambiguate comes
out wrong, and it comes out wrong differently on different voices.

**Spell it in `vo`. The `caption` may use the compact form** — that is what captions are for.

| Written | `vo` says | `caption` says |
|---|---|---|
| `62%` | sixty two percent | 62% |
| `4,200 stores` | four thousand two hundred stores | 4,200 stores |
| `3.5x` | three and a half times | 3.5x |
| `₹4.2cr` / `INR 4.2 cr` | four point two crore rupees | ₹4.2cr |
| `Q1 FY26` | the first quarter of financial year twenty six | Q1 FY26 |
| `GT` / `MT` | general trade / modern trade | GT / MT |
| `SKU` | ess-kay-you, or "line item" — prefer the word | SKU |
| `24x7` | around the clock | 24x7 |
| `sq ft` | square feet | sq ft |
| `&` | and | & |
| `31 days` | thirty one days | 31 days |
| `#1` | number one | #1 |
| `e.g.` / `i.e.` | for example / that is | — rewrite, do not use |
| `2026-07-30` | the thirtieth of July | 30 Jul 2026 |

Two more traps:

- **Acronyms the client uses daily are fine spoken as letters** if the audience is internal to that
  world — `KPI`, `CRM`, `POS`. Acronyms the client does not use are jargon. When in doubt, use the
  words; the caption can carry the acronym.
- **Proper nouns get checked against the voice.** Indian place and brand names are where a US voice
  most often mispronounces. Synthesize the line and listen before you commit:

  ```sh
  say -v Samantha -r 165 -- "across Bengaluru, Kochi and Guwahati"
  ```

  If it is wrong, either change the voice (see below) or rephrase — "across three southern states"
  costs less than a mispronounced city name in a client's home market.

---

## Length arithmetic

```
seconds = words ÷ brand.video.voiceover.rateWpm × 60
```

At the brand's **165 wpm** that is **2.75 words per second**:

| Words | Narration | What it is |
|---|---|---|
| 8 | 3s | The hold floor. A single short line. |
| 14 | 5s | The hold default. One sentence. |
| 22 | 8s | Two short sentences. A comfortable scene. |
| 33 | 12s | The hold ceiling. Already two ideas. |
| 40 | 15s | Over the ceiling. Split it. |
| 165 | 60s | A one-minute film. |
| 248 | 90s | A ninety-second film. |

Whole-film runtime, exactly as the builder computes it:

```
runtime = Σ max(holdSec, slideHoldSec.min, voSeconds + 0.4)
          + intro (3.0s) + outro (3.5s)
          − transitionSec (0.4s) × (elementCount − 1)
```

`elementCount` counts the intro and the outro. Present this number with the script, before approval.

**A useful shortcut:** for a target of *N* seconds with an intro and an outro, budget
`(N − 7) × 2.75` words, then take off 10% for the per-scene silence tails. A 95-second film is about
**220 words**. If the draft is at 280, it is a two-minute film pretending otherwise.

---

## The voice engine

`brand.video.voiceover` for Example Brand:

```json
{"enabled": true, "engine": "say", "voice": "Samantha", "rateWpm": 165, "targetLufs": -16.0}
```

### `engine`

`say` is the only engine implemented. Any other value is a build error, not a fallback. Moving the
brand to a real recorded voice or a third-party TTS is a `brand.json` change plus pre-rendered audio,
and it needs the plugin owner — not a flag on one build.

### `voice`

The build fails fast on a voice macOS does not have, so check before you set one:

```sh
say -v '?' | grep -E 'en_(IN|GB|US|AU)'
```

| Voice | Locale | Trade-off |
|---|---|---|
| `Samantha` | en_US | The profile default. Clear, neutral, well-paced. **Mispronounces Indian place and brand names**, and reads as American to an Indian client. |
| `Rishi` | en_IN | Indian English. Handles Indian proper nouns correctly and matches the audience. Slightly slower than the wpm estimate — budget for it. |
| `Aman` | en_IN | Indian English, male. Same benefits as `Rishi`. |
| `Daniel` | en_GB | British English. Reads as formal; good for a corporate readout, wrong for a field-facing piece. |
| `Karen` | en_AU | Rarely the right answer here. |

**Raise the voice choice with the user, do not decide it.** For an Indian retail programme narrated to
an Indian client, `Rishi` or `Aman` is usually the better answer than the default — and that is a
`brand.json` change if it sticks, logged through the learn protocol.

Never use a novelty voice (`Bad News`, `Bells`, `Jester`, `Superstar`). They are on the list.

### `rateWpm`

Passed to `say -r`. 165 is the brand's setting and is a good pace for a client-facing film: fast enough
not to drag, slow enough that a number lands.

- Below ~140 the film feels like a training video.
- Above ~190 numbers stop registering, which defeats the proof beat.
- **It is not a runtime control.** Speeding the voice up to hit a duration target makes an unlistenable
  film. Cut words instead.

### Estimate versus reality

`--dry-run` estimates from `rateWpm`. The real build measures the `say` output with `ffprobe`, and the
two differ per voice — measured on the same 22-word line at 165 wpm:

| Voice | Estimated | Measured | Drift |
|---|---|---|---|
| `Samantha` | 8.00s | 7.37s | −8% |
| `Rishi` | 8.00s | 8.15s | +2% |

So a 100-second dry-run estimate can render at 93 seconds on `Samantha`. **The dry run is a plan, not a
promise.** Treat a target overshoot under about 10% as within noise, and re-check the real runtime from
the timeline after the build.

### `targetLufs`

−16.0 LUFS integrated for the finished master, which is where speech-led social video sits. The
validator allows ±2 LU (`AUDIO.LOUDNESS`, warn) and a −1.0 dBTP true-peak ceiling (`AUDIO.CLIPPING`,
warn). Both are handled by the builder; you only touch them if the brand target itself is wrong.

---

## Caption authoring

Captions are the film for everyone watching muted, which on social is most people. Treat them as copy.

### What the builder does for you

From each scene's `caption` (or `vo` when `caption` is absent): split at sentence boundaries, wrap to
`maxCharsPerLine` (42), pack into cues of at most `maxLines` (2), and distribute the cues across the
scene's narration window with every cue at or above `minDurationSec` (1.2s). It writes `film.srt`.

You never hand-write an SRT. You write good `caption` strings and the shape follows.

### What you decide

**1. Whether `caption` differs from `vo` at all.**

Leave it out when the narration is one or two short sentences — verbatim is best, because the muted
viewer gets exactly what the listening viewer gets.

Write it explicitly when the narration is long. Then the caption is a *condensation*, and the rule is:

> **Keep the claim, drop the setup.**

> `vo`: "The result is not that people got better. Self-reported compliance barely moved. Audited
> compliance rose from sixty one to ninety two percent, because the gap between the two stopped being
> invisible."
>
> `caption`: "Audited compliance rose from sixty one to ninety two percent."

The muted viewer gets the number. They do not get the mechanism — and that is the trade, made
deliberately, because the mechanism is on the slide behind it.

**2. Numerals over words.** `62%` reads faster than "sixty two percent" and costs a third of the line
budget. The `vo` spells it out; the caption does not have to. This is the main reason to write a
`caption` at all on a numeric scene.

**3. Sentence length.** Cues break at sentences, so the sentence *is* the cue. A sentence that wraps to
three lines becomes two cues split wherever the wrap fell — usually mid-clause. Aim for sentences of
about 80 characters or under: two 42-character lines.

**4. The condensation still has to be true.** A caption that overstates what the narration said is the
one that gets screenshotted.

### Line-break quality

The builder wraps greedily on word boundaries, which is right most of the time and ugly occasionally.
Read the caption block in the dry run — line lengths are printed in brackets. A line that ends on
"the", "of", "and" or "a" reads badly. Fix it by rewording, not by fighting the wrapper:

```
  8    00:01:19,582 --> 00:01:29,037   [s7]
       Live today across more than seven thousand  (42)
       stores.  (7)
```

A 7-character orphan on line two. "Live today across seven thousand stores" fits on one line and reads
better spoken as well.

### What the validator checks

| Violation | Severity | Fires when |
|---|---|---|
| `CAPTION.MISSING` | **error** | The brand requires captions and there is no SRT and no embedded subtitle stream. |
| `CAPTION.OVERLAP` | **error** | Two cues overlap, or a cue ends before it starts. |
| `CAPTION.LINE_LEN` | warn | A line exceeds `maxCharsPerLine`. |
| `CAPTION.LINE_COUNT` | warn | A cue has more lines than `maxLines`. |
| `CAPTION.TOO_FAST` | warn | A cue is held under `minDurationSec`. |
| `CAPTION.GAP` | info | Over 3s with no caption while the timeline says narration is running. |

And over the caption text itself: `CONTENT.PLACEHOLDER` (**error**) and `VOICE.EXCLAMATION` (**error**).

None of them check whether the caption says what the voice says. **Read the captions with the sound
off**, in step 7. That is how the audience will.

---

## Script checklist

Before you show the script for approval:

- [ ] It is prose, in order, readable start to finish.
- [ ] Every one of the six arc stages is present, in order.
- [ ] It opens on the client's problem, not on the agency.
- [ ] No scene carries two ideas. Anything needing two sentences of setup is two scenes.
- [ ] Every number has a baseline and a source you can say out loud.
- [ ] Numbers, currency, dates and abbreviations are spelled the way they are spoken.
- [ ] No exclamation marks. No forbidden phrases. Sentence case, present tense, active voice.
- [ ] Every sentence survives being read out loud in one breath.
- [ ] The word count and the runtime estimate are stated, against `meta.durationTargetSec`.
- [ ] The closing beat names one concrete next step.
- [ ] The voice has been raised with the user if the default locale does not match the audience.

---

## Presenting the script for approval

Write the whole script as continuous prose, in the order it will be spoken, **before** you touch the
IR. Present it with a beat label per paragraph, the per-paragraph arithmetic, and the runtime total:

```
HOOK          A retail plan is agreed in a boardroom. It is executed by one person,
              standing in one store, on a Tuesday afternoon.                          (22 words, 8.0s)

PROBLEM       Most programmes do not fail on strategy. They fail on three things…
              …
                                                              TOTAL 248 words · 90s narration
                                                              + intro 3.0s + outro 3.5s
                                                              − 9 transitions × 0.4s
                                                              ≈ 103s runtime (target 95s, +8%)
```

Say the estimate out loud when you present the script. If it overshoots `meta.durationTargetSec` by
more than about 10%, cut words now — not scenes, **words**. Cutting after the render means re-rendering.

**Get explicit approval. Silence is not approval.** An amendment is fine and expected: take it, redo
the script, re-show it. No frames get rendered until the user has read the words.

---

## Scene pacing

**One idea per scene.** That is the whole rule; everything below is arithmetic in service of it.

A scene that needs two sentences of setup before it can make its point is really two scenes. The setup
is one idea and the point is another, and the viewer is being asked to hold the first while you finish
the second, over a single unmoving image. Split it. Two 7-second scenes beat one 14-second scene every
time, and they cost nothing extra to render.

The brand sets the floor and the ceiling in `brand.video.slideHoldSec`. For Example Brand:

| | Seconds | Words at 165 wpm | What it is for |
|---|---|---|---|
| `min` | 3.0 | ~8 | The floor. Below this the eye cannot land on the frame before it changes. Enforced — a shorter scene is silently padded. |
| `default` | 5.0 | ~14 | One short sentence. Where most scenes should sit. |
| `max` | 12.0 | ~33 | The ceiling. Above this a still frame is dead air with a voice over it. |

**The ceiling is really a word budget.** 12 seconds at 165 wpm is 33 words. Write a 40-word scene and
the builder resolves it to about 15 seconds. It never truncates your narration, and it must not: the
fix is to cut words or split the scene, never to let the frame sit there longer.

**And the ceiling warning will not catch it for you.** The build warns when the *declared* `holdSec`
exceeds `slideHoldSec.max`. It says nothing when a modest `holdSec` is stretched past the ceiling by
long narration — which is the more common way it happens. **Read the `dur` column in the dry run, not
the warnings**, when you are checking pacing. Anything over the ceiling is a scene carrying two ideas.

Two consequences worth internalising:

- **`holdSec` is a floor, not a duration.** Resolved duration is
  `max(holdSec, slideHoldSec.min, voDuration + 0.4)`. Set `holdSec` to what the *visual* needs; the
  narration will take it from there if it needs more.
- **A silent scene uses `holdSec` exactly.** Scenes with no `vo` — a full-bleed image, a title card —
  are the only place `holdSec` is the whole answer. Give them 3–5 seconds. They feel longer than they
  are.

A 90-second film is roughly 8–12 scenes. If you have 5, the scenes are too long. If you have 25, the
film is a slideshow and nothing lands.

### While authoring the IR

- **Every scene declares a `role`, and the roles walk the brand's arc in order.** For Example Brand:
  `hook → problem → approach → proof → outcome → call-to-action`. A missing stage or an out-of-order
  first appearance is `STRUCTURE.STORYLINE` (warn); an unrecognised role is the same violation.
  Repeating a stage is fine — two `problem` scenes in a row is normal.
- **`caption` defaults to `vo` when omitted.** Omit it when the narration is short enough to read.
  Write it explicitly when the narration is long, and make it the *load-bearing sentence* — not a
  paraphrase and not a headline.
- **Write in the brand's voice.** Sentence case, present tense, active voice, no exclamation marks,
  never a phrase from `brand.voice.forbiddenPhrases`. Both are checked against the shipped captions.
