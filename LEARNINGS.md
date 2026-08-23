# Learnings

Things this codebase found out the hard way. Every item here cost either money, a wrong
image delivered to a client, or a bug that looked fine in every test we had.

Read this before changing a prompt, a price, or anything that deploys.

---

## Image models

**The reference image does not carry placement.** A photograph of a ring shows a ring; it
does not say the ring goes on a finger. Every placement string used to be hardcoded to
earrings, so a ring came back as an earring — twice, from two different root causes. The
prose has to say where the piece is worn. `product.Category.placement` exists for this.

**The reference image does not carry scale either.** Nothing in a product photo says
whether a piece is delicate or chunky, which is why competitors ask. `Options.size` is the
one control that decides whether the output looks like *the client's* piece or merely a
plausible one.

**Early tokens weigh far more than late ones.** Expression and lighting were both ignored
at the end of a prompt and obeyed at the front. This is also why the product description
must stay short — it competes with everything after it.

**Negative phrasing gets ignored; positive phrasing holds.** "No jewellery" was ignored
twice on cast portraits. "Her earlobes are completely bare and empty" worked. State the
world you want, not the world you don't.

**The model invents brand marks.** A cast portrait came back with a fabricated "LANORO"
watermark. These portraits are the identity reference for every shoot, so it would have
followed a face into paid client images. Watermarks, logos and lettering need forbidding
explicitly.

**Two instructions for one decision get resolved arbitrarily.** A custom frame plus the
shot's own framing means "extreme close up of the hand" and "her whole figure" in the same
prompt. Never emit both — see `composition.custom_framing`.

**Describe the light, never the rig.** A cast brief asked for "a large softbox key just
above the lens with a silver reflector below" and got a portrait with the softbox and the
reflector both in shot. Equipment named in a prompt is equipment in the frame. Say what
the light does to the face — catchlights, shadow under the cheekbone — and forbid the
hardware in the negative.

**Asking for realism gets you the DMV.** "Natural skin texture with visible pores and no
heavy retouching" under even lighting was meant to avoid plastic AI skin. It produced
twenty-eight documentary headshots that read as ID card scans, and a customer picking a
face for a jewellery campaign rejected six outright. A campaign face needs the word MODEL
in the first line, professional makeup, styled hair and campaign-standard retouching;
distinctive bone structure is what keeps it from looking generic, not a bare face.

**`num_images` produces near-duplicates.** A "shoot" of three has to vary framing
explicitly and give each frame its own seed. A fixed seed per framing also meant a paid
reshoot returned a byte-identical image; `seed_for` adds the attempt.

---

## Substring traps — this has bitten three times

Always use `\b` word boundaries when testing prose:

| looked for | matched | where |
|---|---|---|
| `ring` | ear**ring**s | a fidelity self-check |
| `ear` | n**ear** | the same check |
| ` her` | herringbone parquet | a location plate check |

Three separate incidents, same root cause. `re.search(r'\bher\b', text)`, never
`'her' in text`.

---

## Providers and money

**fal 4K costs exactly 2× 2K.** Measured against the real balance: $0.15 and $0.30 per
image. Not an estimate — one 2K plus one 4K debited $0.45 total.

**fal bills asynchronously.** Per-call balance deltas are useless — the first call read
$0.00 and the second read the first call's charge. Measure a total across several calls.

**`FAL_RESOLUTION` had a 4K tier no UI could reach** for months. Worth grepping for other
capabilities that exist in the provider layer and are unreachable from the app.

**A shoot's price is pinned at reserve time.** `reserved_credits` goes onto the job and
settlement compares against *that*, never a recomputed price. This is what makes changing
the price table safe while work is in flight.

---

## Money correctness

These are the invariants. Breaking any one of them loses money silently.

- **The job row and its credit reserve commit in one transaction.** Job-then-crash gives
  away free work; reserve-then-crash vanishes a customer's credits with nothing to sweep.
- **Idempotency keys derive from the thing that happened**, never from the request:
  `reserve:{job_id}`, `razorpay:{payment_id}`.
- **Key on the payment id, not the webhook event id.** One Razorpay payment emits several
  events with different ids. Keying on the event lets two of them credit the same money
  while the dedupe sits there looking correct.
- **Verify the webhook HMAC over the raw request bytes.** Re-serialising parsed JSON passes
  in dev and fails in prod on key ordering.
- **The browser must never credit anything.** The success handler polls the real balance
  until the webhook lands. A client-side handler that granted credits would be a
  free-credits endpoint wearing a payment flow's clothes.
- **Charge before the provider call.** Generate-then-debit hands free work to anyone who
  kills the connection.

---

## Deploys

**Verify the running image, never the tag.** Claimed "deployed" twice when production was
still serving the previous build. The cheap check is a route that only exists in the new
build: `401` means deployed and protected, `404` means it is not there — with a made-up
route as a control to prove `401` is not a catch-all.

**A successful boot proves the process started, not that it did its job.** The Dockerfile
copied `*.py`, `static/` and `assets/` — never `migrations/`. So `db.migrate()` globbed a
directory that did not exist, applied nothing, returned `[]`, and boot read that as
success. Every deploy for months reported healthy against a schema that had only ever been
applied to RDS by hand. It surfaced the first time a deploy actually needed a new table:
the image shipped code writing to `pieces` with no way to create it. **Any step that can
legitimately do nothing must be able to tell "nothing to do" from "nothing to do it
with"** — `migrate()` now refuses to boot on a missing or empty migrations directory, and
`/healthz` answering finally means the schema was checked.

**Check the artifact, not the recipe.** The bug lived in a `COPY` line that was never
wrong-looking. `docker run <image> ls migrations/` would have found it on day one, and now
does — the same lesson as verifying the running image rather than the tag, one layer down.

**Backgrounded deploys die with their shell.** `nohup ./deploy.sh &` inside a command that
then exits gets killed mid-flight; the log stops somewhere harmless-looking. Use a tracked
background task.

**One list drives both secret sync and secret wiring.** `deploy.sh` once had two hardcoded
key lists. A key added to only one lands in Secrets Manager where nothing reads it, and the
app sees it unset. That is exactly how the Razorpay webhook secret went missing while
looking configured — and the symptom was silent: Razorpay retries for 24h, gives up, and
the customer's credits never arrive while their payment shows successful.

**Never derive a redirect URI from the `Host` header.** It is attacker-controlled.
`PUBLIC_ORIGIN` is a deploy-time constant.

---

## Storage

**App Runner's disk is ephemeral.** Anything written locally is gone at the next deploy.
Uploads were local-only for months, which meant reshooting anything older than the last
deploy returned `410` forever.

**A lifecycle expiry rule deletes work a customer paid for.** `shoots/` and `retouches/`
were on a 30-day timer while the database kept the job rows — so history listed every
shoot with every link dead. Caught with 28 days to spare. Storage is ~$0.025/GB/month;
that is not a number worth deleting a catalogue over.

**Store the S3 key, presign at read time.** A presigned URL outlives neither the
instance-role session that signed it nor a redeploy.

**Ship assets at the size they are used.** Full-resolution PNGs were 454MB of container for
images the browser renders at 250px. Now 10MB. Check what actually consumes an asset before
deciding its resolution — location plates are only ever picker thumbnails; cast portraits
are uploaded to the provider and need the pixels.

---

## Testing

**A `demo()` per module, run as `python <module>.py`.** No framework. It has caught more
real bugs than any other practice here, because it runs against the real database and the
real provider vocabulary.

**A browser catches what a unit test cannot.** In one session: a note that went stale
because a click handler redrew only its own row, a bar reading "1 credits", and a label
rendering "Front 34" instead of a three-quarter view. All three passed every assertion.

**Look at the image.** Twice, output that satisfied every check was visibly wrong — white
type invisible on a bright backdrop, and a logo ghosted because its halo was contrasting
the background instead of the mark.

**Pilot before bulk generation.** Four faces cost $0.60 and caught two problems that would
have ruined all twenty.

**Do not mutate files a background job owns.** Optimising the asset manifests while
`gallery.py` was still running meant it wrote its stale in-memory copy back afterwards,
leaving 49 plates pointing at deleted files. Verify every manifest reference resolves to a
real file rather than trusting a count.

**Assert structure, not counts.** `assert len(ALL) == 10` goes stale the first time someone
adds a location, and teaches whoever hits it to edit the number rather than read the check.

---

## Domain

**Jewellery is not apparel.** Frames and poses are curated per category because "ankle",
"both feet" and "product strap across torso" cannot apply to a ring. A menu of sixty
options where fifty are impossible is worse than eight that all work.

**Skin tone range is a product requirement, not decoration.** Yellow gold reads completely
differently on deep brown skin than on fair. A cast that is one face in eight hairstyles
cannot show a jeweller their piece on the customer they are selling to.

**A backdrop must never contain jewellery.** The boutique location has deliberately empty
vitrines — the plate is also the backplate, so a showroom stocked with other pieces puts a
competitor's necklace in soft focus behind the client's.

**SKU is the difference between a gallery and a catalogue.** A manufacturer with two
thousand designs identifies a piece as RG-4471, not as "ring / signet / udaipur-palace"
plus a sentence a vision model wrote.

**Bridal is the highest-value category** and every original location was outdoors, so there
was nowhere to shoot it.
