# PRD: AI Jewellery Photoshoot SaaS on Higgsfield[^1][^2]

## 1. Product Vision

The product is a verticalized SaaS platform that lets jewellery brands generate professional, on‑model and lifestyle product photoshoots from a single high‑quality jewellery image, using Higgsfield and complementary image processing pipelines. It replaces traditional studio shoots with AI‑powered, brand‑faithful visuals while preserving exact jewellery fidelity (stones, metal, proportions) across all generated images.[^2][^3][^4]

Core value proposition:
- Turn one well‑shot jewellery product photo into an entire campaign (studio, catalogue, lifestyle, on‑model) without physical photoshoots.[^3][^2]
- Ensure product fidelity: stone count, metal colour, engravings and proportions match the real SKU, avoiding misrepresentation and returns.[^5][^2]
- Provide a no‑prompt, button‑based UX tailored to jewellery marketers (templates, simple configuration, safe defaults).[^6][^7]

Problem it solves:
- Traditional jewellery shoots are expensive (often thousands of dollars per campaign) and slow (weeks from planning to delivery).[^8][^2]
- Generic AI image tools hallucinate jewellery details and drift colours because they are not product‑aware.[^4][^2]
- Existing vertical tools are closed and not oriented to programmatic workflows or agent integration; brands need a controllable, API‑first system they can integrate into their stacks.[^7][^6]

## 2. Target Customers

Likely ICP segments:
- Jewellery manufacturers: Need catalogue and B2B sales imagery for hundreds of SKUs and variants; value batch workflows and fidelity.[^8]
- Jewellery retailers: Require e‑commerce and marketplace‑ready images (white background, lifestyle, on‑model) at scale.[^9][^3]
- D2C jewellery brands: Focus on campaign variation and A/B testing of creative across markets (London vs Dubai vs Mumbai aesthetics).[^4][^8]
- Luxury jewellery brands: Demand high aesthetic quality and strict product integrity; prefer curated templates and controls.[^8]
- Jewellery marketplaces: Need standardized packshots, scale cues, and white‑background compliance at volume.[^3][^9]
- Jewellery marketing agencies: Operate production workflows for multiple clients; require API access, templates, and analytics.[^8]

MVP prioritization (recommended):
- Primary ICP for MVP: D2C jewellery brands and small/medium retailers that already use online channels and are open to AI workflows.[^2][^4]
  - They feel the pain of campaign creation and have enough SKUs to benefit from templated generation.
  - They are more tolerant of new tooling and credit‑based pricing than enterprise luxury houses.
- Secondary ICP: Jewellery marketing agencies and marketplaces for V1/V2 when APIs, multi‑tenant features, and SLAs mature.[^8]

Assumption: Domain‑specific fine‑tuning is not required in MVP because Higgsfield’s Soul family and high‑aesthetic models can provide baseline fashion‑aware output when conditioned properly, while jewellery fidelity is enforced via segmentation, masking, and compositing.[^10][^5]

## 3. User Journeys

### First‑time user

1. Land on marketing site → click "Start free" → sign up (email + password or SSO).
2. Create organization (brand name, country, currency) and accept terms.
3. Land in dashboard with "Upload your first product" empty state CTA.
4. Upload a high‑quality jewellery product photo (guided checklist, automatic quality checks).[^2][^3]
5. Configure jewellery product details (type, metal, stone, SKU code, collection).
6. Choose a photoshoot template (e.g., "Minimal studio", "Bridal", "Instagram campaign").[^2][^8]
7. Adjust simple configuration (model presence on/off, location aesthetic, number of images, aspect ratio).[^11]
8. Start generation → see progress for each image (queued, processing, completed).
9. Review generated gallery → mark favourites, flag issues, optionally regenerate specific frames.
10. Download images (individual or zip) → see credits consumed.

### Returning user

1. Login → land on dashboard with aggregated stats (total products, photoshoots, credits).
2. View product library; search by SKU, collection, tag.
3. Open an existing product → view associated photoshoot projects.
4. Duplicate an existing photoshoot configuration or create a new one.
5. Generate additional scenes or variations for existing SKUs.

### Creating a new photoshoot

1. Start from product library → select jewellery product.
2. Click "New photoshoot".
3. Choose template (e.g., "Website hero" or "Valentine’s Day").
4. Configure:
   - Images per shoot (e.g., 4–8).
   - Aspect ratio(s) (1:1, 4:5, 9:16, 16:9).[^12][^1]
   - Resolution (up to 4K in later versions; MVP may cap at 2K).[^1][^12]
   - Model options: on/off, gender, skin tone cluster, age range, vibe (e.g., "modern Indian bridal").
   - Location aesthetic: city (London, Paris, Dubai, Mumbai, etc.) and environment (studio, wedding, party).[^8]
5. Confirm credit cost estimate.
6. Submit generation job (asynchronous).
7. Receive in‑app notification when complete.
8. Review gallery, tag images, approve or regenerate.

### Creating multiple variations

1. Within photoshoot results, click "Create variations" on a selected hero image.
2. Choose variation axes: background only, pose change, location change, outfit/mood change.
3. System generates new variants with product locked via mask/composite pipeline.
4. Variants appear grouped under the base image for comparison.

### Regenerating an image

1. From gallery, click on a specific image.
2. Use "Regenerate" → adjust configuration (e.g., new pose, slightly different lighting).
3. System reuses same jewellery input and template but changes requested parameters.
4. New generation replaces or appends to the previous image.

### Downloading a photoshoot

1. In photoshoot view, select images or "Download all".
2. Choose format: JPG/PNG, resolution downscale if needed.
3. System generates zip and provides download link (signed URL with limited TTL).

### Managing previously generated photoshoots

1. Dashboard → "Photoshoots" tab.
2. Filter by product, template, date range, performance (e.g., downloads).
3. Open photoshoot details: configuration summary, gallery, credit usage.
4. Archive, duplicate or edit configuration.

## 4. UX / UI Specification

### Global design principles

- Clear, step‑wise flows that hide complexity behind defaults and templates.[^6][^4]
- Strong guidance around product image quality (checklist, automatic warnings).[^3][^2]
- No manual prompt entry in MVP; structured forms only.

### 4.1 Dashboard

Purpose: Summarize account activity and provide entry points into products, photoshoots and billing.

UI components:
- Top navigation: Dashboard, Products, Photoshoots, Templates, Credits & Billing, Settings.
- KPI cards: Products uploaded, Photoshoots created, Images generated, Credits remaining.[^13][^5]
- Recent activity list: last photoshoots, generation status.
- Primary CTA: "Upload product" when no products; "New photoshoot" when products exist.

User actions:
- Navigate to main sections.
- View credits and usage summary.
- Jump into recent photoshoots.

Inputs: None beyond navigation.

Outputs:
- Summary metrics.
- Navigation state.

Validation: N/A.

Empty states:
- "No products yet" with instructions and link to upload flow.
- "No photoshoots yet" until first generation.

Loading states:
- Skeleton loaders for KPI cards and activity list.

Error states:
- Generic error banner if API for statistics fails; user can retry.

### 4.2 Product Library

Purpose: View and manage jewellery products as the core entities.

UI components:
- Table/grid of products: thumbnail, SKU, name, type (ring, necklace), metal and stone summary.
- Filters and search: text search, type filter, collection filter.
- CTA: "Upload product".

User actions:
- Browse, search, filter products.
- Open product details.
- Upload new product.

Inputs:
- Search term, filter selection.

Outputs:
- Product list, filtered results.

Validation:
- Verify pagination and filters respond correctly.

Empty state:
- Quick explanation and "Upload product" CTA.

Loading:
- Skeleton rows.

Error:
- Table‑level error message.

### 4.3 Product Upload

Purpose: Ingest source jewellery images and create product records.

UI components:
- Drag‑and‑drop upload area with file requirements (min resolution, plain background, no heavy shadows).[^3][^2]
- Progress bar and file list.
- Quality hints: warnings if resolution too low or background too busy.

User actions:
- Select/upload images.
- Continue to jewellery product setup.

Inputs:
- Image files (JPG/PNG), optional SKU code.

Outputs:
- Upload success, stored file IDs, product creation form.

Validation:
- File type, size, min resolution; show warnings for poor quality.

Empty:
- Guidance copy ("Shoot against plain neutral background, diffuse light").[^2][^3]

Loading:
- Upload progress.

Error:
- Failed upload message; retry option.

### 4.4 Jewellery Product Setup

Purpose: Describe the product metadata and prepare it for generation.

UI components:
- Form fields: Product name, SKU, collection, type (ring, necklace, earrings, etc.), metal type and colour, stone type(s), stone count (optional), notes.
- Preview of uploaded image.

User actions:
- Fill metadata and save.

Inputs:
- Text, select dropdowns, numeric fields (stone count).

Outputs:
- Saved product record.

Validation:
- Required fields (name, type, metal, image); numeric fields; basic sanity checks (stone count >0 if provided).

Empty:
- Pre‑filled suggestions from filename or EXIF if present.

Loading:
- Save spinner.

Error:
- Field‑level validation errors; server errors with generic message.

### 4.5 Model Selection

Purpose: Choose whether a human model should be present and, if so, a configuration preset.

UI components:
- Toggle: "Show on model" on/off.
- Preset cards: "Studio model", "Lifestyle model", "Bridal model".

User actions:
- Toggle model presence, select preset.

Inputs:
- Boolean and preset choice.

Outputs:
- Model configuration object (e.g., on_model: true, preset: bridal).

Validation:
- None beyond required when on_model is true.

Empty:
- Explanation of pros/cons of on‑model vs product‑only shots.[^3][^8]

Loading:
- N/A.

Error:
- N/A.

### 4.6 Model Customization

Purpose: Simple configuration of model attributes without overwhelming the user.

UI components:
- Collapsible section active only when "Show on model" is on.
- Dropdowns: Gender (female/male/non‑binary), Age band (20s/30s/40s), Skin tone palette (light/medium/deep with example chips), hair style presets (updo, loose waves, bun), pose families (front‑facing, three‑quarter, side).[^8]

User actions:
- Adjust attributes as needed.

Inputs:
- Structured selections.

Outputs:
- Model configuration details for prompt construction and possible Soul ID use later (V2).

Validation:
- Ensure default preset exists; no field is required.

Empty:
- Default model preset selected.

Loading:
- N/A.

Error:
- None; fallback to defaults.

### 4.7 Background Selection

Purpose: Choose background type and mood.

UI components:
- Background type cards: Studio, Luxury interior, Wedding, Party, Outdoor, Festive, Custom.
- Sub‑options per card (e.g., "Minimal white", "Velvet plinth", "Marble surface").[^3][^8]

User actions:
- Pick background type and variant.

Inputs:
- Background category and variant.

Outputs:
- Scene configuration segment.

Validation:
- At least one background per photoshoot.

Empty:
- Recommended default ("Minimal studio" for e‑commerce).[^3]

Loading:
- N/A.

Error:
- N/A.

### 4.8 City / Location Selection

Purpose: Add regional aesthetic context.

UI components:
- Dropdown or card list: London, Paris, Dubai, Mumbai, New York, Singapore, Other.
- Optional description chips ("European townhouse", "Indian wedding mandap").[^8]

User actions:
- Select city aesthetic.

Inputs:
- City, descriptor.

Outputs:
- Location configuration.

Validation:
- Optional; default "Neutral".

Empty:
- "Neutral" selected by default.

Loading:
- N/A.

Error:
- N/A.

### 4.9 Photoshoot Configuration

Purpose: Single configuration screen summarizing all parameters.

UI components:
- Summary panel of product + image preview.
- Sections for Template, Model, Background, Location, Shot type, Count, Aspect ratio, Resolution.
- Credit cost estimator.

User actions:
- Review and tweak; start generation.

Inputs:
- Aspect ratio(s), resolution tier, number of images, shot type (close‑up, lifestyle, on‑model), mood (e.g., "warm, romantic").

Outputs:
- Complete configuration payload for generation job.

Validation:
- Basic constraints: max images per shoot (e.g., 12), supported aspect ratios/resolutions.[^12][^1]

Empty:
- Template defaults filled.

Loading:
- N/A.

Error:
- Show any missing data.

### 4.10 Resolution / Aspect Ratio Selection

Purpose: Configure output format without confusing users.

UI components:
- Radio buttons for common ratios (1:1, 4:5, 9:16, 16:9).[^1][^12]
- Resolution tiers: Standard (1080p), High (2K), Ultra (4K – V1 or V2 only).[^12][^1]

User actions:
- Select ratio and resolution tier.

Inputs:
- Ratio, resolution.

Outputs:
- Rendering size configuration.

Validation:
- Enforce max resolution based on plan.

Empty:
- Default: 4:5 at 2K for e‑commerce hero.

Loading:
- N/A.

Error:
- Display credit impacts when user attempts unsupported resolutions.

### 4.11 Generation Screen

Purpose: Show job progress and allow monitoring.

UI components:
- Job status card: queued, running, completed, failed.[^14][^15]
- List of individual image jobs with thumbnails as they complete.
- Notifications for completion.

User actions:
- Watch status; navigate away if desired.

Inputs:
- None.

Outputs:
- Status updates.

Validation:
- None.

Empty:
- Display "Generating your photoshoot" with tips.

Loading:
- Progress spinners.

Error:
- Per‑image failure banners with option to retry.

### 4.12 Results / Gallery

Purpose: Review generated images, manage regeneration, and download.

UI components:
- Grid of thumbnails, selectable.
- Detail view with image, configuration summary, regenerate button, download button.

User actions:
- Select favourites, flag issues, regenerate, download.

Inputs:
- Image selection, action triggers.

Outputs:
- Download links, regeneration jobs.

Validation:
- Confirm credit spending for regenerations.

Empty:
- Message if all jobs failed.

Loading:
- Lazy‑load thumbnails.

Error:
- Show error icons for failed images.

### 4.13 Image Regeneration

Purpose: Fix or iterate on individual frames without rerunning entire shoot.

UI components:
- Regeneration modal: highlight current image, allow minor config tweaks (pose, background variant, mood).

User actions:
- Adjust configuration; submit regeneration.

Inputs:
- Tweaked parameters.

Outputs:
- New generation job tied to same product and base reference.

Validation:
- Enforce max regeneration per image in free plans.

Empty:
- Pre‑loaded current settings.

Loading:
- Status indicator.

Error:
- Message if regeneration fails.

### 4.14 Download / Export

Purpose: Export images.

UI components:
- Multi‑select grid.
- Format options (JPG/PNG), resolution downscale.

User actions:
- Select and download.

Inputs:
- Selection and format choices.

Outputs:
- Zip file URL.

Validation:
- Limit total payload size where necessary.

Empty:
- N/A.

Loading:
- Download progress.

Error:
- Download failed message.

### 4.15 Account / Settings

Purpose: Manage profile, organization, and preferences.

UI components:
- Account details form.
- Organization settings (logo, brand colours, default templates).

User actions:
- Update info.

Inputs:
- Text, file uploads.

Outputs:
- Updated settings.

Validation:
- Email format, required fields.

Empty:
- Placeholders.

Loading:
- Save spinner.

Error:
- Field‑level errors.

### 4.16 Credits / Subscription

Purpose: View plan, credits, and billing.

UI components:
- Current plan card (e.g., Starter, Plus, Ultra equivalent).[^16][^13]
- Credit balance and usage chart.
- Upgrade buttons.

User actions:
- Upgrade plan, buy top‑up credits.

Inputs:
- Plan choice, payment details.

Outputs:
- Updated plan and credits.

Validation:
- Payment API responses.

Empty:
- N/A.

Loading:
- Billing API spinners.

Error:
- Handling failed payments.

## 5. Photoshoot Configuration System

Configuration dimensions considered:
- Number of images per shoot.
- Aspect ratio.
- Resolution.
- Shot type (product‑only, on‑model, lifestyle close‑up).
- Model presence and attributes.
- Pose family (ear, neck, hand, wrist placements).[^3][^8]
- Outfit style (casual, formal, bridal).
- Lighting (soft studio, warm sunset, neutral daylight).[^8]
- Background type and variant.
- Location aesthetic.
- Mood (e.g., romantic, minimal, festive).
- Jewellery placement (left hand vs right, neck positioning).

Recommended user‑exposed controls (MVP):
- Number of images (1–8, slider).
- Aspect ratio (limited list of 3–4 options).
- Resolution tier (Standard vs High; hide exact pixels but reflect credit impact).
- Shot type (product‑only vs on‑model vs lifestyle).
- Template selection (pre‑configured combos of background, mood, lighting).
- Model presence toggle + simple presets (gender, skin tone, broad vibe).
- Location aesthetic.

Controls to keep behind intelligent defaults:
- Detailed camera angle (eye‑level vs overhead) — derive from template and shot type.[^3]
- Detailed pose coordinates — use internal pose libraries and ControlNet style conditioning.[^17][^18]
- Micro‑lighting parameters — set per template for consistency.[^5][^8]
- Jewellery placement specifics — internal rules per jewellery type (earrings at lobe, rings at finger base).[^2][^3]

Rationale: Non‑technical jewellery marketers prefer curated templates that guarantee brand‑safe outcomes without complex sliders; fine‑grained controls can be added as "Advanced" later.[^4][^2]

## 6. AI Generation Architecture Around Higgsfield

### Confirmed Higgsfield capabilities

- Higgsfield provides an asynchronous API for image, video, audio and 3D generation via model‑specific endpoints with JSON parameters; each request returns a `request_id` and `status_url` for polling, or webhooks for completion.[^15][^14]
- Base URL: `https://platform.higgsfield.ai` with server‑side key/secret authentication.[^14][^15]
- Official Python and TypeScript SDKs exist for integration.[^19]
- Models include high‑aesthetic image generators such as Soul 2.0 (photo foundation model), Nano Banana, FLUX, GPT Image and others, with up to 4K native resolution and standard aspect ratios.[^10][^1][^12]
- Soul 2.0 and related Soul models support text‑to‑image with optional reference images, and Soul ID enables character‑consistent faces via training on 20+ photos.[^20][^21][^10]
- MCP integration allows Claude Code and other agents to call Higgsfield without manual API key management, but the underlying API still uses server authentication for production systems.[^22][^15]

Assumptions (explicit):
- Higgsfield image models accept a reference image parameter for conditioning (confirmed for Soul family and some general image models).[^21][^10]
- Higgsfield may not currently expose full ControlNet‑style conditioning (canny, depth) out of the box; any ControlNet usage would be via a separate pipeline (e.g., Stable Diffusion on own infra).[^23][^17]
- Product‑specific segmentation and masking is not provided by Higgsfield directly; must be implemented using SAM or similar segmentation models in our backend.[^24][^5]

### High‑level architecture

- Frontend (SPA) communicates with Backend (REST/GraphQL) over HTTPS.
- Backend manages:
  - Image uploads and storage (S3 or equivalent).
  - Product segmentation and mask generation (SAM or similar).[^5]
  - Prompt and parameter construction.
  - Submission of generation jobs to Higgsfield API.
  - Queue management for asynchronous jobs.
  - Storage of generated outputs and metadata.
  - Webhook or polling to retrieve results.[^15][^14]

### Frontend ↔ Backend communication

- Frontend calls backend APIs to:
  - Upload product images.
  - Create products and photoshoot configurations.
  - Trigger generation jobs.
  - Poll job status.
  - Fetch generated image URLs.
- All calls authenticated via JWT (session token) with organization context.

### Image upload and storage

- Frontend uploads images via signed URL or direct multipart upload to backend.
- Backend stores original product images in object storage under `/products/{org_id}/{product_id}/source.jpg`.
- On upload, backend runs:
  - Basic validation (resolution, format).
  - SAM segmentation to isolate jewellery and create mask(s).[^24][^5]
  - Optional preprocessing (background neutralization).
- Masks and processed images stored alongside original.

### Higgsfield API integration

- Backend uses Higgsfield Python SDK or direct HTTP calls to:
  - Submit generation requests to chosen model endpoint (e.g., Soul image endpoint).[^15][^12]
  - Include text prompt and reference image ID as inputs.
  - Receive `request_id` and `status_url`.
- For production, backend registers a webhook endpoint in Higgsfield settings to receive completion notifications.[^14]

### Prompt construction

- Backend composes prompts from structured configuration (see section 8), e.g.:
  - System prompt: "You are an AI image generator for jewellery product photography, preserving exact jewellery appearance from the reference image."
  - Scene prompt combining model, background, location, mood and shot type.
  - Negative prompts for jewellery hallucinations ("do not change stone count, metal colour or shape; no redesign of jewellery").[^25][^26]

### Parameter mapping

- User selections map to:
  - `prompt`: main text description.
  - `aspect_ratio`: e.g., `4:5`, `1:1`, `9:16`.[^1][^12]
  - `resolution` or `quality`: 1080p vs 2K vs 4K.
  - `reference_image_id`: ID of uploaded product composite.
  - Optional `soul_id` for character consistency in future versions.[^20][^21]

### Generation job queue

- Backend maintains a jobs table.
- When a photoshoot is started:
  - Create one job per requested image with configuration snapshot.
  - For each job, submit request to Higgsfield.
  - Store `request_id` and `status_url`.

### Asynchronous processing

- Polling path:
  - Scheduled worker periodically polls Higgsfield `status_url` until jobs reach `completed` or `failed`.[^19][^14]
- Webhook path:
  - Higgsfield sends POST to our webhook with job status and output URLs.
  - Backend marks jobs completed and stores generated image URLs.

### Generation failure handling

- If Higgsfield returns error state or job fails:
  - Mark job as failed with reason.
  - Surface error in UI with option to retry.
  - Implement retry logic with exponential backoff for transient errors (timeouts, rate limits).[^27][^19]

### Generated image storage

- Download output images (Higgsfield output URLs valid for at least seven days).[^15]
- Store final images in our own object storage `/photoshoots/{project_id}/{image_id}.jpg`.
- Maintain separate storage for thumbnails.

### User notification

- In‑app notifications: websockets or polling to inform when a photoshoot is complete.
- Email notifications optional (V1/V2).

### Retry workflow

- Manual retry: user clicks "Retry" on failed image.
- Automatic retry: first transient failure auto‑retries once; repeated failures mark job as failed.

## 7. Jewellery Preservation Strategy

Jewellery fidelity is the highest priority; the architecture must minimize hallucinations (stone count, metal color, shape) while maintaining quality and reasonable latency.[^5][^2]

### Techniques considered

- Reference‑image conditioning: Using product photo as conditioning image (IP‑Adapter style) so the model preserves visual characteristics.[^26][^5]
- Image‑to‑image generation: Starting from product composite and modifying environment only.[^5]
- Product masking and segmentation: Using SAM to generate precise jewellery masks and lock product pixels.[^24][^5]
- Inpainting: Masking background region and inpainting new environment while preserving product region.[^28][^24]
- ControlNet‑style approaches: Edge or depth conditioning to preserve silhouette and structural detail, particularly for rings and complex earrings.[^23][^17]
- Reference adapters: IP‑Adapter or similar for product identity anchoring.[^26]
- Multi‑stage generation: First stage background + lighting, second stage relighting and refinement.[^29][^5]
- Jewellery detection: CLIP‑based or classifier detection to ensure the jewellery exists and is correctly placed.[^30]
- Background replacement pipelines: Segmentation + ControlNet inpainting + relighting for product background swaps.[^29][^5]
- Face/model generation separate from product compositing: Generate model/environment first, then composite real product in post.
- Post‑generation product verification: Compare output to reference via silhouette and colour metrics.[^31][^25]
- AI quality scoring: Scoring outputs on fidelity and rejecting outliers.

### Recommended architecture (MVP and beyond)

#### MVP (balanced quality and complexity)

- Step 1: Segmentation
  - Use SAM to obtain precise jewellery mask from reference photo.[^24][^5]
- Step 2: Composite neutral base
  - Place masked jewellery onto a neutral, high‑resolution base (white or grey) aligned for target composition (e.g., centered ring).[^5][^3]
- Step 3: Reference‑conditioned generation (Higgsfield)
  - Use Higgsfield Soul or similar image model with reference image conditioning to generate surrounding environment while preserving product.[^10][^1]
  - Prompt emphasises "use this jewellery as fixed subject; do not modify its shape or details".
- Step 4: Output verification
  - Run silhouette comparison: edge detection on reference vs generated product region, compute similarity (e.g., edge F1).[^25][^29]
  - Run colour sanity check on metal and stones (approximate LAB or HSV comparison).[^31]
  - Flag images with high divergence for regeneration or user review.

Pros: Uses Higgsfield’s strong aesthetics and reference capability; latency acceptable (single generation per image); no need for separate diffusion stack initially.[^10][^1]

Cons: Limited structural constraints; still some risk of minor jewellery drift.

#### V1/V2 (higher fidelity with extra components)

- Add a secondary pipeline using open‑source diffusion tools (Stable Diffusion + ControlNet + inpainting) for high‑fidelity modes:
  - Step 1: SAM segmentation and mask.[^24][^5]
  - Step 2: Generate environment with SD + ControlNet using canny/depth maps from jewellery photo, but keep jewellery pixels locked through inpainting mode.[^27][^17][^24]
  - Step 3: Composite environment with original jewellery photo, optionally relight using depth maps and environment maps.[^29][^5]
  - Step 4: Run quality scoring and verification.

- Users can choose "Ultra Fidelity" template (higher credits, slower) for critical SKUs; default templates use Higgsfield only.

Trade‑offs:
- Quality: Multi‑stage, mask‑based and ControlNet pipelines offer highest structural fidelity and allow advanced colour‑fidelity frameworks.[^23][^31]
- Latency: Additional segmentation and diffusion passes add time.
- Cost: Compute cost increases; must be priced accordingly.
- Scalability: Two pipelines increase operational complexity but separate concerns (creative vs fidelity).

Recommendation: MVP use Higgsfield reference conditioning plus segmentation and verification; V1 adds optional SD+ControlNet inpainting pipeline for highest fidelity SKUs.

## 8. Prompt Engineering System

Goal: Generate structured prompts from configuration without user manual input.

### Structured inputs

- Jewellery type: ring, necklace, earrings, bracelet, bangles, pendant, full set.
- Model: presence, gender, age band, skin tone palette, hair style.
- Pose: neck, ear, hand, finger, wrist placement.
- Outfit: casual, formal, bridal.
- Environment: background type and variant.
- City: London, Paris, Dubai, Mumbai, etc.
- Lighting: soft studio, warm sunset, neutral daylight.
- Camera: shot type, distance (close‑up, mid shot), angle (eye‑level, slight above).
- Mood: romantic, minimal, festive, editorial.
- Composition: product emphasis, negative space, framing.
- Aspect ratio: 4:5, 1:1, 9:16.

### Prompt layers

- System prompts (internal, not sent to users):
  - Example: "You generate professional jewellery product photographs. You must preserve the exact jewellery appearance from the reference image, including stone count, metal colour, engravings and proportions. You may change background, model, lighting and composition according to the prompt, but never redesign or alter the jewellery itself."[^25][^5]

- Product prompts:
  - Describe the jewellery: "a 22k yellow gold engagement ring with a single round brilliant diamond and pavé band".[^4][^3]
  - Include metal finish (polished, matte) and stone properties.[^3]

- Scene prompts:
  - Combine environment, model, location, mood: "on a South Asian bridal model in a traditional wedding mandap in Mumbai, warm golden lighting, shallow depth of field".[^8]

- Negative prompts:
  - Explicit constraints: "do not change stone count, do not recolour metal, no extra gems, no text, no logos, no redesign of jewellery".[^26][^25]

- Jewellery preservation instructions:
  - "The jewellery piece in the reference image must remain identical: same shape, metal colour, stone count and placement, engravings and proportions. Treat it as a locked subject; only adjust the surroundings."[^31][^5]

- Quality‑control prompts (optional second pass):
  - For verification runs, can prompt models or scoring engine: "return true if jewellery silhouette and colors match the reference within threshold".

### Framework implementation

- Use server‑side prompt builder that:
  - Takes structured config (product, template, user choices).
  - Constructs a canonical product description from metadata.
  - Applies template prompt snippets for scene and mood.
  - Attaches negative and preservation instructions.
- Ensure consistent phrasing for jewellery identity across all prompts to minimize variance.[^26]

## 9. Photoshoot Templates

Templates encapsulate common creative directions optimized for jewellery.

### Example template types

- Luxury campaign.
- Bridal.
- Wedding.
- Festive (Diwali, Christmas).
- Minimal studio.
- Editorial fashion.
- Instagram campaign.
- Website hero image.
- Catalogue photography.
- E‑commerce product image (white background).[^3]
- Lifestyle campaign.
- Valentine’s Day.
- Diwali.
- Christmas.
- Mother’s Day.[^8]

### Template behaviour

Each template defines defaults for:
- Shot type (product vs on‑model vs lifestyle).
- Model presence and styling.
- Background type and variant.
- Location aesthetic.
- Lighting style.
- Mood and colour palette.
- Aspect ratio and resolution recommendations.
- Max number of images per shoot.

Users:
- Select a template; can override a limited subset of parameters (e.g., city, aspect ratio, number of images).
- See preview thumbnails or reference examples.

### Technical representation

- Database entity `photoshoot_templates` with fields:
  - `id`, `name`, `description`.
  - `category` (campaign, catalogue, e‑commerce, seasonal).
  - `default_config` (JSON blob of configuration parameters).
  - `is_active` flag.
  - `thumbnail_url`.
- When user selects template, backend copies `default_config` into photoshoot project record, allowing overrides.

## 10. Backend Architecture

### Components

- REST API service (Node.js or Python FastAPI).
- Authentication and authorization layer.
- Database (PostgreSQL).
- Object storage (S3‑compatible, e.g., AWS S3 or Backblaze B2).
- Queue system (e.g., Redis queues or AWS SQS).
- Worker service for generation jobs.
- Webhook endpoint for Higgsfield.
- Integration with payment provider (Stripe).
- Logging and monitoring stack.

### APIs

- Auth: login, signup, refresh token.
- Organizations: create, update, list.
- Products: CRUD.
- Jewellery assets: upload, list.
- Photoshoot projects: create, update, list, get.
- Templates: list, get.
- Generation jobs: create, list, status.
- Images: list, get download URL.
- Credits and billing: get balance, buy credits, plan changes.

### Authentication

- JWT tokens for frontend.
- Server‑side authentication to Higgsfield via key/secret stored in secure config.[^19][^15]

### Database

- PostgreSQL with normalized schema (see section 11).

### Object/file storage

- S3 buckets:
  - `product-source` (original uploads).
  - `product-masks` (segmentation outputs).
  - `photoshoot-outputs` (generated images).
  - `thumbnails`.

### Generation jobs and queue

- Jobs stored in DB table, processed by worker.
- Queue (Redis/SQS) to offload generation to worker process.

### Webhooks

- Endpoint `/webhooks/higgsfield` to receive job completion and error events.[^14]

### AI provider integration

- Higgsfield: primary creative generation provider.
- Optional SD+ControlNet pipeline hosted on our own infrastructure (V1/V2).

### Usage tracking

- Track per‑organization and per‑user generations, credits consumed, regeneration counts.

### Credits and billing

- Credit balance per organization.
- Deduct credits for generation jobs and regenerations.
- Map our internal credit pricing to Higgsfield’s credit economics and margins.[^16][^13]

### User/project management

- Organizations with multiple users; roles (owner, editor, viewer).

### Image metadata

- Store prompts, configuration, verification scores.

### Error logging

- Central logging pipeline with structured error events.

### Analytics

- Basic cohort and usage metrics stored in DB; exported to BI tools if needed.

### Tech stack recommendation (MVP)

- Backend: Python (FastAPI) or Node.js (NestJS/Express). FastAPI pairs well with Python‑native ML components and Higgsfield SDK.[^19]
- Frontend: React (Next.js) or Vue.
- DB: PostgreSQL.
- Queue: Redis or AWS SQS.
- Storage: AWS S3.
- Deployment: Docker containers on AWS ECS/Fargate or similar.

## 11. Database Schema (MVP)

### Entities and key fields

#### users
- `id` (PK).
- `email`, `password_hash`.
- `name`.
- `role` (admin, user).
- `created_at`, `updated_at`.

#### organizations
- `id` (PK).
- `name`.
- `country`.
- `default_currency`.
- `plan` (free, starter, plus, enterprise).
- `created_at`, `updated_at`.

#### user_organizations
- `user_id`, `organization_id` (composite PK).
- `role` (owner, member).

#### products
- `id` (PK).
- `organization_id` (FK).
- `name`.
- `sku`.
- `type` (ring, necklace, etc.).
- `metal_type`, `metal_colour`.
- `stone_types` (JSON array).
- `stone_count` (int optional).
- `collection`.
- `source_image_id` (FK to jewellery_assets).
- `created_at`, `updated_at`.

#### jewellery_assets
- `id` (PK).
- `organization_id` (FK).
- `product_id` (FK).
- `source_url`.
- `mask_url`.
- `processed_url` (composite base used for generation).
- `quality_score`.
- `metadata` (JSON).

#### models (human model presets)
- `id`.
- `name`.
- `gender`.
- `age_band`.
- `skin_tone`.
- `hair_style`.
- `pose_family`.
- `preset_config` (JSON).

#### photoshoot_projects
- `id`.
- `organization_id`.
- `product_id`.
- `template_id`.
- `name`.
- `status` (draft, generating, completed, failed).
- `config` (JSON snapshot).
- `created_at`, `updated_at`.

#### photoshoot_templates
- `id`.
- `name`.
- `category`.
- `description`.
- `default_config` (JSON).
- `thumbnail_url`.
- `is_active`.

#### generation_jobs
- `id`.
- `photoshoot_project_id`.
- `organization_id`.
- `product_id`.
- `status` (queued, running, completed, failed).
- `higgsfield_request_id`.
- `higgsfield_status_url`.
- `provider` (higgsfield, internal_sd).
- `config` (JSON).
- `error_code`, `error_message`.
- `created_at`, `updated_at`.

#### generated_images
- `id`.
- `generation_job_id`.
- `photoshoot_project_id`.
- `product_id`.
- `image_url`.
- `thumbnail_url`.
- `prompt_id`.
- `verification_score` (structural, colour metrics).
- `is_favourite`.
- `created_at`.

#### prompts
- `id`.
- `photoshoot_project_id`.
- `generation_job_id`.
- `system_prompt`.
- `product_prompt`.
- `scene_prompt`.
- `negative_prompt`.
- `metadata`.

#### credits
- `id`.
- `organization_id`.
- `balance`.
- `last_updated`.

#### transactions
- `id`.
- `organization_id`.
- `type` (purchase, generation, refund).
- `amount` (credits).
- `monetary_amount`.
- `description`.
- `created_at`.

#### subscriptions
- `id`.
- `organization_id`.
- `plan`.
- `status`.
- `renewal_date`.
- `created_at`, `updated_at`.

## 12. API Specification (Key Endpoints)

### Auth

**POST /api/auth/signup**
- Purpose: Create user and organization.
- Request: `{ email, password, name, organization_name }`.
- Response: `{ user, organization, token }`.
- Auth: None.
- Errors: email in use, weak password.

**POST /api/auth/login**
- Purpose: Authenticate user.
- Request: `{ email, password }`.
- Response: `{ token, user }`.
- Auth: None.
- Errors: invalid credentials.

### Products

**POST /api/products**
- Purpose: Create product metadata.
- Request: `{ name, sku, type, metal_type, metal_colour, stone_types, stone_count, collection, source_asset_id }`.
- Response: `{ product }`.
- Auth: JWT.
- Errors: validation, org mismatch.

**GET /api/products**
- Purpose: List products.
- Request: query params (search, filter).
- Response: `{ products: [...] }`.
- Auth: JWT.
- Errors: auth.

**GET /api/products/{id}**
- Purpose: Get product details.
- Response: `{ product, assets }`.

### Jewellery assets

**POST /api/assets/upload-url**
- Purpose: Get signed URL for product image upload.
- Request: `{ file_name, mime_type }`.
- Response: `{ upload_url, asset_id }`.

**POST /api/assets/{id}/finalize**
- Purpose: Finalize asset after upload, trigger segmentation.
- Request: none.
- Response: `{ asset }`.

### Photoshoot projects

**POST /api/photoshoots**
- Purpose: Create photoshoot project.
- Request: `{ product_id, template_id, config }`.
- Response: `{ photoshoot }`.

**GET /api/photoshoots**
- Purpose: List projects.
- Response: `{ projects: [...] }`.

**GET /api/photoshoots/{id}**
- Purpose: Get photoshoot details.
- Response: `{ project, jobs, images }`.

### Generation

**POST /api/generation-jobs**
- Purpose: Trigger generation jobs for a photoshoot.
- Request: `{ photoshoot_id }` (backend uses project config and template to create jobs).
- Response: `{ jobs: [...] }`.

**GET /api/generation-jobs/{id}**
- Purpose: Get job status.
- Response: `{ job }`.

**POST /api/generation-jobs/{id}/retry**
- Purpose: Retry failed job.
- Response: `{ job }`.

### Images

**GET /api/images/{id}**
- Purpose: Get image metadata.
- Response: `{ image }`.

**GET /api/images/{id}/download**
- Purpose: Generate download URL.
- Response: `{ url }`.

### Templates

**GET /api/templates**
- Purpose: List templates.
- Response: `{ templates: [...] }`.

### Credits and Billing

**GET /api/credits**
- Purpose: Get credit balance.
- Response: `{ balance }`.

**POST /api/credits/purchase**
- Purpose: Buy credits (integrate Stripe).
- Request: `{ package_id }`.
- Response: `{ transaction, new_balance }`.

### Webhooks

**POST /api/webhooks/higgsfield**
- Purpose: Receive completion events.
- Request: per Higgsfield spec (job id, status, output URLs).[^14][^15]
- Response: 200 OK.

## 13. Admin Dashboard

Admin system should provide:

Features:
- View customers (organizations) with plan, credits, usage stats.
- View generated images (with ability to inspect prompts and verification scores).
- Monitor generation jobs (status, failure reasons).
- Monitor API usage and rate‑limit events.
- Track Higgsfield costs vs internal pricing (per‑credit economics).[^13][^16]
- Manage templates (create/update/deactivate).
- Manage model presets (backgrounds, human model presets).
- Manage credits (manual adjustments for support).
- Review failed generations for debugging.
- Monitor system health (queues, worker status, error rates).

Implementation:
- Internal admin SPA secured by role.
- Backend admin APIs: 
  - `/admin/organizations`, `/admin/jobs`, `/admin/images`, `/admin/templates`, `/admin/metrics`.

## 14. Pricing and Credits

Higgsfield uses a credit‑based model; each generation consumes credits depending on model and resolution.[^22][^13][^12]

### Current Higgsfield pricing (as of mid‑2026)

- Consumer tiers: Free, Starter ($15/month, 200 credits), Plus ($49/month or $39 annual, 1,000 credits), Ultra ($129/month or $99 annual, 3,000+ credits), Business (~$89/seat, 1,500 credits per seat).[^16][^13]
- Credits spent across models; simple Nano Banana image generations reported around 2 credits each; video and premium generations much higher.[^32][^13]

Assumption: Our SaaS will use a Business/Enterprise plan or dedicated workspace; internal per‑image credit consumption will be measured during beta.

### Proposed pricing architecture

- Internal "generation unit" mapped to underlying Higgsfield credits plus additional compute.
- Per photoshoot: 4–8 images; each image consumes X Higgsfield credits + segmentation/verification compute.

Recommended user pricing (example):

- Free trial:
  - 1 product, up to 2 photoshoots, capped resolution (1080p), watermark; no credit card.

- Starter (SMB):
  - $49/month, includes 150 images/month (roughly 20–30 photoshoots at 5–6 images each).

- Growth:
  - $129/month, includes 500 images/month.

- Scale:
  - $299/month, includes 1,500 images/month.

- Enterprise:
  - Custom pricing, SLA, dedicated pipelines, per‑image or per‑SKU pricing.

Credits model:
- 1 image generation = 1 internal credit.
- Regeneration of existing image = 0.5 credit.
- Ultra fidelity pipeline (ControlNet/inpainting): 2–3 credits per image.
- Higher resolution (4K) ×1.5 multiplier.

Gross margin targets:
- Aim for 60–70% margin after Higgsfield and infra costs; adjust per‑plan credit allowances based on measured average credit usage.[^13]

## 15. MVP Definition

### MVP scope

Include:
- User signup/login.
- Single‑organization per user.
- Product upload with basic segmentation and quality checks.
- Product metadata management.
- Photoshoot templates (minimal list: E‑commerce white background, Minimal studio, Bridal lifestyle, Instagram campaign).[^2][^3]
- Photoshoot configuration with:
  - Number of images.
  - Aspect ratio.
  - Resolution tier (up to 2K).
  - Shot type (product‑only vs on‑model).
  - Basic model presets (gender, skin tone cluster).
  - Background type and simple location aesthetic.
- Integration with Higgsfield API for image generation (Soul or Nano Banana model with reference image).[^1][^10][^15]
- Asynchronous generation with job status.
- Gallery view and image download.
- Simple regeneration of individual images.
- Basic credits system (balance, deduction per image) without Stripe integration (manual top‑ups for MVP).
- Admin dashboard with customers, jobs, basic metrics.

Exclude from MVP:
- Multi‑tenant organizations with complex roles (beyond owner + default user).
- Advanced pricing tiers and full billing integration (Stripe can be added in V1).
- SD+ControlNet pipeline (added in V1 or V2 for ultra fidelity).
- Soul ID character training integration.
- Email notifications.
- Detailed analytics dashboards.

### V1 additions

- Stripe or similar billing integration.
- Multiple users per organization with roles.
- More templates and fine‑grained configuration.
- Simple analytics and reporting.
- Optional "High fidelity" mode using internal diffusion pipeline.

### V2 and future

- Full ultra fidelity pipeline with ControlNet and colour‑locked workflows.[^23][^31]
- Soul ID integration for consistent human models.[^21][^20]
- API access for agencies and marketplaces.
- Virtual try‑on features (video or interactive experiences).[^33][^34][^35]
- Advanced analytics and A/B testing integrations.

## 16. Non‑Functional Requirements

Performance:
- Generation job submission and UI interactions <300ms backend response.
- Gallery rendering optimized with lazy loading.

Scalability:
- Horizontal scaling of worker service and API.
- Queue‑based architecture supports thousands of jobs/day.

Security:
- JWT‑based auth.
- Encrypted storage for credentials.
- HTTPS everywhere.

Privacy:
- Product images treated as confidential; no sharing outside organization.
- Clear retention policy (user can request deletion).

Reliability:
- Job retry on transient errors.
- Transparent status and error reporting.

Image quality:
- Minimum resolution enforced for uploads.
- Verification on structural and colour fidelity.

Generation latency:
- Target average <30–60 seconds per photoshoot (4–6 images) depending on model load.[^22][^12]

Cost controls:
- Hard cap on daily/weekly generations per organization if needed.
- Real‑time credit checks.

Rate limiting:
- Per‑user and per‑organization API rate limits to avoid abuse.[^19]

Storage:
- Lifecycle policies for older outputs to manage costs.

Observability:
- Metrics: job counts, latency, error rates.
- Central logging with correlation IDs.

## 17. Analytics

Key metrics:
- Photoshoots created per day/week/month.
- Images generated.
- Generation success rate (completed vs failed).[^5]
- Regeneration rate (per image, per photoshoot).
- Cost per generated image (credits + infra) per plan.[^13]
- Images downloaded.
- Active customers (organizations with activity in period).
- Products uploaded.
- Photoshoots per customer.
- Conversion from upload → first generation.
- Conversion from generation → download.
- Customer retention (cohort analysis).
- Revenue per customer.

Implementation:
- Event tracking for key operations (upload, create photoshoot, start generation, completion, download).

## 18. Error Handling

Define UX and backend behaviour for key errors:

- Invalid image:
  - Backend detects unsupported format or corrupt file; return error.
  - UI shows clear message and checklist.

- Poor‑quality product photo:
  - Backend scoring flags low resolution or noisy background; suggest reshoot.[^2][^3]

- Jewellery not detected:
  - SAM fails to find object or mask too small; UI asks user to crop or upload clearer photo.[^5]

- AI generation failure:
  - Higgsfield returns failed status; job marked failed; user can retry.

- API timeout:
  - Worker logs timeout; automatic retry with backoff.

- Rate limit:
  - Higgsfield or internal limits; respond with informative error and recommended wait.[^19]

- Insufficient credits:
  - Backend prevents job submission and prompts user to upgrade or top‑up.

- Storage failure:
  - If S3 upload/download fails, log and show generic error; auto retry with fallback.

- Image generation that materially changes jewellery:
  - Verification detects silhouette/colour mismatch; mark image "fidelity risk" and hide by default, allow regeneration.

- Content policy violations:
  - If model generates inappropriate content (rare for jewellery but possible with human models), mark unsafe; user sees error; job flagged.

## 19. Security and Abuse Prevention

Considerations:

- Authentication:
  - Secure JWT, password policies, optional SSO for larger customers.

- Authorization:
  - Org‑scoped resources; user roles control access.

- Organization‑level isolation:
  - Data separated by org_id at DB and storage layers.

- Signed URLs:
  - Time‑limited URLs for downloads.

- Image access controls:
  - Only authorized users can view organization assets.

- API key security:
  - Higgsfield keys stored in secure config (KMS/Secrets Manager).

- Prompt injection:
  - Frontend does not allow arbitrary prompt input in MVP; later, sanitize if manual prompts allowed.

- Abuse prevention:
  - Rate limits, monitoring unusual activity.

- Data retention:
  - Clear policies for product and output storage; deletion tools.

## 20. Claude Code Implementation Plan (Build Brief)

### Recommended technology stack

- Backend: Python FastAPI + Higgsfield Python SDK for API interactions.[^19]
- Frontend: React (Next.js) SPA.
- Database: PostgreSQL.
- Storage: AWS S3.
- Queue: Redis (RQ or Celery) or AWS SQS.
- Workers: Python workers running segmentation, prompt building, generation, and verification.

### Repository structure

- `backend/`
  - `app/`
    - `main.py` (FastAPI entry).
    - `routers/` (auth, products, assets, photoshoots, jobs, admin).
    - `models/` (SQLAlchemy models).
    - `schemas/` (Pydantic schemas).
    - `services/` (higgsfield_service, segmentation_service, prompt_service, verification_service, credits_service).
    - `workers/` (job_worker).
  - `tests/`.

- `frontend/`
  - `pages/` (Next.js pages: dashboard, products, photoshoots, templates, settings).
  - `components/`.
  - `lib/` (API client).
  - `styles/`.

- `infra/`
  - Dockerfiles.
  - Deployment manifests.

### Architecture diagram (described)

- Frontend (React) → Backend API (FastAPI) → PostgreSQL (data) + S3 (images) + Redis/SQS (queue).
- Worker service consumes jobs from queue, calls:
  - Segmentation (SAM).
  - Prompt builder.
  - Higgsfield API.
  - Verification.
- Higgsfield API returns results via polling/webhook; worker stores outputs in S3 and updates DB.

### Database setup

- Use Alembic migrations.
- Create schema entities described in section 11.

### Environment variables

- `HF_API_KEY_ID`, `HF_API_KEY_SECRET` for Higgsfield auth.[^15]
- DB connection string.
- S3 credentials and bucket names.
- Queue connection details.
- JWT secret.

### Third‑party integrations

- Higgsfield API (image generation).[^14][^15]
- Stripe (V1) for billing.

### Backend implementation sequence (phased)

1. Auth and organizations.
2. Products and assets upload + basic storage.
3. Photoshoot templates and configuration persistence.
4. Prompt builder service.
5. Higgsfield integration service (submit and poll jobs).
6. Generation jobs and worker infrastructure.
7. Gallery and image metadata endpoints.
8. Credits system (deduct on generation).
9. Admin APIs.

### Frontend implementation sequence

1. Auth pages and layout shell.
2. Dashboard and navigation.
3. Product library and upload flow.
4. Product setup forms.
5. Templates selector and photoshoot configuration screen.
6. Generation progress UI.
7. Gallery view and download interactions.
8. Regeneration UI.
9. Credits and basic settings screens.

### Higgsfield integration sequence

1. Implement server‑side authentication and simple image generation from prompt only using a basic model (e.g., Nano Banana).[^12][^1]
2. Add reference image parameter: upload product composite and pass as input.[^10]
3. Implement asynchronous polling or webhook handling.[^15][^14]
4. Integrate with photoshoot configuration and prompt builder.
5. Add per‑model configuration (Soul, others) as needed.

### Testing strategy

- Unit tests for services (prompt builder, segmentation, verification).
- Integration tests for Higgsfield calls (mocked in CI).
- End‑to‑end tests for core flows (upload → configure → generate → download).

### Local development setup

- Docker‑compose including:
  - backend.
  - frontend.
  - Postgres.
  - Redis.
  - MinIO (S3‑compatible local storage).

- Use test Higgsfield workspace and keys.

### Production deployment

- Containerize backend and workers; deploy to ECS/Fargate or Kubernetes.
- Frontend deployed via Vercel or CloudFront.
- Managed Postgres (RDS) and S3.

### Monitoring

- Use basic APM (e.g., OpenTelemetry + a provider) for backend.
- CloudWatch or similar for infra metrics.

### Seed data for models/backgrounds/templates

- Initial templates seeded via DB migration or script:
  - E‑commerce white background.
  - Minimal studio.
  - Bridal lifestyle.
  - Instagram campaign.

- Seed model presets and backgrounds for quick use.

### Definition of done

MVP is done when:
- A user can sign up, upload a jewellery product photo, configure a basic photoshoot using templates, generate multiple images via Higgsfield with jewellery preserved visually, review outputs in a gallery, regenerate individual images, and download images — all within a credit‑tracked, multi‑tenant SaaS.

## Claude Code Build Brief (Concise)

Context:
- Build a vertical SaaS for AI jewellery photoshoots.
- Use Higgsfield’s image API for creative generation, anchored by product reference images and segmentation.
- Jewellery fidelity is critical; environment is flexible.

Architecture:
- React frontend + FastAPI backend.
- Postgres for data; S3 for storage; Redis/SQS for job queue.
- Worker service runs segmentation (SAM), prompt building, Higgsfield calls, and verification.

Priorities:
1. End‑to‑end MVP flow: upload → product setup → template‑based photoshoot config → async generation → gallery → download.
2. Simple, non‑prompt UI with templates and safe defaults.
3. Higgsfield integration with reference image conditioning.
4. Basic segmentation and simple verification of outputs.
5. Credit accounting per image.

Implementation Phases:
- Phase 1: Core platform skeleton
  - Implement auth, organizations, product entities and asset upload.
  - Build frontend dashboard, product library and upload UI.

- Phase 2: Templates and configuration
  - Implement photoshoot templates and configuration persistence.
  - Build configuration UI (number of images, aspect ratio, shot type, basic model/background/location settings).

- Phase 3: Higgsfield integration
  - Implement Higgsfield service for simple text‑prompt image generation.
  - Add reference image parameter using uploaded product composite.
  - Implement asynchronous job handling (polling or webhook).

- Phase 4: Gallery and regeneration
  - Implement gallery view and image download endpoints.
  - Build regeneration flow for individual images tied to same product.

- Phase 5: Segmentation and verification
  - Integrate SAM or equivalent for jewellery mask creation.
  - Implement simple verification checks (silhouette and colour comparison) and flagging.

- Phase 6: Credits and admin
  - Implement credits accounting (balance tracking, per‑image deduction).
  - Build admin dashboard to monitor organizations, jobs, images and basic metrics.

Start with minimal template set and a single Higgsfield model, then iterate on fidelity and configuration depth once the basic system is working.

---

## References

1. [AI Image Generator: Text to Image AI](https://higgsfield.ai/ai-image) - Create studio-quality AI images with Nano Banana Pro, Higgsfield Soul, Seedream, FLUX, and more. Nat...

2. [AI Jewellery Product Photography: Complete Guide (2026)](https://www.oralab.ai/blog/ai-product-photography-for-jewellery-brands) - How AI product photography works for jewellery brands, what it costs vs a studio shoot, how to keep ...

3. [AI Jewelry Product Photography: Pro Shots Without a Studio](https://www.slashlink.io/blog/ai-jewelry-product-photography) - Jewelry is one of the hardest products to photograph. Learn how to go from a decent phone photo to m...

4. [AI product photography for jewelry](https://uselamina.ai/blog/ai-product-photography-for-jewelry-brands-what-actually-works) - Plain, specific guide to AI product photography for jewelry brands: what works, what fails, and how ...

5. [How AI Product Photography Works: The Real Tech Stack](https://www.absolutelyai.com.au/news/how-ai-product-photography-works) - Segmentation. The model finds the exact pixel boundary of the product and separates it from its orig...

6. [GlamShot — AI Product Photography](https://glamshot.ai/) - GlamShot — AI-powered product photography for jewellery brands.

7. [Jewel AI Studio | AI Jewellery Photoshoot](https://www.jewelaistudio.com/) - Create premium AI jewellery photoshoots from raw product photos. Generate catalogue, studio, model-w...

8. [Sparkle at Scale: How AI Jewellery Product Photography Reimagines Visual Selling - Kruise Fest](https://kruisefest.com/sparkle-at-scale-how-ai-jewellery-product-photography-reimagines-visual-selling/) - Rings, earrings, watches, and fine accessories live or die by how they catch the eye online. Every p...

9. [AI product photography for Jewelry](https://www.photoroom.com/industry/jewelry) - Edit complex photos, add realistic shadows, and retain product fidelity with Photoroom's image editi...

10. [Higgsfield Soul 2.0 - High Aesthetic AI Photo Generation ...](https://higgsfield.ai/soul-intro) - Soul 2.0 is Higgsfield's foundation photo model built for creative, fashion-aware, culture-native ge...

11. [AI Product Photography for Ecommerce - Krea](https://www.krea.ai/ecommerce) - Create AI-generated images and art from text with Krea. Use Flux, Imagen, Nano Banana, and ChatGPT I...

12. [higgsfield-ai/cli](https://github.com/higgsfield-ai/cli) - Higgsfield CLI. Contribute to higgsfield-ai/cli development by creating an account on GitHub.

13. [Higgsfield Pricing | UsagePricing](https://www.usagepricing.com/blueprint/higgsfield) - Higgsfield pricing 2026: credit-based AI video subscription — Free, Starter $15, Plus $49, Ultra $12...

14. [How to use the API - Higgsfield API Docs](https://docs.higgsfield.ai/docs/how-to/introduction)

15. [docs](https://docs.higgsfield.ai/docs)

16. [Higgsfield AI Pricing 2026: $15, $49 and $129 Plans — Creetr](https://creetr.com/blog/higgsfield-pricing) - Higgsfield AI costs $15/mo (Starter, 200 credits), $49/mo (Plus, 1,000) or $129/mo (Ultra, 3,000), p...

17. [Developing an AI solution for product photography](https://www.ml6.eu/en/blog/developing-an-ai-solution-for-product-photography-what-we-learned) - Discover how AI can revolutionize product photography by generating professional images from amateur...

18. [What is ControlNet and how does it help AI product photography ...](https://nightjar.so/help-desk/what-is-controlnet-and-how-does-it-help-with-ai-product-photography-consistency) - ControlNet is a Stable Diffusion add-on that lets a generative model copy the structure of a referen...

19. [docs.higgsfield.ai](https://docs.higgsfield.ai/docs/llms.txt)

20. [How do I create and use a Soul ID character?](https://higgsfield.ai/creator-hub/help-center/ai-models/how-do-i-create-and-use-a-soul-id-character) - Learn how to train a Soul ID character on Higgsfield, which photos work best, and how to use it acro...

21. [Soul ID — Character Training | higgsfield-ai/cli | DeepWiki](https://deepwiki.com/higgsfield-ai/cli/2.4-soul-id-character-training) - The `higgsfield soul-id` command group enables users to create and manage "Soul IDs"—custom AI chara...

22. [Higgsfield CLI | AI Image & Video Generation for Any Agent](https://higgsfield.ai/cli) - Run Higgsfield from the command line in Claude Code, Cursor, or any MCP-compatible CLI agent. Genera...

23. [Master Precision AI Art in 2025 [72% Accuracy Boost]](https://www.cursor-ide.com/blog/image-to-image-controlnet-guide) - According to July 2025 benchmarks, ControlNet revolutionizes image-to-image generation with 94.2% st...

24. [[PDF] 12 XI November 2024 https://doi.org/10.22214/ijraset.2024.65289](https://www.ijraset.com/best-journal/aigraphy-image-to-advertisement)

25. [AI Image Product Shape Distorted in Hero Shots | AI Tools Guidebook](https://aitoolsguidebook.com/en/articles/ai-image-product-shape-distorted/) - AI hero shots warp the bottle, bend the box, melt the logo edge. Here is the working fix: ControlNet...

26. [Object consistency: keep objects stable across AI shots | Morphic](https://morphic.com/ai-glossary/Object-Consistency) - Object Consistency ensures specific objects maintain stable appearance across AI-generated frames an...

27. [AIGraphy: Image to Advetisement - IJRASET](https://www.ijraset.com/research-paper/aigraphy-image-to-advetisement) - This study investigates the use of generative artificial intelligence (AI) models to improve and aut...

28. [E-Commerce Inpainting with Mask Guidance in Controlnet ...](https://arxiv.org/pdf/2409.09681.pdf)

29. [ozgursntrk/product-bg-replacement-pipeline: Background ... - Fastly](https://ithub.global.ssl.fastly.net/ozgursntrk/product-bg-replacement-pipeline) - Background replacement for product photos: rembg segmentation + SD 1.5 ControlNet inpainting + IC-Li...

30. [GitHub - PSRahul/product_photography_with_lora_sd: An experiment to generate highly accurate product photography using Low Rank Adapation (LORA) on Stable Diffusion](https://github.com/PSRahul/product_photography_with_lora_sd) - An experiment to generate highly accurate product photography using Low Rank Adapation (LORA) on Sta...

31. [How To Batch-edit Product Photos Using Stable Diffusion Without ...](https://www.alibaba.com/product-insights/how-to-batch-edit-product-photos-using-stable-diffusion-without-losing-brand-color-fidelity.html) - A practical, step-by-step guide to batch-editing e-commerce product photos with Stable Diffusion whi...

32. [Higgsfield AI Pricing: Every Plan and Credit Cost | The AI ...](https://aijourn.com/higgsfield-ai-pricing-every-plan-credit-cost-30-discount/) - Most people don't want a feature-by-feature breakdown of AI video and image tools. They want one ans...

33. [NeuroViz - AI Jewelry Photography](https://neuroviz.ai/) - Transform your jewelry photos with AI-powered retouching, background removal, and virtual try-on. No...

34. [SoraiPixel — AI Jewelry Photography](https://soraipixel.com/) - Transform raw jewelry photos into studio-quality images in seconds. AI-powered product photography f...

35. [Gemzy | AI Jewelry Photography Studio for Designers](https://www.gemzy.co/) - Gemzy is the AI studio for jewelry designers and brands. Upload your jewelry and generate campaign-r...

