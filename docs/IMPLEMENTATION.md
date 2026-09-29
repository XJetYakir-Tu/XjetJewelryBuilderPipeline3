# Pipeline 3 — implementation record

Date: 2026-09-29. Planning baseline: [PIPELINE_3_SPEC.md](PIPELINE_3_SPEC.md) (the handoff) and [PIPELINE_2_FLOW.md](PIPELINE_2_FLOW.md) (P2 reference trace).

This document separates three things:

- **Confirmed** — agreed product requirements (spec §3).
- **Recommendation** — implementation defaults chosen here. These are *not* product decisions and can be changed.
- **Open** — decisions or verifications still needed.

## 1. Isolation from Pipeline 2

- This is an independent Git repository with its own remote. Nothing here imports, links to, or reads Pipeline 2 at runtime.
- Pipeline 2 (`C:\Users\yakir.tubul\Git\XjetJewelryBuilder`) was read only, at revision `1e871734bb9ac0f824bdb8950d62fbf6a35083bb`. Nothing was edited or installed there, and its backend was not run. Its two pre-existing untracked items (`PIPELINE_2_FLOW.md`, `RingRescaler/`) were fingerprinted before work began and re-verified unchanged afterwards (§9).
- One read-only step touched P2 files: P2's browser calculator (`xjet-calc.js` and its data files) was evaluated under Node from a scratch directory to produce parity reference values. No files were written into P2.
- The following were copied into this repo as independent files, after review:
  - the two Nano Banana system prompts and the P2 prompt suffix → `config/prompts/`
  - a subset of the CPP constants, overrides, and metal map → `p3/pricing/cpp_db.json`
  - material colour tints/swatches and the ring-size list → `config/materials.json`
  - the fal error classification idea and the CPP formula → re-implemented
- Not copied: secrets, key files, databases, generated assets, logs, job JSON, customer data, deployment/nginx scripts, VisualHull, or the classifier.
- Runtime data lives only under `P3_DATA_DIR` (default `./var`, gitignored). The dev server runs on port **8310**.

## 2. Confirmed requirements → where they live

| Requirement | Implementation |
|---|---|
| One prompt → six different images | `p3/images.py`: each batch has 6 candidate rows (slots 0–5) sharing one `effective_prompt`, each with a distinct seed |
| Select one; clear rectangular border | `web/index.html` `.card.selected` (3 px solid outline + "Selected" tag). Selection is persisted server-side (`PUT /api/designs/{id}/selection`) |
| Zoom | Separate zoom button per card → lightbox with click-to-magnify. Zoom never toggles selection |
| Refine / Proceed / Start new | Action bar. Refine and Proceed are disabled until a ready image is selected |
| Refine → six new variations of the selected image | `CreateRefinement`: the selected image is uploaded once and used as `image_urls[0]` for all six edit requests, with one identical instruction |
| Customize image = selected image | `POST /api/designs/{id}/customize` returns the selected candidate's `image_url`. The UI shows it before the server responds |
| Customize video via `minimax/h3-max/camera-controls` | `p3/movies.py`, endpoint constant in `p3/providers/endpoints.py` |
| Default material Silver | `config/materials.json` (`default_material_id` is validated as `silver`) |
| Fashion group: Stainless Steel, Silver, Vermeil | `config/materials.json` |
| Luxury group: gold options | The six golds P2 offered in Customize (10K/14K/18K, yellow and rose). **No other luxury materials were invented.** |
| Fashion pricing at fixed 1 cm³ | `p3/pricing/service.py`. Numbers are **Open**; see [PRICING.md](PRICING.md) |
| Luxury "Price unavailable"; Add to Bag disabled | Enforced by the quote service and `CustomizeService.AddToBag` (HTTP 409), and shown in the UI |
| Standard ring size; size never changes price | US 4–12 in half sizes. Size is not a pricing input (see tests) |
| No Visual Hull, no automatic measurement | Neither exists in this codebase |
| Developer-only 3D/STL via `hitem3d/hi3d/v3.0/image-to-3d` | `p3/meshes.py` behind `P3_ADMIN_KEY` (`/api/dev/*`, `/dev` page) |

## 3. Implementation recommendations adopted (not product decisions)

- **Six requests per batch, one image each.** The provider caps `num_images` at 4 (see §4), so a batch is six single-image requests, each with its own seed. This gives per-slot identity, retry, and status, and a failed slot never regenerates successful ones.
- **Concurrency.** Up to 6 image requests run at once server-wide (`images.max_concurrent_requests`), so one batch runs fully in parallel.
- **Duplicate outputs.** An exact duplicate within a batch (by SHA-256) is re-requested once with a new seed (`max_duplicate_retries_per_slot: 1`). A second duplicate marks that slot `failed/duplicate_output`, and the user can retry it.
- **Refinement presentation.** The current grid stays visible with a "Creating six refinements… n of 6 ready" line. When the new batch finishes, the view switches to it and the selection is cleared so the user chooses again. Earlier batches stay as tabs ("Original", "Refinement 1", …).
- **Partial batches.** Ready images can be selected while other slots finish or fail. The UI states "n of 6 designs are ready" and offers "Retry missing designs". A batch is only labelled `complete` at 6/6.
- **Movie does not gate price or bag.** The movie is a preview. A failed movie leaves the image, material, size, price, and Add to Bag usable, with a "Retry movie" action.
- **Material preview** reuses P2's CSS tint filters on the same image and movie, labelled "Metal colour is a visual preview of the same design". No paid regeneration happens on material change.
- **Selecting a group** reveals its options and selects that group's last-used option (Fashion → Silver by default; Luxury → 10K Yellow Gold first). The price is cleared immediately on switching, so a Fashion price is never shown for Luxury.
- **"Start new"** clears the active design, selection, and customization in the UI. Saved designs and the bag are kept.
- **Size must be chosen explicitly** before Add to Bag (no default size).
- **Quantity** is 1–10. Different sizes of the same design are separate bag lines at the same unit price.
- **Access.** Customer requests require an access token (`X-Access-Token`), issued by an operator via `python -m p3.cli create-token`. Unlike P2, anonymous generation is not allowed. Email self-registration and mail were not carried over.
- **Usage.** Each provider submission is recorded in `usage_events` (image = 1 per slot, movie = 1). **No quota is enforced** — see Open items.
- **Asset URLs** (`/assets/...`) are unauthenticated capability URLs built from random 128-bit IDs. Developer meshes are stored outside the public asset root and are served only through the authenticated download route.
- **Prompt validation** is length-only (3–2000 characters). P2's LLM prompt gate and ONNX ring classifier were **not** carried over (see Open items).
- **Reference upload** (optional) requires a rights-confirmation checkbox, as in P2. It is limited to PNG/JPEG/WebP up to 10 MB and re-encoded to PNG. With a reference, the initial batch uses the edit endpoint.

## 4. Provider verification (fal.ai OpenAPI, fetched 2026-09-29)

| Endpoint | Verified contract | How P3 uses it |
|---|---|---|
| `fal-ai/nano-banana-pro` | `prompt` (≥3 chars), **`num_images` 1–4**, `seed`, `aspect_ratio`, `resolution` 1K/2K/4K, `output_format`, `system_prompt`, `limit_generations`. Output `images[].url` | 6 requests × `num_images=1`, distinct `seed`, 1:1, 1K, png, P2's system prompt |
| `fal-ai/nano-banana-pro/edit` | As above plus required `image_urls[]` | Refinement, and initial generation with a reference |
| `minimax/h3-max/camera-controls` | **Exists.** `image_url` (first frame), `prompt_expansion_mode` (`disabled`/`balanced`/`quality`), `duration` 3–15 s (int), `resolution` `480P`/`768P`/`1080P`, `camera_trajectory` = 2–12 keyframes `{time 0–1, azimuth°, elevation −90..90°, distance>0}`, `seed`. Output `video.url` | `duration 6`, `768P`, expansion `disabled`, a rigid-scene orbit prompt, 5 keyframes 0→360° azimuth at 10° elevation, distance 1.0 |
| `hitem3d/hi3d/v3.0/image-to-3d` | `image_url` (PNG/JPEG/WebP ≤ 20 MB), `resolution` `2048quality`/`2048master`, `face_count` 100k–5M, **`export_format` glb/obj/stl/fbx/usdz**, `enable_texture`, `enable_pbr`, `shading`. Output `model_mesh.url` | `export_format: "stl"` by default (native STL, no conversion). GLB/OBJ can be converted with trimesh as a separate step |

**Discrepancies and cautions:**

- The spec's warning was correct: `num_images=6` is not accepted (maximum 4).
- The Minimax `camera_trajectory` values are a **recommendation, not validated live**. In particular, the meaning of `distance: 1.0` ("normalized scene units") and how well a 360° orbit preserves a ring on white are unverified.
- The fal OpenAPI lists `prompt_expansion_mode` as required, but it has a default. P3 always sends it.
- None of Veo's parameters (`aspect_ratio`, `negative_prompt`, `generate_audio`, 8 s/720° prompt) are sent to Minimax. A test asserts this.

**Published costs** (fal model pages, 2026-09-29; fal notes pricing may change):

- Nano Banana Pro: $0.15 per image at 1K/2K, so **about $0.90 per six-image batch** and $0.90 per refinement (P2 used 1 image per generation).
- Hi3D v3.0: about $2.10 at the quality tier, billed per credit (texture +10 credits, PBR +5; both are disabled here).
- **Minimax H3 Max camera-controls: no price shown — Open.**

## 5. Architecture

```
web/index.html + app.js      customer UI (Alpine.js), server-authoritative, stale-response guards
web/dev.html                 developer mesh tool (bearer P3_ADMIN_KEY)
p3/app.py                    FastAPI routes; startup reconciliation
p3/images.py                 six-slot batches, refinement lineage, dedupe, slot retry
p3/movies.py                 selected-image Minimax job, dedupe by (candidate, config version)
p3/customize.py              selection → customization → quote → bag (server-enforced rules)
p3/meshes.py                 developer Hitem3D single-image mesh, STL
p3/pricing/                  quote service + CPP port + CPP data snapshot
p3/providers/                fal adapter, mock provider, endpoint ids
p3/runner.py                 task registry + one bounded polling contract
p3/db.py                     SQLite schema (own file under var/)
p3/assets.py                 containment-checked paths, atomic writes, content validation
config/                      generation params, prompts, catalog, pricing profiles
```

**Persisted objects** (`p3/db.py`): `access_tokens`, `designs` (selected_candidate_id), `batches` (kind initial/refine, parent_candidate_id, user_text, effective_prompt, endpoint, reference, config_version), `candidates` (slot, seed, status, provider_request_id, asset, sha256, error), `customizations` (one per design+candidate: material, size, quantity), `movies` (candidate, config_version, provider_request_id, status), `meshes` (candidate, settings, provenance, original/STL paths), `bag_lines` (quote snapshot), `usage_events`.

**Statuses:**

- candidate: `pending / generating / ready / failed`
- batch (derived): `queued / generating / complete / partial / failed`
- movie and mesh: `queued / running / ready / failed / interrupted`

**Job contract** (`p3/runner.py`):

1. Submit to the fal queue and persist `provider_request_id` immediately.
2. Poll status at `P3_POLL_INTERVAL_S` until complete, within a per-kind deadline (images 300 s, movie 900 s, mesh 1800 s).
3. Transient read errors retry the *same* request up to `P3_MAX_TRANSIENT_POLL_ERRORS` consecutive times.
4. Fetch the result, download it, validate the content (image decode / MP4 `ftyp` / STL or GLB structure), then write atomically (temp file + rename).
5. Only then mark the job `ready`.

**Restart reconciliation** (on startup):

- A job with a persisted `provider_request_id` resumes polling. It is not resubmitted.
- A job without one (possibly submitted just before a crash) is marked `failed/interrupted` (candidate) or `interrupted` (movie/mesh) and waits for an explicit user retry. **Paid work is never resubmitted automatically.**

**Late results:** workers write only their own candidate, movie, or mesh row. Nothing asynchronous changes `designs.selected_candidate_id`. The UI ignores poll responses for any design or batch other than the one they were issued for.

**Cache keys:** movies are reused per `(candidate_id, movie config_version)`. A partial unique index allows only one queued/running/ready movie per key, which deduplicates repeated Proceed clicks and races. `config_version` is a content hash of `config/generation.json` sections and the prompts, so editing them invalidates reuse automatically.

## 6. API (all JSON; errors are `{"error": {"code", "message"}}`)

| Method & path | Purpose |
|---|---|
| `GET /api/health` | Provider mode, pricing profile version/approval, config versions |
| `GET /api/session` | Token label and usage counts |
| `GET /api/catalog` | Groups → materials, default, ring sizes |
| `GET /api/quote?material_id&ring_size` | Fixed-volume quote (size ignored) |
| `POST /api/designs` (multipart `prompt`, `client_request_id`, optional `reference` + `rights_confirmed`) | New design + initial six-image batch (idempotent per client_request_id) |
| `GET /api/designs`, `GET /api/designs/{id}` | Saved designs; full state for reload recovery |
| `POST /api/designs/{id}/batches` `{parent_candidate_id, instruction, client_request_id}` | Six-image refinement of the selected candidate |
| `GET /api/batches/{id}` | Batch + candidates |
| `POST /api/batches/{id}/retry-failed`, `POST /api/candidates/{id}/retry` | Retry failed slots only |
| `PUT /api/designs/{id}/selection` `{candidate_id}` | Persist selection (ready candidate of this design) |
| `POST /api/designs/{id}/customize` `{candidate_id}` | Proceed: customization (Silver default) + ensure movie |
| `GET/PATCH /api/customizations/{id}` `{material_id?, ring_size?, quantity?}` | Customize; response carries quote, `can_add_to_bag`, reason, movie |
| `POST /api/candidates/{id}/movie` | Start/reuse movie; after failure, a new attempt |
| `GET /api/bag`, `POST /api/bag {customization_id}`, `DELETE /api/bag/{line}` | Server-validated bag of quote snapshots |
| `/api/dev/*` (Bearer `P3_ADMIN_KEY`) | `status`, `candidates`, `candidates/{id}/meshes`, `meshes`, `meshes/{id}`, `meshes/{id}/convert-stl`, `meshes/{id}/download?kind=stl\|original` |

**Checkout:** there is none. The bag reports `checkout_available: false` and states that no order, payment, or production is created. The P2 coupon/reservation screen was not copied.

## 7. Removed relative to Pipeline 2

The following do not exist here in any form, including as dormant calls:

- Visual Hull and its jobs/caches
- bore measurement and ring rescale
- `ringMeasuredDiameter`/`ringBaselineGeo`/`analysisReady`/`open_ring`
- cardinal/quadrant extraction and padding
- multi-view Hitem3D
- `/api/analyze-part` geometry pricing
- size-driven `sf³` pricing
- Veo video, the JPEG scrub-frame pipeline, and fallback catalog prices

The P2 ring classifier was not copied, because it imports VisualHull. The consequence for validation is listed in Open items.

## 8. Open items and decisions needed

1. **Fashion price numbers** — fixed table, or approved CPP reference dimensions, plus the Vermeil plating cost. Until then, Fashion shows "Price unavailable" in the shipped configuration. See [PRICING.md](PRICING.md).
2. **Minimax cost** per video (not published on the model page), and **live validation** of the camera trajectory, duration, and resolution. The chosen values are recommendations.
3. **Batch charging/quota policy.** Six images per batch and per refinement cost about 6× a P2 generation. P3 records usage but enforces no limits. A policy is needed before public use; P2's "one credit per video" was deliberately not reinterpreted.
4. **Prompt/ring validation.** P2's LLM gate (config absent from the P2 checkout) and ONNX ring classifier (VisualHull dependency) are not in P3. Only length validation plus the Nano Banana system prompt constrain outputs. Decide whether an independent validator is needed.
5. **Registration.** Email self-registration was not carried over; tokens are operator-issued.
6. **Checkout/order integration** — out of scope by spec §12; requires a separately identified system.
7. **Video gating.** P3 does not require the movie before Add to Bag (recommendation). Confirm.
8. **Luxury list** — the six P2 golds. White gold and 24K were not offered in P2 Customize and were not added.
9. **Live performance** — no latency or quality claims are made until measured with live providers.
10. **Uploaded reference URL lifetime** — fal storage URLs are reused when a failed refinement slot is retried later. If fal expires them, the retry fails with a provider error, and a new refinement re-uploads.

## 9. Verification evidence

**Automated** (`pytest`, 50 tests; **all provider calls mocked** by `p3/providers/mock.py`; no network):

- six distinct candidates from one prompt with distinct seeds and `num_images=1`
- token required
- idempotent create
- reference upload requires rights confirmation and uses the edit endpoint
- selection ownership
- refinement uses the selected image bytes for all six with one instruction; lineage and prior batch preserved
- missing reference → 409, no text-only fallback
- partial failure → slot and batch retry without regenerating ready slots
- total failure retry
- duplicate re-request and bound
- transient poll retry and bound
- designs do not cross-contaminate; no auto-selection
- restart: 5 resumed, 1 interrupted, zero resubmissions
- path containment
- Proceed shows the selected image; exactly one Minimax call using that image; no Veo params; no mesh call
- repeated Proceed dedupes; movie failure keeps image and price; retry calls only the movie endpoint
- size invariance; quantity affects the line total only
- Luxury clears the price and gets 409 from the bag; switching back restores the Silver price
- bag requires size and an available quote; unapproved/unconfigured profile blocks the bag; owner scoping; reload recovery
- pricing: CPP port equals the P2 browser calculator to 1e-9 (6 cases); dimension sensitivity; unconfigured/unapproved/zero/invalid profiles → unavailable; Vermeil plating rule
- developer routes: disabled without key, 403 with a wrong key or customer token; single-image STL mesh with provenance; GLB→STL conversion; invalid settings rejected; mesh failure leaves the customer flow intact

**Manual UI check** (mock provider, local only, port 8310):

- sign-in
- six-image grid with loading states
- selection border kept after zoom
- refinement progress, then the new batch tab
- Proceed → Customize with the static image, then the movie auto-shown when ready
- Luxury → "Price unavailable" with Add to Bag disabled
- back to Silver with the price restored; size change leaves the price unchanged
- Add to Bag
- developer endpoints via curl (403 without or with a wrong key; native STL download)

Late in the session the preview pane stopped painting (`requestAnimationFrame` never fired), so the final bag screen was confirmed through the page state rather than a screenshot.

**Not exercised:** no live fal.ai call of any kind was made. Live compatibility, output quality, latency, and cost remain unverified. Live validation needs a Pipeline 3 test key with a spending limit and an explicit test budget.

**Pipeline 2 integrity:** checked at the end of implementation (see the final section of the session report). HEAD `1e871734`, status still shows only the two untracked items, and their SHA-1 fingerprints are unchanged.
