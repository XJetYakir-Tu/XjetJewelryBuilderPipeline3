# P3 Admin — Dashboard · Sessions · Users

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

## Generate 3D (admin only)

`POST /api/admin/sessions/{id}/3d`, code in `p3/production3d.py` and `p3/geometry.py`.

- **Never automatic.** Only this admin action starts Hi3D v3.0, on the session's selected option.
- **Ring size:** the customer's size, else **US 10**; the admin can override it before generating. Stored as `customer_size` and `production_size`, with `size_source` = customer / default / admin_override. Material is handled the same way: customer, default, or admin override.
- **No repeated paid calls.** If a raw Hi3D model already exists for that option, it is reused: a new size or material re-scales and re-measures it without another Hi3D call.
- **Geometry** (`ring-bore-sections-v1`):
  1. Repair: merge vertices, drop degenerate faces, fix normals, fill holes.
  2. Find the ring axis: the direction of least surface spread.
  3. Find the bore: cross-sections at 3 heights, the nearest wall per 5° bin, then a circle fit with an iterated centre. The narrowest height gives the inner diameter.
  4. Scale uniformly to the target inner diameter (US size → mm: 11.63 + 0.8128 × size), with the axis aligned to Z.
  5. Measure: X/Y/Z, inner diameter, volume (only if watertight) and surface area.
- **Checks recorded:** watertight before/after repair, bore roundness, whether the inner diameter after scaling matches the target, and the known limitation that uniform scaling also scales band width.
- **Status:** `measured` or `needs_review`.
- **Accuracy:** tested against an ideal ring in a random orientation and arbitrary units. Inner diameter is within 0.2%, volume and surface area within 1%.
- **Stored per stage** in `geometry_results`, raw and production (STL kept). Both downloads are admin-only.
- **Weight** = volume × density from `config/materials.json`, stored in `price_calculations`.

## Fixed price vs production cost vs 3D price

Three separate values that never overwrite each other:

1. **Fixed customer price:** the price the customer saw. It is the Add to Bag snapshot, else the price shown in Customize, else the current list price, always with its pricing version. Customer pages never read 3D data, so this price can't change.
2. **Production cost:** weight × metal price per gram + production cost.
3. **3D calculated price:** production cost × markup.

Values 2 and 3 come from `config/production_costs.json`, which is **not configured**: every value is null. Until real numbers are entered, they show "—" with status `cost_model_not_configured`. Nothing is invented.

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
