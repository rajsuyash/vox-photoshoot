# Donna Photoshoot

Upload a jewellery product photo, pick an Indian model and a shoot location, get
campaign-ready images. Higgsfield under the hood.

## Run it

```bash
cp .env.example .env          # add HF_KEY=<key-id>:<key-secret>
uv venv --python 3.13
uv pip install -r requirements.txt
.venv/bin/uvicorn app:app --port 8000
```

Open http://localhost:8000.

## What runs where

| file | role |
|---|---|
| `app.py` | FastAPI: 3 endpoints + static UI |
| `static/index.html` | the whole front end, no build step |
| `shoot.py` | **the only thing the app calls** — one shoot in, images out |
| `product.py` | reads the uploaded piece: what it is, and where it is worn |
| `retouch.py` | the other tool: clean up one product photo, no model, no location |
| `locations.py` | 10 location presets + prompt assembly |
| `hf.py` | Higgsfield client (upload, estimate, download) |
| `cast.py` | one-time: generate the model cast |
| `gallery.py` | one-time: location card thumbnails |
| `docs/higgsfield-api.md` | endpoint reference, generated + live-verified |

## Running it as a business

Sales-led: there is no signup page. You provision a customer, they log in, they spend
prepaid credits, and they get a GST invoice.

**One credit is one generated image.** A shoot is 3, a reshoot is 1, a retouch is 1.
Not a bundle — a partial shoot (two of three framings, which is normal) would otherwise
need integer arithmetic on money and the answer is always slightly wrong. At 1:1 the
refund is exact and the customer sentence is "you pay for images you receive".

| file | role |
|---|---|
| `db.py` | Postgres pool and the migration runner |
| `auth.py` | passwords, sessions, and who may spend a workspace's credits |
| `jobs.py` | jobs, images, and recovering what a dead container left behind |
| `credits.py` | the append-only ledger: reserve, settle, refund |
| `billing.py` | Razorpay invoices and the webhook that credits on payment |
| `admin.py` | provisioning, as a CLI and behind /admin.html |
| `migrations/*.sql` | applied once each, in order, recorded in `schema_migrations` |

### Onboarding a customer

```bash
# From the admin page, or from the terminal:
.venv/bin/python admin.py workspace "Kalyan Jewellers" --gstin 32AABCK1234M1ZP \
    --billing-email accounts@kalyan.com
.venv/bin/python admin.py user priya@kalyan.com --workspace <id> --role owner
.venv/bin/python admin.py grant <id> 100 --note "opening balance"
```

The generated password is printed once and is not recoverable.

### Invariants worth not breaking

- **Reserve and job creation are one transaction.** Job-then-crash gives away free work;
  reserve-then-crash debits a customer for a job nothing will ever sweep, so the credits
  vanish silently.
- **Charge before the provider call.** Generate-then-debit is free work for anyone who
  kills the connection.
- **`UNIQUE (workspace_id, idempotency_key)`** is what makes a double-clicked Generate,
  a retried settle, and a replayed Razorpay webhook all safe. Do not drop it.
- **Every worker write is fenced** with `AND claimed_by = me`. Without it a stalled
  container that resumes overwrites a job that was already failed and refunded.
- **The orphan sweep runs from a request, not a timer.** App Runner throttles CPU
  between requests, so a background loop is exactly what fails to run when needed.
- **`credits.reconcile()`** compares the ledger sum against the newest row's
  `balance_after`. If those ever disagree, something is wrong that day, not at audit.

### Pricing

**₹35 per credit, ex-GST.** A shoot is ₹105, a reshoot or retouch ₹35. Derived in
`billing.py`, not guessed — there are self-checks that fail if the price stops clearing
cost, so a fal price rise or a rupee move cannot quietly put you underwater.

| | per credit |
|---|---|
| fal generation @2K | $0.1500 |
| Anthropic detection (⅓ of a shoot) | $0.0008 |
| S3 + App Runner CPU | $0.0007 |
| forex markup 3% | $0.0045 |
| **marginal** | **₹14.94** |

Plus ~₹2,283/month fixed (App Runner memory, RDS, Secrets Manager). AWS currently bills
**$0** because account credits absorb it; that is costed in anyway, because it comes back.

Contribution is ₹19.23 per credit after the Razorpay fee, so:

| shoots/mo | revenue | profit | net |
|---:|---:|---:|---:|
| 25 | ₹2,625 | −₹840 | −32% |
| **40** | ₹4,200 | ₹0 | **break-even** |
| 100 | ₹10,500 | ₹3,487 | 33% |
| 200 | ₹21,000 | ₹9,257 | 44% |

**Below ~40 shoots a month this loses money**, because the fixed base does not care how
little you use it. That is the number to watch on a single-client deployment.

### Infrastructure

| | |
|---|---|
| App Runner | 0.5 vCPU / 1 GB, **pinned to one instance** until jobs are fully multi-instance safe |
| RDS | `db.t4g.micro`, public endpoint, TLS required |
| Secrets | all four in Secrets Manager, pulled by the instance role at container start |
| S3 | 30-day expiry on `shoots/` and `retouches/` |

`./deploy.sh` builds an immutable `<sha>-<time>` tag, syncs `.env` into Secrets Manager,
deploys, and verifies the live image is the one just built.

## Findings that shaped this (do not undo these)

These were all established by testing against the live API. Each cost credits to learn.

1. **`popcorn/auto` is the only endpoint that preserves the product.** `soul/reference`
   redesigns it; `soul/standard` ignores it entirely. See `fidelity_test.py`.
2. **The model's face must go in `image_urls`.** As prompt text alone it re-rolls a
   different woman every generation, and drifts non-Indian at foreign locations.
   See `identity_test.py`.
3. **Keep product descriptions short.** A longer description naming a "pendant" produced
   a hallucinated necklace and flattened the pavé. See `pave_test.py`.
4. **Negative prompts are unreliable.** "Do not add a necklace" failed in four separate
   runs. Positive phrasing ("her neck and collarbone are bare") is the load-bearing fix.
5. **One reference photo beats two.** Adding the back view muddied the geometry.
6. **`num_images` returns near-duplicates.** Variety comes from separate calls with
   different framings and seeds — that is what `shoot()` does.
7. **No image model offers 4:5.** Generate 3:4 and crop for Instagram.
8. **The official Python SDK's `upload_file` is broken** — it sends an empty
   `x-amz-tagging` header and S3 rejects the signature. `hf.upload` replaces it.
9. **Say what you want, never what you don't.** "No jewellery" and "do not add a
   necklace" both failed repeatedly. "Her earlobes are completely bare and empty" works.
10. **Do not phrase it as bare skin.** "Her neck and collarbone are completely bare"
   removed the necklace but also removed the saree — the model read it as wardrobe.
   Target the jewellery, then restate the wardrobe explicitly.
11. **The reference image does not carry placement — the prose does.** A ring uploaded
   as reference #1 came back as an earring hanging from the ear, because every framing
   and craft line said "earring". `product.identify` now reads the piece off its own
   photo (Claude Haiku, ~$0.002 a shoot) and `product.CATEGORIES` supplies the placement,
   the three crops and the negative hint for that body part. If the call fails for any
   reason it falls back to the client's earrings, so a shoot never dies on detection.

## Detected vs asked

Two kinds of fact go into a prompt, and they come from different places.

**Detected** — category, sub-type, description. All visible in the photograph, so
`product.identify` reads them and the client never types anything. Sub-type matters
more than it sounds: a stud sits flat on the lobe, an ear cuff clips the outer rim with
no piercing at all, and no amount of "earrings" gets you from one to the other.

**Asked** — size, and for a ring the finger and hand. **A product shot on a table
carries no scale reference**, so nothing can infer it. Without the size sentence the
generator renders a plausible ring rather than *this* ring, which is why the size
control is anchored to a body part ("about as wide as her fingernail") instead of
millimetres — the model cannot use millimetres, and the client does not know them.

Plus a free-text `instructions` box, which lands after the craft rules so it can beat
them: "keep the brushed matte finish, do not polish it" works.

| | asked for | anchored against |
|---|---|---|
| ring | size, finger, hand | fingernail |
| earrings | type, size | earlobe |
| necklace | size | collarbone |
| bracelet | size | wrist |

The API is two calls, because which controls to show depends on what the piece is:
`POST /api/pieces` (photo in, category + type + description + control spec out), then
`POST /api/shoots` with the client's answers. `GET /api/categories` lets the UI
re-render its controls when the client corrects the category.

## Retouch

The second tool. One product photo in, the same piece back without the desk, the dust,
the fingerprints and the colour cast. No model, no location, one image out.

| control | why |
|---|---|
| mode — faithful / studio / vivid | how much licence the retoucher gets: clean only, relight on a sweep, or campaign punch |
| gemstones — keep true / polish | **defaults to keep true**, unlike every competitor. Inclusions are what make a stone read as a real stone; polishing them out is what makes catalogue images look synthetic |
| background — keep / white / grey / black | `keep` adds nothing to the prompt at all, so it cannot quietly restage the shot |
| instructions | placed last so it beats the mode — "leave the patina alone" has to win against "relight with crisp highlights" |

Output keeps the shape the client shot: `providers.Provider.nearest_aspect` maps the
source dimensions onto the closest ratio the provider accepts, rather than forcing 3:4
and cropping the piece.

**A retouched photo is a better shoot reference.** The oldest known limitation here is
that pavé collapses into a single stone when the source file is small and dirty, because
the generator cannot resolve the individual stones. The results pane puts a *Shoot this*
button on a retouch for exactly that reason — it feeds the cleaned image back in as the
product reference.

Same fidelity risk as the shoot, handled the same way: every mode leads with
`retouch.FIDELITY`, which says in as many ways as possible that this is a retouch of one
photograph and not a new design.

## Adding a category

`product.CATEGORIES` is the only place to edit. Each entry needs a `placement`
("on her right wrist"), a `craft` line saying what must stay bare, a `negative` naming
the other categories, a `scale` of five sentences from `xs` to `xl` anchored to a body
part, a `types` map of sub-styles, and `asks` listing which controls the UI should
show. Framings go in the block below the table, under the keys `hero` / `profile` /
`detail` — those three keys are fixed, because `shoot.SEEDS`, `app.merge_images` and
the reshoot endpoint all address a frame by name. The vision call's `category` and
`type` enums are derived from `CATEGORIES`, so they update themselves. Run
`python product.py`.

## Known limitations — tell the client these

- **Invented jewellery, roughly 1 image in 10.** A phantom necklace, nose stud or extra
  earring appears despite instructions. Review before delivery; the `detail` framing
  makes it easy to spot.
- **Ages skew old, most strongly on deeper skin tones.** Briefs of 32 and 35 render
  around 45. Ask for about ten years younger than the target age.
- **Pavé renders as a solitaire when the source photo is small.** The client's file is
  640px; a larger product photo should improve stone detail. Detection reads the setting
  off the same small file, so it can call a pavé cluster a solitaire and put that in the
  prompt. The detected line above the results is there so this is visible.
- **The cast portraits are head-and-shoulders, with no hands.** For a ring or a bracelet
  the identity reference cannot anchor the `detail` crop, which is hand-only. Faces still
  hold on `hero`; expect the macro frame to be a generic hand.

## Costs

Roughly 1.5 credits per image, so about 4.4 per 3-image shoot. Check before spending:

```python
hf.estimate('/higgsfield-ai/popcorn/auto', arguments)   # free, returns credits + usd
```

## Deploying to AWS

The app is a thin CRUD layer — Higgsfield does the GPU work — so it stays small.

- **S3** for uploads and generated output. Higgsfield deletes its copies after ~7 days,
  so copy completed images out.
- **App Runner** (or Lambda behind API Gateway) for `app.py`.
- **DynamoDB** replaces the in-memory `JOBS` dict, keyed by job id.
- **Webhooks over polling** for production: pass `?hf_webhook=<https-url>` on submit and
  keep polling only as a recovery path. Higgsfield retries for two hours.

Concurrency, not requests-per-second, is the real limit — track each `request_id` until
terminal before submitting more.
