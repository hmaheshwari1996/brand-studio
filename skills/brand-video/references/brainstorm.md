# Brainstorming a film — before there is a brief

Read this when the user wants to **think**, not to execute: "we need something for the new client
pitch", "ideas for a brand reel", "what could we do with this case study". The rest of **brand-video**
assumes a brief already exists. This is what happens before that.

The output of this file is a chosen concept and an approved script. Only then does step 3 of the
workflow begin. Do not author a Video IR from a brainstorm.

---

## 1. Shape the problem before generating anything

Four questions. Ask them in this order, in one message, and wait. Concepts generated before these are
answered are decoration.

| Question | Why it changes the output |
|---|---|
| **Who is watching, and where?** | A CMO in a pitch room watches 1920×1080 on a wall with sound, once, with three competitors in her memory. A field supervisor watches 1080×1920 on a phone in a store aisle, muted, between two customers. Those are not the same film at any runtime. |
| **What ONE thing should change in their head?** | This is the film. Everything else is delivery. "They should know we exist" is not an answer; "they should believe their compliance number is worse than their dashboard says" is. |
| **What is the hard constraint?** | Runtime, format, deadline, and — the one people forget — what footage and what data **actually exist today**. A concept resting on a client number nobody has pulled is a proposal to do analysis, not a film. |
| **What must be true for them to believe it?** | This is what the `proof` beat has to carry. Name it now, in one sentence, or the proof scene becomes an unattributed percentage. |

**If the user cannot answer #2, that is the finding.** Say so plainly and stop. Generating concepts
against an unstated change-of-mind produces three films that are all technically fine and none of
which anyone can choose between — and the user will then ask you to pick, which you cannot honestly
do either. Push back once, concretely: offer two or three candidate "one things" drawn from what they
have told you and ask which is closest. That conversation is worth more than any concept.

Question #3 has one answer that is load-bearing and easy to miss: **format is not a per-film switch
today.** `build_video.py` renders at `brand.video.resolution` (1920×1080 for Example Brand) and
`VIDEO.RESOLUTION` is an **error**, not a warning. `brand.video.formats` describes the three delivery
targets; it does not yet reconfigure the build. So if the answer to "where" is a reel, that is a
brand-level change and a question for the brand owner — surface it at brainstorm, not at step 5 with
a rendered landscape film in hand.

---

## 2. The format catalogue

The films this agency actually needs. Pipelines are from SKILL.md — `deck-video` when a deck exists
or the audience wants a walkthrough, `explainer` when the idea needs motion to land.

| Film | What it is for | Runtime | Delivery format | Pipeline | The trap |
|---|---|---|---|---|---|
| **Capability film** | First meeting, website, "what does Example Brand do" | 90–120s | landscape | `deck-video` | Becomes a service list. Nine capabilities, eight seconds each, no proof anywhere. Pick two capabilities and prove one. |
| **New-business pitch film** | Opens one pitch, for one named prospect | 60–90s | landscape (in-room) | `explainer` | Built about Example Brand instead of about the prospect. A pitch film with no number from the prospect's own category is a capability film with the logo swapped. |
| **Case film / client story** | The proof asset; one account, one result | 90–120s | landscape, square cutdown | `deck-video` — the QBR deck already exists | Client approval. Never build this before the client has agreed in writing which numbers can leave the building, anonymised or not. |
| **Brand reel (social)** | LinkedIn and Instagram, top of funnel | 15–30s | vertical (reel), square (LinkedIn feed) | `explainer` | Cutting down a 90s film. See §6 — it is a different craft, not a shorter one. |
| **Training / rollout film** | The field workforce learns one procedure | 45–90s | vertical | `explainer`, or `deck-video` off the training deck | Written for the client who paid, not the promoter who watches. And the `call-to-action` beat is a job instruction ("photograph the shelf before you leave"), never a contact line. |
| **Quarterly readout** | A QBR someone watches instead of attending | 3–5 min | landscape | `deck-video`, `--deck` the real QBR pptx | The only film here that legitimately runs long — which is why it is the one that degenerates into four minutes of a voice reading slides. It still needs the six beats. |
| **Launch / announcement** | New capability, new city, new client win | 30–60s | square + landscape | `explainer` | No `problem` beat. Announcements jump to `outcome` and land as self-congratulation. Give the viewer the reason the thing exists before the thing. |
| **Testimonial** | A client says it instead of us | 45–75s | landscape + vertical pull-quote cut | Neither — this is a production brief | This pipeline does not shoot people. If the film's value is a client's face and voice, say so and stop; what this pipeline can do is the surround — titles, data cards, the lower-third context. |
| **Recruitment film** | Field supervisors, trainers, store staff | 60–90s | vertical primary, landscape secondary | `explainer` | Corporate voice. The audience is the person in the film's world; a film that sounds like the client deck reads as a different company than the one they would join. |

**Runtime is a word budget.** At the brand's 165 wpm (2.75 words/sec), with a 3.0s intro, a 3.5s
outro and 0.4s crossfades between every element:

| Target | Scenes | Narration |
|---|---|---|
| 15s | 2–3 | ~30 words |
| 30s | 4 | ~70 words |
| 60s | 7 | ~150 words |
| 90s | 10 | ~230 words |
| 120s | 13 | ~310 words |
| 4 min | ~24 | ~640 words |

Do this arithmetic **while** you are shaping the concept, not after. A concept whose beats need 400
words is not a 90-second film, and finding that out at step 3 means throwing away the shape, not
trimming adjectives.

---

## 3. Concept generation — the divergence discipline

The failure mode is three variations of one idea presented as three concepts. It happens because the
first idea sets the frame and everything after it is a rewrite. The fix is mechanical: **generate from
a different angle each time, and name the angle.**

| Angle | The move | Example Brand one-liner |
|---|---|---|
| **Problem-first** | Open on the cost the client is already paying | "The display shipped to four thousand stores. In four of ten, it is still in the stockroom — that is the launch budget, on a floor." |
| **Single person** | One promoter, one store, one day; the numbers arrive only at the end | "Follow one supervisor from the ten o'clock opening to the four o'clock report. The film is that shift." |
| **Number-first** | One figure, and everything that follows from it | "Sixty two percent. The film exists to explain that number and then to move it." |
| **Contrast** | The same thing, before and after | "The same shelf, in the same store, eight weeks apart. Nothing else changes on screen." |
| **Process** | How it actually works, end to end | "From the audit photograph to the corrected shelf in seventy two hours. Four things happen in between and none of them are meetings." |
| **Outsider** | What the shopper sees | "She has never heard the word compliance. She just cannot find the pack, so she buys the other one." |
| **Counter-intuitive** | The thing everyone believes that is wrong | "Retail execution is not a training problem. Every person in that store already knows what to do — nobody knows what is actually happening." |

Rules that make this work:

1. **One concept per angle, then kill the weak ones.** Do not generate three and stretch to seven.
2. **If two concepts would open on the same shot, they are one concept.** Not similar — the same. Cut
   one and generate again from an angle you have not used.
3. **The angle is not the concept.** "Problem-first" is a generator; the concept is the specific
   problem, the specific opening, and the specific proof.
4. **Vary the motion too.** A concept whose beats all map to `counter` is a spreadsheet with a
   soundtrack — see `references/motion-templates.md`.

---

## 4. What a concept must contain to be judged

Fixed shape, so three concepts can be compared honestly instead of by enthusiasm. Every field, every
concept, or it is not ready to show.

| Field | What goes in it |
|---|---|
| **Premise** | One line. What the film argues. |
| **Opening line** | The first sentence of narration, written as it would actually be spoken — sentence case, present tense, active, numbers spelled as spoken, no exclamation marks. Not a description of the opening. |
| **Beat map** | Each of the six arc beats: one line of intent, and the motion template that carries it. Do not restate the beat→template table — read `references/motion-templates.md` and cite the template by name. |
| **Proof** | The specific evidence, with a denominator and a date range. "Compliance improved" is not proof. |
| **Runtime** | Scene count, word count, and the arithmetic at 165 wpm against the target. |
| **Needs that do not exist yet** | Footage, data, permission, a client quote. Distinguish *not pulled yet* (a task) from *cannot exist* (a killed concept). |
| **Honest risk** | The thing most likely to make this film not work, named before the user finds it. |

### Worked concept — copy this shape

> **Concept 1 — "Six in ten"** · problem-first angle · `explainer` · landscape · 90s
>
> **Premise.** A retail plan is only as real as the fraction of stores that executed it, and the
> client's dashboard is measuring the plan, not the shelf.
>
> **Opening line.** "Your new display shipped to four thousand stores. In six of them out of ten, it
> went up."
>
> **Beat map** (templates per `references/motion-templates.md`):
>
> | # | Role | Intent | Template |
> |---|---|---|---|
> | 1 | `hook` | The gap between shipped and standing | `kinetic-type` |
> | 2 | `problem` | The planogram as designed, against the shelf as photographed | `compare` (`mode: "wipe"`) |
> | 3 | `problem` | Compliance decays week by week after launch week | `chart-reveal` |
> | 4 | `approach` | Audit, train, fix, re-audit — the loop, not the adjectives | `process-flow` |
> | 5 | `approach` | Who runs it: the route, the supervisor, the evidence photo | `scene` (`variant: "split"`) |
> | 6 | `proof` | Stores audited, weeks to lift, points gained | `counter` |
> | 7 | `proof` | Audited stores against control, over the same eight weeks | `chart-reveal` (`highlightGap: true`) |
> | 8 | `outcome` | Where the loop runs now — states, cities, stores | `coverage-map` |
> | 9 | `outcome` | The client's own sentence about what changed | `scene` (`variant: "quote"`) |
> | 10 | `call-to-action` | One category, one region, one audit | `kinetic-type` → the cached brand outro |
>
> **Proof it rests on.** Shelf-compliance rate for one category across two quarters, audited stores
> versus non-audited, with the store count as the denominator and the date range on screen.
>
> **Runtime.** 10 scenes, ~230 words. 3.0s intro + ~88.0s of scenes + 3.5s outro − 4.4s of crossfades
> ≈ **90s**. Roughly 23 words per scene, which sits at the brand's 5.0s default hold and well under
> the 12.0s ceiling.
>
> **Needs that do not exist yet.** (a) The baseline and lift numbers — they exist in the audit data,
> nobody has pulled them; (b) written client permission to use the category and region, even
> unnamed; (c) one client quote for scene 9 — if it does not come, scene 9 is cut and the film is 82
> seconds, which is fine; (d) a licensed music bed, which is a kit-wide gap, not this concept's.
>
> **Honest risk.** The film hangs entirely on the plan-to-shelf gap being large. **Pull the number
> before writing the script.** If the real baseline comes back above ninety percent, the problem beat
> has no teeth and this concept should be killed rather than softened. Second risk: scenes 3 and 7
> are both `chart-reveal`. If the second one lands flat in review, keep the chart at the problem and
> move the proof entirely onto `counter`.

---

## 5. Presenting concepts to the user

**Three, or at most four.** A list of seven is a way of avoiding a point of view, and it transfers the
work back to the person who asked for help.

- **Lead with a recommendation and say why**, in one sentence, before the concepts. Not "here are
  three options" — "I would make Concept 2, because the proof beat is the only one of the three that
  survives a client asking where the number came from."
- **Make the trade-offs explicit.** Three axes are usually enough, and they usually disagree:

  | | Cheapest to build | Most persuasive in the room | Most reusable |
  |---|---|---|---|
  | What it means | No new footage, no new data pull, motion templates only | Rests on one specific client number | Works for the next client with the category swapped |
  | Costs you | Nothing looks like this client | A pull, a permission, and a delay | Nothing is specific enough to be memorable |

  Say which axis you optimised and which you gave up. A concept that claims all three is not being
  described honestly.
- **Invite merging, explicitly.** The best film is usually one concept's structure with another's
  opening. Say which combinations you think work — "Concept 1's spine with Concept 3's opening line
  is probably the film" — rather than waiting to be asked.
- **Flag any concept that would need brand-level changes** (a vertical render, a supplied intro
  plate, a licensed track) as part of the presentation, not as a footnote after it is chosen.

---

## 6. Reels specifically

A reel is not a short film. It is a different craft that happens to share a brand.

- **No slow build.** The point lands in the first two seconds or the thumb moves. The hook is not
  setup for the point; the hook *is* the point, and the rest earns it retroactively.
- **It is watched muted.** Captions carry the meaning, not the narration. Write the caption first and
  the voiceover second — the reverse of every other film here. the brand's captions are a hard
  42 characters over 2 lines with a 1.2s floor, so a caption that needs three lines is two scenes.
- **Vertical safe area.** In `formats.vertical` the **top 12% and bottom 20% are platform chrome**.
  Nothing legible goes there — captions sit above the lower band, not at the true bottom.
- **It should work as a still.** Pause it anywhere and the frame should still say something. Motion
  that only means something in sequence is wasted here.
- **The plates cost real runtime.** A 3.0s intro plus a 3.5s outro is 6.5s — 22% of a 30-second reel.
  Keep the outro, since it carries the lockup and the ask; consider `"intro": false` and accept the
  `VIDEO.NO_INTRO` warning with a stated reason. It is a warning, not an error.

**Three beats against six:**

| Reel beat | Seconds (of 30) | Job | Arc `role` to label it |
|---|---|---|---|
| **Hook** | 0–2 | The claim, stated flat. No setup. | `hook` |
| **Turn** | 2–18 | The one thing that makes the claim credible — a number, a contrast, a shelf | `proof` |
| **Payoff** | 18–30 | What it means, and the single ask | `call-to-action` |

Labelling only three of the six roles fires `STRUCTURE.STORYLINE` as a **warning** (missing beats).
That is an accepted warning with a reason: *"30-second cutdown; problem, approach and outcome are
carried by the parent film."* Record the reason in the report — never delete beats from
`brand.video.storyline.arc` to silence it.

---

## 7. Handing off

Brainstorming output is **not a brief** until the user has picked one and the script is approved.

1. **The user picks one** — or names the merge. If they say "any of these", they have not picked;
   ask which axis matters and recommend again.
2. **Write the narration as prose**, in speaking order, with beat labels, word count and the runtime
   arithmetic. `references/script-and-vo.md` is the file for this.
3. **Get the prose approved.** Nothing renders until the user has read the words. This is step 3 of
   the workflow and it is not skippable because a concept was thorough.
4. **Then author the Video IR** and continue at **step 4** of SKILL.md.

Carry three things forward from the brainstorm so they are not re-litigated: the chosen format and its
runtime target, the proof and where it came from, and the list of things that still do not exist.

---

## 8. What NOT to do

- **No mood-board language.** "Cinematic", "bold and dynamic", "energy and authenticity". None of it
  is a decision and none of it can be built.
- **No borrowed interest.** If the concept would work for a logistics firm or a bank with three words
  changed, it is not a concept — it is a template with the brand's colours on it.
- **Nothing that needs footage or data that does not exist.** Not-pulled-yet is a task and it belongs
  in the concept's needs list. Cannot-exist is a dead concept — say so and generate another.
- **No exclamation marks**, anywhere, including in the concept write-up. `VOICE.EXCLAMATION` is an
  error and the brand's voice forbids it upstream of the validator.
- **No concept whose whole idea is a visual trick.** A clever transition is not a story. If the
  premise cannot be stated in one flat sentence without describing the animation, there is no premise.
- **No salesy register.** the brand's voice is plain, operational, confident. A concept that needs
  superlatives to sound good is a concept that has no proof.
- **Never present a concept you would not defend.** Padding a list of two good ideas with a third to
  reach three is worse than presenting two, because the user may pick it.
