# Seedance 2.5 — prompting best practice (for jewellery video ads)

Compiled 2026-09-27 from the four sources below, for the app's image-to-video ads (animate an approved shoot still).

**Sources:**

| Source | What it is |
| --- | --- |
| `https://docs.byteplus.com/en/docs/ModelArk/2222480` | Official ByteDance/BytePlus — Seedance 2.0 series prompt guide |
| `https://docs.byteplus.com/en/docs/ModelArk/2607688` | Official ByteDance/BytePlus — Seedance 2.5 tutorial |
| `https://docs.byteplus.com/en/docs/ModelArk/2607689` | Official hub page — navigation only, no prompting content. Pages are JS-rendered; all three fetched via `r.jina.ai` reader. |
| `https://fal.ai/learn/devs/seedance-2-5-prompting-guide` | fal.ai — Seedance 2.5 prompting guide |
| `https://seadance.io/guide` | Third-party, **not affiliated with ByteDance** — lower authority, use as a secondary cross-check only |

---

## 1. The formula

Official (2.0 guide): **precise subject + action details + scene/environment + lighting & colour tone + camera movement + visual style + image quality + constraints.**

> First lock who is doing what, then where and what atmosphere, then how to shoot, then tighten with style, quality and constraints.

The model splits a prompt into a **spatial layer** (what is in frame) and a **temporal layer** (how it changes) — write "engineering-style instructions", not copywriting.

fal adds an optional block template — use only the sections that solve a real problem, not all of them every time:

```
FORMAT / REFERENCE ROLES / STARTING STATE / TIMELINE / CAMERA / CONTINUITY / AUDIO / ENDING STATE / CONSTRAINTS
```

seadance.io (3rd party): Subject + Motion + Environment + Aesthetics + Camera + Audio; subject + motion are the required minimum.

---

## 2. Image-to-video (first frame) — our mode

- Task type is inferred from inputs: supplying a `first_frame` image selects the first-frame task automatically. Output aspect ratio then follows the first-frame image (ratio-adaptive only — you cannot override it). Duration is 4–30s, or `-1` for auto, on 2.5. (official 2.5 tutorial)
- Official product example (verbatim): *"A strawberry sandwich cookie slowly completes one full rotation while the camera moves smoothly around it and keeps the product centered."*
- The still carries identity and layout — describe the **change over time**, not the subject. Keep subject description to 2–3 stable features.
- Name the ending state: what remains true in the final frame once motion settles (fal).

---

## 3. Timing

- 2.5 official examples use timestamp beats: `"0-3s: …"`, `"Shot 1 [0:00-0:03] – …"`.
- fal: use time blocks when several actions share one shot. Timestamps allocate **proportion, not frames** — the 30s duration setting adds duration, not events. Don't pad a short action to fill a long clip.
- **Conflict to note:** the official 2.0 guide says precise timing (0–3s) is unstable and recommends `Shot 1 / Shot 2` ordering without strict durations instead.
- **Our choice:** use approximate timestamp beats for 5s/10s single takes, one primary change per beat; fall back to Shot-ordering if beats misbehave in testing.

---

## 4. Motion

Official:
- Body-part-specific actions with range, speed and force: *"slowly raise a hand"*, *"slightly lower the head"*.
- Prefer slow, gentle, coherent, subtle motion. Avoid sprints, big jumps, rolls.
- State inertia/continuity between actions: *"use the inertia of turning around to naturally raise a hand"*.
- Express emotion through physical detail, not abstract words (not "she feels confident" — describe the posture/gesture that shows it).

fal:
- Cause before reaction. Break a complex action into **approach → contact → force transfer → settle**.
- Objects don't vanish when occluded — restate the invariants (same face, clothing, object) when something passes out of view.

---

## 5. Camera

Official: the model understands standard terms — medium shot, close-up, wide shot, slow push-in, smooth lateral tracking, fixed shot. **One camera movement per shot** — combining push/pull/pan/move increases instability.

fal: specify screen position, movement trigger, and composition lock. Avoid abstractions like "dynamic".

---

## 6. Constraints (negations at the end — Seedance has no `negative_prompt` field)

Official:
- Subtitles cannot be fully prevented. Add: *"Keep it subtitle-free"*, *"Avoid generating any text or subtitles"*, *"Do not generate watermarks"*, *"Do not generate logos"*.
- **Portrait output generates subtitles noticeably more often than landscape** — relevant to us because our main size is 9:16.
- Face stability line from the official example: *"The character's face remains stable without deformation; movements are natural and smooth, with no stutter or flicker."*

fal: *"No cuts, no slow motion, no repeated action / no duplicated props, no extra hands / no text, no logo, no music."*

---

## 7. Identity & moderation

Official:
- ID drift ("face swapping", resembling a celebrity) can get the video blocked in review. Cause: face too small in the reference, or mixed reference sheets.
- Mitigation: use headshot + full-body refs, define the subject explicitly, put important assets first. Don't use multi-view sheets (→ risk of "twins").
- Keep dialogue in one language (no Chinese/English mixing).

All sources: use common characters, no rare symbols.

**Our spike (2026-09-27):** 6/6 Seedance 2.5 i2v runs on photoreal Indian models passed moderation on Higgsfield.

---

## 8. Parameters & limits

| Param | Value |
| --- | --- |
| Max single-generation duration | 2.5: up to 30s; 2.0: up to 15s |
| Max references | up to 50 total (30 images, 10 video, 10 audio) |
| Ref video/audio length | 4–30s |
| Resolution | i2v: 480p / 720p / 1080p (fal's image-to-video schema; Higgsfield's estimate prices 1080p at $1.1372/s). Reference-to-video: 480p / 720p only. We use 720p. |
| Native audio | default ON — set `generate_audio: false` for silent ads |
| Languages | 11 supported |
| Output format | new `.mov` (H.264 yuv444p, PCM audio) |
| Draft mode | 480p preview, then final render from the draft task id with the same seed/params (official). Higgsfield support for draft mode: **UNVERIFIED** |
| Reference syntax | `@Image1` / `@Video1` in upload order, one job per reference — e.g. *"@Image1 controls only …; do not copy … from @Image1"* |

---

## 9. Measured in our spike (2026-09-27)

Higgsfield `bytedance/seedance-2.5/image-to-video`, 5s, sound off:

- Output 832–834×1112–1114 @24fps for 3:4 stills
- Latency: 142–297s
- Est. cost: $1.73/clip at the promo-free token rate formula
- Optimised prompt correctly revealed the necklace pendant (Kling kept it hidden on the same still)
- Ring close-up looked enlarged/more ornate than the still — possible redesign drift, watch for it on future runs

---

## 10. Our template (what `motion.py` renders for Seedance)

Order: **subject anchor → timestamp beats (one change each, body-part specific, slow) → short scene clause → lighting/tone → exactly one camera movement → fidelity lock → constraints tail.**

Constraints tail (fixed):

> "Keep it subtitle-free. No text, no logos, no watermarks. No cuts, no duplicated people, no extra jewellery. Her face stays stable without deformation; motion is smooth with no flicker."

**Illustrative example (necklace still, not a real production prompt):**

> The model, wearing rose gold circular pendant necklace with diamond pave and chain, stands looking back over her shoulder on a sunlit Pondicherry street with mustard walls and bougainvillea. 0-2s: she slowly turns her shoulders toward the camera. 2-5s: she gently lifts her chin; the pendant rests facing the camera, centred and in focus. The sunlit street behind her stays softly blurred. Warm natural sunlight. The camera slowly pushes in. The pendant, chain and every stone stay identical to the reference image throughout. Keep it subtitle-free. No text, no logos, no watermarks. No cuts, no duplicated people, no extra jewellery. Her face stays stable without deformation; motion is smooth with no flicker.
