# Audio

Music and voiceover. Both are generated locally, both are free, neither needs a key or an account, and
neither sends anything anywhere. What follows is how each one works, what each one can and cannot do,
and where the two meet in the mix.

- [The two halves](#the-two-halves)
- [Music: how it is generated](#music-how-it-is-generated)
- [Music: the honest ceiling](#music-the-honest-ceiling)
- [Mood presets](#mood-presets)
- [Structure, and why a bed is not a loop](#structure-and-why-a-bed-is-not-a-loop)
- [Music: flags that matter](#music-flags-that-matter)
- [Voiceover: the two engines](#voiceover-the-two-engines)
- [Language coverage](#language-coverage)
- [Speech normalisation](#speech-normalisation)
- [ssml-lite: pauses and emphasis](#ssml-lite-pauses-and-emphasis)
- [VO duration drives everything downstream](#vo-duration-drives-everything-downstream)
- [Loudness](#loudness)
- [Sidecars](#sidecars)
- [Limits](#limits)

```sh
ROOT="${CLAUDE_PLUGIN_ROOT:?set CLAUDE_PLUGIN_ROOT to the brand-studio directory, or run this from Claude Code}"
PY="$HOME/.cache/brand-studio/venv/bin/python"
```

---

## The two halves

| | `scripts/make_music.py` | `scripts/make_voice.py` |
|---|---|---|
| Produces | 44.1 kHz stereo bed | 44.1 kHz mono narration |
| Method | procedural synthesis from music theory | neural TTS (Piper) or macOS `say` |
| Cost | none | none |
| Network | never | only to install a voice model, and only when asked |
| Deterministic | yes, byte-identical for a seed | no — neural sampling varies run to run |
| Default loudness | `brand.video.music.targetLufs` (−23 LUFS) | `brand.video.voiceover.targetLufs` (−16 LUFS) |
| Sidecar | `<out>.music.json` | `<out>.voice.json` |

They are mastered separately and mixed by `build_video.py`, which ducks the bed under the narration
using `brand.video.music.duckUnderVoiceDb`. Neither script knows about the other.

---

## Music: how it is generated

There is no music model in this plugin and no music API behind it. A bed is computed from a key, a
mode, a chord progression and a handful of additive-synthesis voices. **That is what makes it
copyright-free** — not a licence, not a permissive dataset, but the absence of any source material at
all. Nothing is sampled, nothing is looped out of a library, and no model trained on recorded music is
involved at any point. The output is a pure function of `(mood, key, mode, bpm, seed, duration)`. The
brand owns it outright, with nobody to attribute and nobody to pay.

The signal chain, in order:

| Stage | What happens |
|---|---|
| **Voices** | `pad` — 5 harmonics, plus two copies detuned ±7 cents, slow shared vibrato. `bell` — inharmonic partials at 1 / 2.76 / 5.4, each decaying exponentially. `sub` — fundamental plus octave, with a trace of the twelfth. `perc` — a kick from a sine swept 110→46 Hz, brushes from twice-tilted white noise. |
| **Envelopes** | per-note ADSR; raised-cosine attack on the pad so it swells rather than starts |
| **Voicing** | triads inverted to stay near the register, so the progression moves stepwise instead of leaping an octave every bar |
| **Tone** | zero-phase spectral shaping per stem: high-pass, high shelf, gentle low-pass. The pad is high-passed at 55 Hz to leave the bottom to the sub; the sub is low-passed at 420 Hz; brushes keep air to 6.2 kHz |
| **Space** | reverb by FFT convolution with an exponentially-decaying seeded noise burst, darkened and energy-normalised; wet amount is per-voice (bass nearly dry, bells furthest back) |
| **Width** | Haas — the right channel of the pad and arpeggio delayed 220 samples — then a mid/side width of 0.72, because a fully-wide bed partially cancels when a phone sums it to mono |
| **Master** | two-pass ffmpeg `loudnorm` to the brand target, true peak held at −2.0 dBTP |

Measured on the default 30 s `confident` bed: 26% of the energy below 60 Hz, 30% from 60–200 Hz, 33%
from 200–800 Hz, 1% above 2 kHz. Crest factor 14.5 dB. That shape is the point — the bed lives under
the range narration occupies, so it can be heard without competing.

## Music: the honest ceiling

**It is an underscore bed, not a produced track.** Four synth voices moving through a chord
progression. There is no melody, no hook, no performance, no dynamics beyond the section arc, and
nothing anybody will hum. Under narration that is exactly right and roughly all a corporate film
needs. Played on its own to a room it will sound like what it is: a tasteful synth pad.

If a film needs a theme, a top line, live players, or anything with a rhythmic identity, licence a
real track. Saying so early is cheaper than saying so after a client review. What this **is** good for:

- narration-led films where music must not compete
- showreels and looping stand exhibits
- anything where clearance, attribution or a per-use licence would be a nuisance
- getting a cut in front of a client at 2 a.m. without a rights conversation

---

## Mood presets

`--list-moods` prints these with their live parameters. Each one is a complete set of musical
decisions, not a label.

| Mood | Mode / key / bpm | Progression | Percussion | Reverb / low-pass | Use it for |
|---|---|---|---|---|---|
| **confident** | A minor, 84 | `i – VI – III – VII` (Am F C G) | brush + kick | 1.9 s / 3200 Hz | The house default. Forward-moving and self-assured rather than sentimental. |
| **calm** | D dorian, 66 | `i – IV – VII – i` (Dm G C Dm) | none | 2.8 s / 2500 Hz | Reflective openings, quietly-narrated pieces. |
| **urgent** | E minor, 104 | `i – VII – VI – v` (Em D C Bm) | brush + kick | 1.3 s / 3800 Hz | A problem beat. Pressure without drama. Not a whole film. |
| **warm** | F major, 74 | `I – IV – vi – V` (F Bb Dm C) | brush only | 2.3 s / 2900 Hz | People-led stories, culture films. |
| **neutral** | A minor, 80 | `i – III – VI – VII` (Am C F G) | brush only | 1.8 s / 3000 Hz | Deliberately unopinionated. When the narration is dense and the music must disappear. |
| **uplifting** | D major, 96 | `I – V – vi – IV` (D A Bm G) | brush + kick | 2.0 s / 3600 Hz | An outcome beat or a showreel. Tips into corporate-cheerful if overused. |

Other parameters each preset carries: voice mix levels (pad / sub / arp / perc), arpeggio density as a
fraction of the eight eighth-note slots per bar (26% on `calm`, 68% on `urgent`), arpeggio octave
spread, per-voice reverb send, high-shelf trim, pad detune spread and vibrato rate.

**The default comes from the brand, not from this file.** `make_music.py` reads
`brand.video.music.mood` and `brand.video.music.avoid` and scores the presets against them. For
Channelplay — mood `confident, understated, corporate-modern`, avoid `dramatic orchestral, lo-fi
hiphop, aggressive EDM` — that resolves to **confident**, and the sidecar records the reasoning in
`moodReason`. Override with `--mood` when a specific beat wants something else.

`--key`, `--mode` and `--bpm` override the preset. Forcing a mode the progression was not written for
can land a degree on a diminished triad (vii° in major, #iv° in lydian); that degree is automatically
moved a third down to a consonant chord and the swap is recorded in `progression.substitutions`.

---

## Structure, and why a bed is not a loop

Four bars repeated N times is what generated music sounds like when nobody thought about form. Every
bed here is composed to an arc instead:

| Section | What plays |
|---|---|
| `intro` | pad alone, establishing the key |
| `build` | sub enters, then the arpeggio halfway through |
| `hold` | full arrangement, under the body of the narration |
| `lift` | busier arpeggio and a lift in level, for a proof or outcome beat |
| `break` | everything drops but the pad — a breath |
| `outro` | elements fall away; the last bar resolves to the root |

Default is `intro,build,hold,outro`, weighted 1.2 / 2.0 / 3.0 / 2.0. Override with
`--structure intro,build,hold,lift,outro`. Sections shorter than a bar are dropped, `break` first.

**Duration is exact.** The bar count is derived from the requested duration and the preset tempo, then
the *tempo* is nudged so the bars land on the duration — a 30 s request at 84 bpm becomes 10 bars at
80 bpm, and the file is 30.000 s. Truncating a phrase to hit a length is audible; a 5% tempo change is
not. The adjustment is reported as `bpmAdjustedPct`.

The last bar is always the root chord, with a long release, so the bed resolves instead of stopping.

---

## Music: flags that matter

```sh
"$PY" "$ROOT/scripts/make_music.py" --duration 42 --brand channelplay --out bed.wav --json
```

| Flag | Why |
|---|---|
| `--duration` | Required. The **finished film length**. Not "about 30". |
| `--brand` | Reads `video.music` for mood, `targetLufs` and the fade times. |
| `--seed` | Default 7. Same seed + same parameters = byte-identical file. Change it to get a different bed with the same character. |
| `--stems` | Writes `<out>.pad.wav`, `.bass.wav`, `.arp.wav`, `.perc.wav`. They sum back to the master (verified to 70 dB below the mix), so an editor can rebalance without re-levelling. |
| `--lufs` | Overrides the brand target. Rarely correct — fix `brand.json` instead. |
| `--out` | `.wav` (default), `.m4a`, `.mp3`, `.flac`. Duration stays exact in every container. |
| `--preview` | Plays it through `afplay`. |
| `--list-moods` | Prints every preset with its live parameters. |

Fade in and out come from `brand.video.music.fadeInSec` / `fadeOutSec` (1.0 s and 2.0 s for
Channelplay). **`build_video.py` applies its own fades on top** when it mixes the bed into a film, so
a bed handed to the builder gets a slightly faster tail than the standalone file. That is harmless; do
not compensate for it by disabling either fade.

---

## Voiceover: the two engines

### Piper — the default

Local neural TTS. It is the reason narration out of this plugin sounds like a person rather than a
screen reader. No API, no key, no per-word cost, and nothing leaves the machine.

- Models live in `~/.cache/brand-studio/voices/`, **never in the repo** — they are ~63 MB each.
- One-time download per language, from `huggingface.co/rhasspy/piper-voices`, md5-verified.
- **It never downloads on its own.** A missing model fails with the exact install command. Pass
  `--allow-download` to permit a fetch mid-run — and never do that in a nightly run, which is the
  whole reason the flag exists.

```sh
"$PY" "$ROOT/scripts/make_voice.py" --list-voices
"$PY" "$ROOT/scripts/make_voice.py" --install-voice hi_IN-pratham-medium
"$PY" "$ROOT/scripts/make_voice.py" --install-voice hi        # picks the best model for the language
```

`--rate` maps to Piper's `length_scale`. Calibrated against `en_US-lessac-medium` on a 61-word
paragraph: `--rate 140 / 165 / 190` measured 145 / 168 / 186 wpm, within about 4%. **A neural voice's
rate is content-sensitive** — a line thick with expanded numbers runs faster than prose — so treat
`--rate` as a request and `effectiveWpm` in the sidecar as the fact.

### macOS `say` — the fallback

Already installed, 184 voices, instant. Noticeably more robotic than Piper, and it is **not only** a
downgrade: it covers seven languages Piper has no model for at all.

Curated preferences per locale, and every novelty voice (`Bad News`, `Bells`, `Jester`, `Superstar`,
and the localised character voices like `Flo (Italian (Italy))`) is excluded from automatic selection.
For `en_IN` the order is `Rishi`, `Aman`, `Tara`.

### How `--engine auto` decides

1. `--voice` names something installed → that engine.
2. A Piper model for the language is installed → **Piper**.
3. `--lang` named a *locale* Piper has no model for but `say` does → **`say`**. Asking for `en_IN` and
   getting an American neural voice is a worse answer than the right accent from a lesser engine.
4. Otherwise `say`, if it has a voice for the language.
5. Otherwise exit non-zero, naming exactly what to install.

The chosen engine and the reason land in the sidecar as `engine` / `engineReason`. Read it — silent
fallbacks are how a Hindi film ends up narrated in English.

---

## Language coverage

**Piper: 49 languages. `say`: 40. Together: 56.**

The ones that matter for an Indian agency:

| Language | Piper | `say` | Use |
|---|---|---|---|
| English | `en_US-lessac-medium`, `en_GB-alba-medium` (both installed) | Samantha, Daniel, Karen | **Piper** |
| English (India) | no model exists | **Rishi, Aman, Tara** | **`say`** — accent beats engine |
| Hindi | `hi_IN-pratham-medium` (+ priyamvada, rohan) | Lekha | **Piper** once installed |
| Bengali | `bn_BD-google-medium` | Piya (`bn_IN`) | Piper for quality, `say` for the Indian variant |
| Marathi | `mr_IN-google-medium` | — | **Piper only** |
| Malayalam | `ml_IN-arjun-medium` (+ meera) | — | **Piper only** |
| Telugu | `te_IN-maya-medium` (+ padmavathi, venkatesh) | Geeta | **Piper** |
| Urdu | `ur_PK-fasih-medium` (+ aegis_female) | — | **Piper only** |
| **Tamil** | **no model exists** | **Vani** | **`say` only** |
| **Kannada** | **no model exists** | **Soumya** | **`say` only** |
| Gujarati, Punjabi, Odia, Assamese | — | — | **Neither. Record with a human.** |

Beyond India:

- **Both engines** (33): ar bg bn ca cs da de el en es fi fr he hi hu id it kk ko nl no pl pt ro ru sk
  sl sv te tr uk vi zh — prefer **Piper**, unless a specific locale only `say` has is required.
- **Piper only** (16): cy eu fa hy is ka ku lb lv **ml mr** ne sq sr sw **ur**
- **`say` only** (7): hr **ja** **kn** lt ms **ta** th

`--list-voices` prints all of this live, including which models are actually on this machine, and
flags every `say`-only language inline.

---

## Speech normalisation

The difference between narration and a screen reader is mostly numbers, and a brand script is full of
them. Expansion happens **before** a single sample is generated, and every substitution is logged in
the sidecar so it is auditable rather than magic.

English, applied in this order — abbreviations first, so the digits they depend on still exist:

| Input | Spoken |
|---|---|
| `4,200 stores` | four thousand two hundred stores |
| `62%` / `7.5%` | sixty two percent / seven point five percent |
| `3.4x`, `2X` | three point four times, two times |
| `$1.2M`, `Rs 4,200`, `₹4.2 crore` | one point two million dollars, four thousand two hundred rupees, four point two crore rupees |
| `12.5 lakh` | twelve point five lakh |
| `FY2024` | financial year twenty twenty four |
| `2019-2023` | twenty nineteen to twenty twenty three |
| `2000` / `2026` | two thousand / twenty twenty six |
| `3rd`, `21st` | third, twenty first |
| `09:30` | nine thirty |
| `18 kg`, `45 mins`, `24 hours` | eighteen kilograms, forty five minutes, twenty four hours |
| `24/7`, `No. 1`, `Q3`, `R&D`, `vs.`, `e.g.`, `Approx.` | twenty four seven, number one, quarter three, R and D, versus, for example, approximately |
| `250%+` | two hundred fifty percent plus |
| `SKU-4200X` | **left alone** — a part number is not a quantity |

Two design rules worth knowing:

- **Standalone years are read as years only in 1900–2099.** Outside that a four-digit number reads as
  a count: `1200` is "one thousand two hundred", not "twelve hundred".
- **Anything unrecognised is passed through untouched.** A wrong expansion sounds fluent and says the
  wrong thing, which is far worse than a synthesiser reading a token literally.

**Non-English is symbol-only, deliberately.** Both engines' phonemisers read bare digits correctly in
the target language, so digits are left for them. What they get wrong is symbols, and symbols are what
can be fixed without knowing a language's number grammar:

| | `%` | `₹` | `&` |
|---|---|---|---|
| hi | प्रतिशत | रुपये | और |
| bn | শতাংশ | টাকা | এবং |
| mr | टक्के | रुपये | आणि |
| te | శాతం | రూపాయలు | మరియు |
| ta | சதவீதம் | ரூபாய் | மற்றும் |
| kn | ಶೇಕಡಾ | ರೂಪಾಯಿ | ಮತ್ತು |
| ml | ശതമാനം | രൂപ | — |
| ur | فیصد | روپے | اور |

Plus es, fr, de, pt, it, nl, ru, tr. Anything else passes through with `normalization.applied` set to
`"none"`, which is recorded rather than hidden. The currency word is placed **after** the number,
which is correct for every language in the table.

Check `normalizedText` in the sidecar before you ship. **That string is what was actually spoken.**

---

## ssml-lite: pauses and emphasis

```
A retail plan is agreed in a boardroom. [pause 600] It is executed by one person.

Compliance rose to [emph]92%[/emph] across 4,200 stores.
```

- `[pause 400]` inserts real silence of that many milliseconds.
- `[emph]...[/emph]` slows that span ~12% and lifts it slightly.
- A blank line is a paragraph break and always inserts 450 ms, markers or not.
- `--pause-scale 1.3` scales every inserted pause, explicit and paragraph alike.

**Without `--ssml-lite` the markers are stripped, never spoken.** That is the failure mode that ends
up in a client's inbox, so it is guarded by default.

Both engines pad every utterance with 50–150 ms of silence; each segment is trimmed before
concatenation so a `[pause 400]` is 400 ms and not 700.

**The cost of emphasis:** implementing a span means synthesising it as its own utterance, so the
sentence is cut into three. Neither engine supports real prosody markup, so a mid-clause `[emph]` on a
single word will sound slightly seamed. **Emphasise a whole clause, not one word inside one.**

---

## VO duration drives everything downstream

`build_video.py` sets each scene's hold to `max(holdSec, slideHoldSec.min, voDuration + 0.4)`. Every
one of those depends on knowing how long the narration actually is, and the estimate from
`words ÷ rateWpm × 60` is a plan, not a promise — it drifts by up to 10% per voice and per line.

So: **the sidecar's `duration` is the number**. Not the word count, not `--rate`.

The sidecar also carries a per-segment timeline with `startSec` / `endSec`, already shifted to account
for the silence trim, plus the original and spoken text for each segment. Captions are written from
the **original** text — a caption says "62%", the voice says "sixty two percent" — and timed from the
segment boundaries.

`wordCount` is the **spoken** count and `sourceWordCount` the written one. They differ a lot on a
numeric script: "Audited compliance rose from 61% to 92% across 4,200 stores" is 10 words written and
17 spoken. Pace against the spoken count.

---

## Loudness

| | Target | True peak ceiling | Why |
|---|---|---|---|
| Music bed | −23 LUFS (`video.music.targetLufs`) | −2.0 dBTP | Sits under narration with room for the film master |
| Voiceover | −16 LUFS (`video.voiceover.targetLufs`) | −1.5 dBTP | Where speech-led social video sits |
| Film master | −16 LUFS | −1.0 dBTP | Set by `build_video.py` |

**They are mastered separately on purpose.** Normalising a mix of speech and music to one target lets
whichever is louder dictate the gain, and the result is either narration that disappears under the bed
or a bed that is inaudible. Mastering each to its own target first means the ducking maths in
`build_video.py` starts from a known level on both sides — that is what makes
`duckUnderVoiceDb: -18` a meaningful number rather than a guess.

Both scripts use **two-pass** `loudnorm`: measure, then apply in linear mode. Single-pass runs a
look-ahead limiter that eats the tail of a short bed and moves its duration, which would break the
exact-duration guarantee outright. The measured result is reported in every sidecar; the music bed
lands on target to within 0.05 LU.

VO is trimmed to under 150 ms of leading and trailing silence (60 ms head, 140 ms tail kept), so a
scene does not open on a beat of nothing.

---

## Sidecars

Both scripts write JSON beside the audio, and both are meant to be read.

`<out>.music.json` — key, mode, bpm (effective and requested), bar count, the progression as roman
numerals *and* chord names, the full structure map with per-section chords and elements, the seed,
every synthesis parameter, measured LUFS / true peak / LRA, and duration error in milliseconds.
Everything needed to regenerate the bed byte-for-byte, or to explain it to a client who asks what the
music is.

`<out>.voice.json` — engine and why it was chosen, voice, language, the original text, the
`normalizedText` that was actually spoken, every substitution with its rule name, the segment
timeline, measured duration, LUFS and true peak, spoken and written word counts, and effective wpm.

---

## Limits

Things these scripts genuinely cannot do. Say so rather than working around them badly.

- **No melody, no hook, no live instruments.** The music ceiling is a good pad. See above.
- **No music-to-picture.** A bed does not hit a cut or land on a reveal. Sections are proportional to
  the whole, not aligned to scene boundaries.
- **Neural VO is not reproducible.** Two runs of the same line differ slightly. The music bed is
  byte-identical for a seed; the voice is not, and there is no seed that would make it so.
- **No voice cloning, and do not ask.** Using a real employee's or client's voice is a consent
  question, not a technical one.
- **No SSML.** `--ssml-lite` is pauses and a rate/pitch nudge. No phoneme overrides, no pitch
  contours, no `<say-as>`.
- **Pronunciation of proper nouns is not controllable.** Piper mispronounces Indian place and brand
  names about as often as `say` does. If a name matters, listen to it and rewrite the spelling
  phonetically in the script until it is right — that is the only lever.
- **Gujarati, Punjabi, Odia, Assamese and most other Indian languages have no voice in either
  engine.** Record those with a human.
- **`say` is macOS-only.** On any other platform Piper is the only engine, and Tamil, Kannada,
  Japanese, Thai, Malay, Croatian and Lithuanian become unavailable.
- **A missing Piper model in a nightly run fails the run.** By design: an unattended job must not
  quietly pull 63 MB. Install languages ahead of time.
