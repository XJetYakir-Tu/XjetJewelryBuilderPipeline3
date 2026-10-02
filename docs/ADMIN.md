# P3 Admin — Dashboard · Sessions · Users · AI Prompts & Params

- **Page:** `{base}/admin/`, i.e. `http://proto/JewelryB2C3/admin/`.
  - The customer site never links to it.
  - It is served with `X-Robots-Tag: noindex, nofollow`.
- **API:** `{base}/api/admin/*`.
- **Code:** `p3/admin.py` (routes and authorization), `p3/usage.py` (activity from application data), and the admin methods of `LocalAccountProvider` (identity side).
- **UI:** `web/admin.html` and `web/admin.js`.

## Authorization

Every admin route calls `RequireAdmin(Ctx, Authorization)`, which returns an `AdminPrincipal`.

- **Today** it accepts the existing developer key: `Authorization: Bearer <P3_ADMIN_KEY>`, the same key that protects `/dev`. With no key configured, admin is disabled (503).
- **The page** asks for the key once and keeps it in `sessionStorage`, so only for that browser tab.
- **Before the public site goes live**, replace the body of `RequireAdmin` with a proper admin role or company sign-in, for example SSO. The routes and UI do not change. Optionally, nginx can also restrict `/JewelryB2C3/admin` to the office network.

## Users — token management (from B2C2 `/admin/tokens`)

| B2C2 | P3 |
|---|---|
| Name, Email optional | **Name and Email required**, email format checked server-side |
| duplicate emails allowed | **one account per email**: 409 with a link to the existing user |
| Max Generations (default 10) | same, 1–9999 |
| 6-letter token, profanity-filtered | same (shared generator) |
| table: Token, Name, Email, Used/Max, Status, Created | same, plus **Last activity**; statuses Active / Unused / Pending verification / Exhausted / Inactive / Removed |
| Edit: name, email, max, reset usage | same (Name/Email still required) |
| Deactivate / Activate | same: deactivating turns off all of the account's tokens, and activating turns its current token back on |
| Remove = hard delete | **soft remove**: tokens revoked and the user hidden (shown again under "Show removed"). Designs and usage history are kept, and the user can be restored. |
| rows not clickable | **row opens the user detail** |

## User detail

Everything is counted from recorded data; nothing is estimated.

- **Account:** created, email verified, first activity, last sign-in, and last activity.
  - Sign-ins are recorded from 1 Oct 2026, in `account_events`: entering a token, or opening a verification/email link.
  - Last activity is the time of the last authenticated request.
- **Generations used (charged):** one per finished 360° movie (B2C2 rule).
- **Job counts:**
  - image generations, refinements (batches and their images), 360° movies, and 3D meshes (developer tool), each with ready and failed counts;
  - total jobs succeeded and failed;
  - designs created (every design is saved automatically) and lines added to the bag;
  - jobs by provider (mock / live) and sign-ins.
- **Usage over time:** daily bars for the last 90 days.
- **Designs gallery:** thumbnails. Clicking one shows every batch, the four options, the selected one, and its movies.
- **Activity timeline:** design, refinement, movie, 3D, bag, sign-in and admin actions.

## Sessions — the main entity

A **session is one design journey**:
- It starts when the customer submits the first prompt of a New Design (`designs.created_at`). A New Design click with no prompt is only counted on the Dashboard ("New Design clicks").
- Reopening the design from My Designs continues the same session.
- It ends when the customer starts another New Design, or after **30 minutes** without activity.

**Stages:** Started → Generated → Customize → Bag → **Checkout Clicked**.
- Checkout isn't implemented. The button stays "Checkout Unavailable" but is clickable, and each click is recorded.
- Refinements are counted alongside the stages, e.g. `Generated → Refined ×2 → Customize → Bag`.
- A session that ended before the Bag gets `→ stopped`.

**Where the data comes from** (code: `p3/sessions.py`):
- **Job tables:** stage times from batches, candidates, customizations, movies and bag lines.
- **`session_events`** (append-only, `pipeline3.db`) adds what those don't keep:
  - option selected, Customize opened, and every material/size/quantity change, each with the **fixed price shown** and its pricing version;
  - bag added/removed (with a price snapshot), Bag viewed, Checkout clicked, design reopened, New Design clicked;
  - admin 3D actions.
- **Backfill:** sessions from before tracking are rebuilt from the job tables, and existing bag lines get a backfilled `bag_added` event.

**Admin views:**
- **Sessions:** image, customer, email, start / last activity, stage path, generations/refinements, size, material, Bag, Checkout Clicked, 3D status, fixed price, with filters.
- **Session detail:**
  - a journey timeline with the time between steps;
  - Generate 3D, plus any results;
  - the Customize choice history and every image of the session;
  - AI requests for the session (cost "—" until configured).
- **Dashboard:**
  - sessions, New Design clicks and the funnel;
  - average refinements and % selected for 3D;
  - 3D averages (volume, weight by material) and the fixed vs 3D price variance once measured.

## Inspiration Gallery

The gallery on the customer site (home page, first 8, and the Inspiration page) shows **real XJet designs**, chosen in the Admin. Code: `p3/gallery.py`; tables `gallery_items` (the tiles) and `gallery_uses` (customers on them).

- **One shared master design per tile.** A customer who taps **Make it yours** is *linked* to the XJet design (`gallery_uses`); the design, its images, its 360° movies and its Hi3D raw model are never copied. The customer's own selection lives on the link, their Customize choices in `customizations` (one row per customer, design and option), their bag lines in `bag_lines`.
- **Curate:** Sessions → open a session → **Show in gallery** (the design's selected image), or **Use in gallery** under any ready option. ↑ ↓ in the Gallery tab set the display order; **Copy link** gives a share link (`…/JewelryB2C3/#gallery=<id>`) that opens the design's preview directly; **Remove** takes the tile off the site (customers keep their links; the design stays listed as "removed from gallery" while it has journeys).
- **Customer:** one tap opens a large preview — "Love this design? Make it yours." — with **Make it yours** / **Back to gallery**. Signed in: the Design screen opens on the shared design with the gallery image selected ("Your gallery pick and its three siblings"). Not signed in: the sign-in flow opens and the design opens right after. Starting the same design again moves it to the top of My Designs — never a second entry. The gallery is also reachable from the My Designs sidebar (**Inspiration Gallery**).
- **Costs:** nothing is generated or charged by Make it yours. A 360° movie that exists is shown at once; a movie a customer asks for on another option is charged to *that customer* (`movies.requested_by`). A **refinement** of a shared design becomes a design of the customer's own (`source_design_id` = the master; event `gallery_refined`), so the master never changes.
- **Journeys:** every customer on a shared design is a session of its own (session id `use_…`) — in Sessions, on the user's page and in the Dashboard funnel — with "Started from gallery · R-1013-A". The master design's own page and each customer's page show **Customers on this gallery design** (who, when, path, size, material, bag, checkout). Generate 3D from a customer's journey uses that customer's size and material; the Hi3D model belongs to the design and is shared.
- **Gallery tab:** every master design with usage statistics — Selected (journeys), Customers (unique), Customize, Bag, Checkout, 3D, Refined, last used — sortable as Most selected / Most popular / Most added to bag (no "sold" until orders exist); a row opens its customers.
- **Dashboard:** the funnel is also split by origin (own prompt vs gallery).

## Generate 3D (admin only)

`POST /api/admin/sessions/{id}/3d`, code in `p3/production3d.py` and `p3/geometry.py`.

- **Never automatic.** Only this admin action starts Hi3D v3.0, on the session's selected option.
- **Ring size:** the customer's size, else **US 10**; the admin can override it before generating. Stored as `customer_size` and `production_size`, with `size_source` = customer / default / admin_override. Material is handled the same way: customer, default, or admin override.
- **No repeated paid calls.** If a raw Hi3D model already exists for that option, it is reused. Retrying a failed local step never repeats Hi3D while the raw STL exists ("Retry geometry (no Hi3D charge)"). Only a failed Hi3D request offers "Retry Hi3D (paid)".
- **One Hi3D model per design (safeguard).** Once a design has a valid Hi3D model, every later request — any size, material, selected option or customer journey — reuses it: the button reads **Recalculate geometry**, the page says *Existing 3D model available* (… *from Gallery Master* on a customer journey), and no Hi3D call is made. A second paid model (for a genuinely different option) is only possible through **Generate a completely new Hi3D model instead…**, which warns and requires typing `GENERATE NEW 3D`; the server refuses the request without that exact phrase (`hi3d_model_exists`) and records the override on the journey and in the Dashboard's **Admin activity** log. A refined design is its own design and gets its first model normally.
- **Hi3D settings:** 2048quality, 5,000,000 faces, STL only.
- **Download:** the STL is streamed straight to disk (`meshes/<id>/original.stl`), with its SHA-256 calculated during the download. Hi3D's thumbnail is saved too.
- **Measure once** (`ring-measure-once-v3`, `p3/geometry.py` `MeasureRaw`). The full raw STL is measured exactly once, and the results are stored in `raw_geometry`:
  - raw X/Y/Z and inner diameter;
  - volume and surface area;
  - ring frame (orientation) and bore centre;
  - SHA-256 and method version.

  How it works:
  1. Find the ring axis from the exact surface moments.
  2. Find the bore: exact cross-sections at 3 heights, then a circle fit. The narrowest height gives the inner diameter.
  3. Compute the volume from two reference points. If they agree, the mesh is probably closed. This is a cheap heuristic, not proof of watertightness.
- **Any size or material is arithmetic** (`Scaled`), with no file read: s = target ID / raw ID, then lengths × s, area × s², volume × s³, and weight = volume × density (`config/materials.json`). US size → mm: 11.63 + 0.8128 × size.
- **Status:** `measured`, or `needs_review` if:
  - no bore was found;
  - roundness deviation is over 4%;
  - the closed-mesh heuristic disagrees.
- **Background, never blocking the numbers:**
  - a light preview (~25k faces, `preview.p3pv`; visual only, never used for numbers);
  - a mesh-integrity check (edge manifoldness → `raw_geometry.integrity`).
- **Scaled STL:** never stored. "Download scaled STL" queues an export job that writes a temporary STL (`exports/`), aligned with the bore centre at the origin, axis Z, in millimetres. That frees the processing slot; the browser then downloads it natively through a signed link. The file is deleted after 1 hour. Requests measured before v3 keep their stored scaled STL.
- **Accuracy:** tested against an ideal ring in a random orientation and arbitrary units. On a 5M-face torus, inner diameter and volume match the analytic values.
- **Stored per stage** in `geometry_results` (raw and production), with weight and price in `price_calculations`.

### Processing queue and live status

- **One job at a time.** Heavy local work on the 5M STL runs one job at a time in a persistent queue (`geometry_jobs`, `p3/geoqueue.py`). Each job runs in a memory-capped worker process (`p3/geometry_worker.py`, `P3_GEOMETRY_MEMORY_MB`). Priority order: export, then measure, then preview, then integrity.
- **Restarts:** after a restart, a running job is queued again; it fails if it is interrupted twice.
- **Cancel:** a queued job can be cancelled before it starts.
- **Real stages, with persisted start and end times** (`stage_log`), so a refresh shows the same state:
  - `Waiting for Hi3D — 00:42`
  - `Generating 3D — 03:11`
  - `Downloading STL — 64% — 00:08` (a real byte count)
  - `Queued — 1 ahead`
  - `Calculating Geometry — 00:06`
  - `Ready — Total 04:37`

  No other percentages are shown. The page polls only `GET /api/admin/3d/{id}/status`, every 2 s; the clock ticks in the browser, synced to server time.
- **Endpoints:**
  - `POST …/3d/{id}/cancel`, `…/retry`, `…/export`;
  - `GET …/3d/{id}/export/{job}`;
  - `GET /api/admin/storage` (3D bytes and free disk; shown on the Dashboard, with no automatic deletion).

## Fixed price vs production cost vs 3D price

Three separate values that never overwrite each other:

1. **Fixed customer price:** the price the customer saw. It is the Add to Bag snapshot, else the price shown in Customize, else the current list price, always with its pricing version. Customer pages never read 3D data, so this price can't change.
2. **Production cost:** weight × cost $/g.
3. **3D calculated price:** weight × price $/g.

Weight = 3D volume × sintered density.

All four inputs come from one versioned table, **Admin → AI Prompts & Params → Pricing · materials** (`p3/materialprices.py`, table `material_price_lists`): density, price $/g, cost $/g and fixed price, for each material.

- **Fixed price** is what the website shows for the material. Empty means "NA": the website shows "Price unavailable" (luxury gold today). It is a placeholder until there is enough data to set it from real 3D costs.
- **Density** in this table is the only density used: weight on the website and in 3D.
- **Saving** creates a new version (who, when, note). New 3D results and website quotes use it immediately. Results already calculated keep the version they were made with.
- **Seed:** the business table of 2026-10-02 (Silver 925 / 316L / Vermeil / Gold 18K / 14K). 10K gold had no values.
- An empty price or cost shows "—", with the reason. Nothing is invented.

## Cost reporting (prepared, not active)

Each usage event in `accounts.db` now records:

- `provider` (mock | fal), `endpoint` (model id) and `mode` (mock | live);
- `cost_usd` and `cost_source`, left NULL for now.

Events recorded before this change were filled in from the job tables at startup (`p3.usage.BackfillUsageAnnotations`).

The detail view's **Provider usage** table already groups requests by kind, provider and endpoint. To report cost per user:

1. add a provider price table (endpoint → cost per unit, versioned);
2. set `cost_usd` and `cost_source` when usage is recorded, or compute them in the report.

Until then the Cost column shows "—".

## Tests

`tests/test_admin.py` covers:

- protection (customer token rejected, admin disabled without a key);
- Name/Email validation and duplicate emails;
- edit / deactivate / activate / soft remove / restore;
- detail counts after a design, a refinement, a successful movie and a failed one;
- the provider ledger, sign-in recording and the usage backfill.

## AI Prompts & Params

**Code:** `p3/modelconfig.py`. **UI:** the *AI Prompts & Params* tab. **API:** `/api/admin/models/*`, which needs the admin code.

**Models.** All five endpoint identifiers were checked against the existing integration and fal.ai's OpenAPI on 1 Oct 2026.

| Tab | Endpoint | Used for |
|---|---|---|
| any-llm | `fal-ai/any-llm` | **Not used by the P3 pipeline yet** (B2C2 uses it for its prompt gate). Settings can be prepared and previewed. |
| nano-banana-pro | `fal-ai/nano-banana-pro` | New Design without a reference image: 4 separate requests, 1 image each |
| nano-banana-pro/edit | `fal-ai/nano-banana-pro/edit` | Refinements (selected image), and New Design with an uploaded reference |
| minimax camera | `minimax/h3-max/camera-controls` | The 360° movie (selected final image) |
| hi3d | `hitem3d/hi3d/v3.0/image-to-3d` | Admin Generate 3D and the developer mesh tool |

**Source of truth.**
- Every new request is built from the model's **active version**. The version id is recorded on the batch, movie or mesh (`config_version`).
- A request created before an activation keeps its version, even if it is submitted afterwards.
- Version 1 was seeded from `config/generation.json` and `config/prompts/*`, and sends exactly the same requests as before (tested). Those files are no longer read for model parameters.
- The old hardcoded "text + suffix" is now the editable image prompt template `{{user_text}}

<suffix>`. No suffix is added on top of it.

**Configured vs omitted.** Each parameter is either *configured* (sent) or *not sent*, in which case the provider default applies and is shown in the form. Required provider parameters are always sent: the image `prompt`, and the movie `prompt_expansion_mode`.

**Runtime inputs.** These are supplied by the pipeline and never stored in a configuration:
- **Image models:**
  - `{{user_text}}`: the customer's prompt or refinement instruction. Required in the image prompt templates.
  - `{{design_prompt}}`: the session's original prompt. Optional.
  - `image_urls` (edit model): the selected or reference image.
  - `seed`: random, different for each of the 4 images.
  - `num_images`: always 1.
- **Movie:** `image_url` is the selected final image.
- **Hi3D:** `image_url` is the selected design image.
- **any-llm:** `{{user_prompt}}`.
- **Not sent:** `sync_mode`. The pipeline downloads results from their URL, so the provider default (false) applies.

Saving can't replace these with example text or a fixed URL: such fields are refused, and templates must contain their placeholder.

**Validation** runs before activation, against each model's supported parameters:
- types, choices and ranges;
- unknown or pipeline-controlled fields;
- unknown or missing placeholders;
- camera keyframes: 2–12 keyframes in time order, elevation −90…90, distance > 0, at most 32 turns of azimuth travel;
- Hi3D `export_format`: limited to GLB/OBJ/STL, because the geometry measurement can only read those.

**Saving and history.**
- **Preview** shows the exact request payload with clearly labelled `[SAMPLE …]` runtime inputs, and never calls the provider.
- **Save & Activate** creates an immutable new version. If nothing changed, no version is created.
- **History** lists every version with its date, author and note. *Load into form* lets you review or edit an old version; *Restore & activate* makes a copy of it the new active version.
- **Movie reuse:** an existing movie is reused when its version has the same parameters as the active one. That includes movies made before versioning, through version 1's legacy alias. Changing movie settings means the next new movie request uses them. Customers who reopen Customize on an option get a new movie (charged as usual) only when its settings actually changed.

**Export:** TXT (readable) or JSON (structured: model ids, endpoints, version ids and numbers, parameters, which parameters are omitted, and the pipeline-controlled fields), for one model or all. Configurations contain no API keys, and the export includes none.

## AI cost estimates (Sessions)

**Code:** `p3/aipricing.py`. **Admin:** *AI Prompts & Params → AI prices*.

- **Where costs come from.** A versioned price list per endpoint, seeded from the fal.ai pricing API (`GET https://api.fal.ai/v1/models/pricing`, read-only) and the model pages (1 Oct 2026). *Refresh from fal.ai* updates the unit prices with the server's key; the key is never shown. *Edit* changes the list by hand, and every change is kept as a new version.
- **How a P3 request is priced:**
  - images: per image, $0.15 (one 1K image per request);
  - movie: per second by resolution × the movie's duration (taken from the request's recorded configuration version);
  - Hi3D: credits × $0.02, where credits = geometry (90 at 2048quality, 440 at 2048master) + texture 10 and PBR 5 when enabled.
- **What is counted.** Each provider submission recorded for a job, so retries are included. Mock requests cost $0.
- **Estimates, not invoices.** Account discounts, promotions and unlisted surcharges are not reflected. fal.ai labels the minimax per-second rates as launch prices (50% off until 30 Sep 2026); check them.

**Session detail now shows:**
- user status (active, exhausted, inactive, removed…);
- AI cost for the session and for the user in total;
- the selected image, the 360° movie, and the 3D model: Hi3D's thumbnail first, then an interactive three.js viewer of the light preview;
- a pipeline flow with each step's duration, requests and cost;
- 3D results in cc and cm², with a material colour dot, plus "what was done to the model" (repair, bore, scale, alignment, measurement);
- the customer's last Customize choice (full history on request);
- the journey, at the end.

**The Sessions list** shows the user status and *Has* chips (Image / Movie / 3D) for each session.
