# Vox Photo-Shoot

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
| `locations.py` | 10 location presets + prompt assembly |
| `hf.py` | Higgsfield client (upload, estimate, download) |
| `cast.py` | one-time: generate the model cast |
| `gallery.py` | one-time: location card thumbnails |
| `docs/higgsfield-api.md` | endpoint reference, generated + live-verified |

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

## Known limitations — tell the client these

- **Invented jewellery, roughly 1 image in 10.** A phantom necklace, nose stud or extra
  earring appears despite instructions. Review before delivery; the `detail` framing
  makes it easy to spot.
- **Ages skew old, most strongly on deeper skin tones.** Briefs of 32 and 35 render
  around 45. Ask for about ten years younger than the target age.
- **Pavé renders as a solitaire when the source photo is small.** The client's file is
  640px; a larger product photo should improve stone detail.

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
