# Storyline patterns

A deck is a written argument. This file holds the argument shapes that the brand's real work
actually takes, so you start from a known-good sequence instead of from nothing.

Use them as a starting point, not a template to fill. Every one of them gets adapted to the room.

---

## Before any pattern: settle four things

Write these down and get them approved before you touch an archetype.

1. **Audience.** Who is in the room, what they already believe, and what they are sceptical of.
   "The client's national sales head, who was burned by the last agency's reporting" is an audience.
   "The client" is not.
2. **The single decision you want.** One sentence, one decision. *Approve the pilot. Renew at the
   higher tier. Sign off the training rollout. Move two territories to us.* If you cannot name it,
   the deck has no job and you are about to write a document instead.
3. **The arc.** The ordered list of claims that moves a sceptic from where they are to that
   decision. Each claim is one slide, or occasionally two.
4. **The slide count.** A number, agreed up front. It is a budget. Going over means something else
   comes out.

### What a claim looks like

Each content slide makes exactly one claim, and the slide title states it.

| Weak (a label) | Strong (a claim) |
|---|---|
| "Our technology" | "The field app is the system of record" |
| "Results" | "Compliance improves once evidence is required" |
| "Training approach" | "Certification gates which stores a person can work" |
| "Next steps" | "Pick one territory and one metric" |

If a slide's title is a noun phrase, either the slide has no claim or you have not found it yet.

### Cadence rules that apply to every pattern

- **Section breaks every 6–8 slides.** `grammar.deckRules.sectionBreakEveryNSlidesMax` is 8. Beyond
  that the audience loses the thread and the validator raises
  `STRUCTURE.MISSING_SECTION_BREAK`.
- **Never three of the same archetype in a row.** `maxConsecutiveSameArchetype` is 2. Alternate
  `text-visual` with `visual-text`; break a run of prose with `stats` or a `quote`.
- **One `quote` per deck.** Two if one is a client and one is a field person. Never three.
- **At most two `full-bleed` slides.** They are punctuation, not paragraphs.
- **Proof is numeric wherever possible.** Stores, SKUs, states, headcount, days, percentage points.
  Every number needs a caption saying where it came from.
- **The close carries the ask.** `closing`, not a photograph, unless the photograph *is* the ask.

---

## Pattern 1 — New-business pitch

**Purpose:** win a mandate you do not currently hold.
**Audience:** client brand and sales leadership, usually with procurement present.
**The decision:** award the pilot, or shortlist us for the next stage.
**Length:** 14–18 slides. Longer loses the room; shorter reads as thin against incumbents.

The governing rule for Example Brand pitches: **open on the client's problem, never on our
credentials.** Credentials are the proof that we can fix the problem, and proof only lands after the
problem is agreed.

| # | Archetype | Purpose | What it must prove |
|---|---|---|---|
| 1 | `cover` | Whose deck, what it argues | That this is about their business, not our brochure |
| 2 | `agenda` | The map | That the deck has a spine and will end with an ask |
| 3 | `section-break` | Chapter 1: the problem | Names the problem in the client's own language |
| 4 | `stats` | Size the loss | That the problem is worth money — 3–4 numbers with sources |
| 5 | `title-body` | Why it happens | That we understand the mechanism, not just the symptom |
| 6 | `text-visual` | Evidence the diagnosis is right | A chart or photograph from their category |
| 7 | `section-break` | Chapter 2: what we would do | — |
| 8 | `process-band` | The operating model end to end | That there is a system, not a promise |
| 9 | `steps` | The part that fixes their specific failure | That the fix attaches to the problem named on slide 5 |
| 10 | `icon-grid` | What the model changes | 4 parallel consequences, each one line |
| 11 | `section-break` | Chapter 3: proof | — |
| 12 | `stats` | Scale we already run at | That we can carry their volume today |
| 13 | `icon-rows` | Comparable live programmes | That we have done this in their format, not just somewhere |
| 14 | `quote` | A client says it instead of us | Third-party credibility at the moment of maximum doubt |
| 15 | `photo-trio` | Who runs the account | That named, experienced people are accountable |
| 16 | `table` | Commercials and commitments | That the numbers are specific and measurable |
| 17 | `closing` | The ask | One territory, one metric, one quarter |

**Compressing to 12:** drop 6, 10, 15. Never drop the problem chapter, never drop proof, never drop
the close.

**What kills this deck:** opening with "About Example Brand". Proof before problem. A commercial table
with ranges instead of numbers. A close that says "we look forward to partnering with you".

---

## Pattern 2 — Quarterly business review

**Purpose:** account continuity. Report the quarter honestly and set the next one.
**Audience:** the client team that already works with us, plus their leadership.
**The decision:** agree the next quarter's priorities, and approve any change in scope or rate.
**Length:** 12–16 slides.

A QBR is not a pitch. The audience already bought. The job is to prove the programme is under
control, be first to name what went wrong, and get agreement on what changes.

| # | Archetype | Purpose | What it must prove |
|---|---|---|---|
| 1 | `cover` | Quarter, account, period | — |
| 2 | `agenda` | The map | That the misses are on the agenda, not buried |
| 3 | `stats` | Headline performance | The 4 numbers the client tracks, against target |
| 4 | `table` | Commitments against actuals | Every commitment, its target, its actual, measured from what |
| 5 | `section-break` | Chapter: what worked | — |
| 6 | `text-visual` | The quarter's best movement | A trend, with the intervention marked on it |
| 7 | `icon-rows` | Three things that drove it | Attribution, not coincidence |
| 8 | `section-break` | Chapter: what did not | — |
| 9 | `title-body` | The miss, named plainly | That we found it before they did, and know why |
| 10 | `steps` | The corrective plan, with dates | That the fix is already running, with an owner |
| 11 | `section-break` | Chapter: next quarter | — |
| 12 | `columns` | Priorities by workstream | 3–4 priorities, each with a measurable outcome |
| 13 | `table` | What we are asking the client for | Decisions, data, access, sign-offs, with dates |
| 14 | `closing` | Agreement on the above | The specific approvals needed to start Monday |

**What kills this deck:** hiding the miss on slide 11 of 14. A corrective plan with no owner and no
date. Reporting activity (visits made) instead of outcome (availability moved). Rounding a bad
number until it looks acceptable — the client has the same dashboard.

---

## Pattern 3 — Operational readout

**Purpose:** a focused briefing on one operational question — a launch, an incident, a coverage
problem, a pilot result.
**Audience:** operators. People who will act on it this week.
**The decision:** approve a specific operational change, or accept that no change is needed.
**Length:** 6–10 slides. Ruthlessly short.

This is the pattern most often over-built. It is not a pitch and it is not a QBR. The audience wants
the finding, the evidence, and the recommendation, in that order.

| # | Archetype | Purpose | What it must prove |
|---|---|---|---|
| 1 | `cover` | The question being answered | Stated as a question or a finding, not a topic |
| 2 | `title-body` | The finding, up front | The answer, in the first 30 seconds |
| 3 | `stats` | The numbers behind it | 3 numbers, each with its measurement basis |
| 4 | `text-visual` | The evidence | The chart or photograph the finding rests on |
| 5 | `visual-text` | The counter-evidence, or the caveat | That we looked for the ways we could be wrong |
| 6 | `columns` | The options | 3 options with their trade-offs, honestly stated |
| 7 | `steps` | The recommended path | What happens, in what order, starting when |
| 8 | `closing` | The decision required | Who decides what, by when |

**Compressing to 6:** merge 4 and 5 into one `text-visual`; drop 6 if there is genuinely one option.

**What kills this deck:** burying the finding behind five slides of methodology. Presenting three
options when two are obviously unworkable — that is theatre. Ending without naming who decides.

---

## Pattern 4 — Training rollout

**Purpose:** get a client to approve and resource a training programme across a field workforce.
**Audience:** brand, sales and often HR. Frequently the people who have to release staff from the
field to attend.
**The decision:** approve the curriculum, the calendar and the release of field time.
**Length:** 12–16 slides.

The hard part is never the content. It is the cost of taking people out of stores, so the deck has
to earn that time explicitly.

| # | Archetype | Purpose | What it must prove |
|---|---|---|---|
| 1 | `cover` | The programme and its population | Scale up front: how many people, how many days |
| 2 | `agenda` | The map | — |
| 3 | `section-break` | Chapter: why now | — |
| 4 | `stats` | The capability gap, measured | That the gap is real and costs something today |
| 5 | `text-visual` | Where performance and capability diverge | Correlation between certification and outcome |
| 6 | `section-break` | Chapter: the curriculum | — |
| 7 | `steps` | The learner journey | That it is a path, not a set of sessions |
| 8 | `icon-grid` | Modules and what each changes on the floor | Behaviour change per module, not topic titles |
| 9 | `table` | Calendar, cohorts, locations, field time cost | The exact hours out of store, stated plainly |
| 10 | `section-break` | Chapter: how we know it worked | — |
| 11 | `process-band` | Assessment and certification flow | That completion is measured, not assumed |
| 12 | `stats` | Results from comparable rollouts | Before-and-after from a programme of similar shape |
| 13 | `quote` | A field voice, not a client executive | That the people being trained found it usable |
| 14 | `closing` | The approvals needed | Curriculum sign-off, calendar lock, field-time release |

**What kills this deck:** listing modules as topics ("Product knowledge") instead of behaviours
("Can explain the warranty difference without a leaflet"). Hiding the field-time cost. Measuring
attendance and calling it capability.

---

## Pattern 5 — Capability overview

**Purpose:** the standing deck. Explains what Example Brand does, for a first meeting or a warm intro.
**Audience:** someone who does not yet have a brief.
**The decision:** a next conversation about a specific problem of theirs.
**Length:** 10–14 slides. It gets forwarded internally, so it must stand up without a presenter.

The temptation is to list everything. Resist it. A capability deck that covers eight service lines
is a deck about nothing. Pick the two or three the audience is most likely to need and go deep.

| # | Archetype | Purpose | What it must prove |
|---|---|---|---|
| 1 | `cover` | What we do, in one phrase | Positioning, not a slogan |
| 2 | `agenda` | The map | — |
| 3 | `section-break` | Chapter: the problem we exist for | — |
| 4 | `title-body` | The failure mode we specialise in | A point of view, held clearly |
| 5 | `stats` | What it costs the market | That the problem is expensive at scale |
| 6 | `section-break` | Chapter: how we work | — |
| 7 | `process-band` | The operating model | One picture of the whole machine |
| 8 | `icon-rows` | The three capabilities that matter | Depth on three, not breadth on eight |
| 9 | `visual-text` | The technology layer | That evidence is systematic, not manual |
| 10 | `section-break` | Chapter: proof | — |
| 11 | `stats` | Scale today | Stores, states, headcount, clients |
| 12 | `icon-rows` | Live programmes across formats | Range without listing every logo |
| 13 | `quote` | A client sentence | — |
| 14 | `closing` | The invitation | One specific, low-commitment next step |

**What kills this deck:** a logo wall. Service-line lists. Adjectives where numbers should be. A
close that asks for "an opportunity to present in more detail" — ask for something the reader can
say yes to in one reply.

---

## Diagnosing a storyline that is not working

| Symptom | Usually means | Fix |
|---|---|---|
| Every slide title is a noun phrase | The deck has topics, not claims | Rewrite each title as the sentence the slide proves |
| The deck could be reordered without loss | There is no argument, only sections | Build the arc from the decision backwards |
| The ask appears for the first time on the last slide | The deck was not written toward a decision | Foreshadow it on the agenda and in the section breaks |
| Half the slides are `title-body` | The content was never structured | Re-map each slide to the archetype that matches its job |
| The proof chapter is longer than the problem chapter | Proof before problem | Move proof later, cut it, or sharpen the problem |
| Everything is qualitative | No measurement | Find three numbers with sources, or say plainly there are none |
| It is 26 slides | Two decks fighting | Split it: the argument, and the appendix nobody presents |
| The audience keeps asking "so what" | Claims without consequences | Add the consequence clause to each title |

---

## Speaker notes as pressure release

Every archetype has a capacity, and the honest fix for over-long copy is almost always to move it
into `notes` rather than onto the slide. Notes are for:

- the qualifier that made a title 62 characters,
- how a number was measured, when the caption only has room for what it measures,
- the answer to the objection this slide always provokes,
- what to do if the room pushes back — which slide to jump to.

A slide with three lines and a full note is a good slide. A slide with nine lines and no note is a
document being read aloud.

---

## Presenting the outline for approval

Start from the closest pattern above rather than from nothing, adapt it to the room, then present
the outline as a numbered list of one-line slide purposes with the archetype you intend for each,
and **ask for approval before authoring any IR**.

```
 1. cover          — whose deck this is
 2. agenda         — the map
 3. section-break  — chapter 1: where the programme is losing money
 4. stats          — the size of the loss, measured
 5. title-body     — why it happens
 ...
14. closing        — one territory, one metric, one quarter
```

**Silence is not approval.** An amendment is fine and expected; take it, redo the outline, and
re-show it. Only once the user has approved the outline do you author the IR — authoring first and
re-skinning later does not work, because copy written for 32pt navy titles does not survive a brand
whose titles are 24pt.
