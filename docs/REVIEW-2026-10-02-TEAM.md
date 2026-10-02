# JewelryB2C3 — expert team review (2 October 2026, evening)

**Scope:** the customer site `http://proto/JewelryB2C3/` and the Admin `http://proto/JewelryB2C3/admin/` as deployed (commit `931b3e2`), the full customer journey on the local server in mock mode (same code, no paid calls), the Admin operational flow, and the code and server behind proto.

**How it was reviewed**

- Live site on proto: homepage at 1366 × 768 and 375 × 812, every public page, the gallery lightbox and sign-in dialog, HTTP timings and headers, page weight, accessibility probes (alt text, control names, tap targets, heading order, colour contrast). No design, movie, 3D request or email was triggered on proto.
- Full journey on the local mock server: new design → four images → select → refine → Customize (metal, size, movie, price) → Bag → Checkout (details, shipping, review) → order placed → confirmation; desktop and phone widths.
- Live Admin on proto, signed in: Dashboard, Sessions, Orders (ORD-10002), Gallery, Users, Settings (pricing, promo codes, system health), on desktop and phone widths; Admin API timings.
- Server: nginx, systemd, response times on the box, database and asset sizes, backups, cron.
- Code: authentication and sign-in codes, uploads, middleware, orders and payments seams, 3D review rules and production scaling, tests (190 passing).

Personas: luxury jewellery UX · product · conversion · mobile & accessibility · admin/operations · backend & performance · checkout & payments · 3D pipeline & manufacturing.

---

## 1. What works well

- **A credible premium surface.** Cinzel + Inter, gold accents, generous white space, one restrained voice. The hero now shows real rings, rotating; the gallery is curated; names are short and ownable (Serpent Scale, Aurora Lattice).
- **Honesty everywhere.** Mock mode is always labelled; "Price unavailable" instead of a guess; "Payment pending — no provider connected"; "address stored as entered"; the confirmation page says exactly what happens next and when. Nothing fakes success.
- **The data model is right.** One master design, customers linked to it, variations as designs of their own with lineage both ways, unique Ring IDs that are never reused, one Hi3D model per design reused for every size and material, snapshot pricing on bag lines and orders, idempotent order placement, versioned prompts and material prices with a visible history.
- **Checkout is short and clear.** Three steps, a live order summary, server-authoritative totals, promo codes validated server-side, explicit terms version, a confirmation email with the same facts as the page.
- **The Admin is operational, not decorative.** Needs attention first, one vocabulary for the funnel, a session page that leads to every action (3D, STL, gallery, rename, merge, split), production readiness states with reasons, STL names that can be found months later, a cookie sign-in that survives restarts, and an audit trail with "by" on every change.
- **Engineering discipline.** 190 tests, startup reconciliation of background jobs, WAL SQLite, clear seams for payment and address validation, no secrets in the browser.

---

## 2. Findings by persona

### 2.1 Luxury e-commerce / jewellery UX

- **Phones never see a ring above the fold.** The hero's ring column is desktop-only; on a 375 px screen the first product image appears 2,572 px down. For a jewellery brand the product must be the first thing seen.
- **Legibility is traded for elegance.** The customer site uses 10 px text 47 times and 11 px text 58 times (eyebrow labels, captions, button labels). The light gold `#C9A96E` used for labels measures 2.2:1 on white, grey helper text `#8F8F8F` 3.2:1, white-on-gold buttons 3.5:1 — all below the 4.5:1 AA threshold for small text. Premium typography can keep the tracking and the serif while moving labels to 12 px and darkening the gold used for text.
- **Content pages are uneven.** Technology is 330 characters; FAQ has six short questions; the Inspiration page repeats the homepage grid; About XJet is long and corporate. Sizing help, care, delivery times and the warranty terms (the Customize badge says "2-year warranty"; the policy lives under Shipping & Returns) deserve answers in the FAQ where buyers look.
- **Material story.** Customize lists "Fashion Jewelry" (steel, silver, vermeil) and "Luxury — gold, preview only". The naming is honest but "Fashion Jewelry" reads cheap on a premium site; "Sterling & Steel" and "Gold (made to quote)" would carry the same facts with more dignity.
- **The quota chip** ("Customize Check • 49" in the header) is cryptic; it shows the account name and a number with no noun. "Movies left: 49" on hover is clearer.

### 2.2 Product manager

- **The pilot flow is complete end to end**, including the manufacturing side, which is rare at this stage. The gaps are between the systems and the people: payment, status communication and backups all depend on someone remembering.
- **Internal activity pollutes product metrics.** Every session, order and funnel step on proto today belongs to XJet staff; the dashboard excludes mock mode but has no notion of an internal account. One flag on the account ("staff / internal — not counted") keeps the numbers meaningful from the first real customer.
- **Variation quality is unproven.** The new per-image directives went live today; no refinement has run under them yet. Decide the success measure now (for example, perceptual distance between the four results, which the review tooling already computes) and check the first ten real refinements.
- **No analytics beyond the built-in funnel.** There is no event stream to answer "where did this visitor come from" or "which gallery ring converts". Privacy-friendly, self-hosted analytics would do.

### 2.3 Conversion / growth

- **The sign-in wall is the biggest leak.** The first tap on "Start designing" opens "Sign in or register" with two mental models at once (send a verification link, or enter a 6-letter code). The visitor has not typed a word about their ring yet. Let them write the prompt first, keep it, and ask for the email only when the four images are about to be generated; make the code flow a 6-digit code typed on the same screen.
- **Gold has no price and no path except "Request a quote".** That is honest, but the luxury customer leaves with nothing in hand. A "from" price band per gold material, or an estimated range from the 3D weight, keeps them in the flow.
- **No reassurance near the decision points.** Customize and the bag have no delivery date promise, no "made in real metal by XJet" line, no returns statement; those live three clicks away. The Review step does this well; pull two lines of it earlier.
- **No recovery loops.** A customer who signed in, designed and stopped gets no email; a bag with a ring gets no reminder; a pending-payment order gets no nudge (the Admin flags it after three days, the customer hears nothing). Three automatic emails would recover a meaningful share.
- **Shared links have no preview.** `#gallery=…` links carry no Open Graph image or title, so a ring shared on WhatsApp or Instagram shows a bare URL.

### 2.4 Mobile UX & accessibility

- **The Design screen on a phone needs scrolling for the main action.** After the four images, the composer sits at 949 px on an 812 px screen; the top strip ("Showing · Original · Variations · My Designs") truncates ("VARIAT…"). A sticky bottom composer, or a sticky "Refine / Customize" bar, would keep the next step in reach.
- **Customize on a phone is good**: 44 px size buttons, an Image/Movie toggle, a fixed bottom bar with price and the action.
- **Accessibility basics are mostly there**: `lang`, labelled inputs, focus moved into dialogs, Escape closes them, keyboard-operable grids. Missing: a `<header>` landmark and skip link, heading order jumps H2 → H4 in "How it works", one unnamed control, 12 controls under 40 px on the homepage, and the contrast issues above. `prefers-reduced-motion` is respected by the hero and the waiting screen.
- **Admin on a phone** works (session cards, order page stacks), with a few buttons under 36 px tall on the order page.

### 2.5 Admin / operations

- **Needs attention is the right front door**; Sessions, Orders and the session page cover the daily loop. The merge, split, rename and lineage tools from this week close the data-hygiene gaps.
- **Nothing is automated after the confirmation email.** Status changes (payment confirmed, in production, shipped) do not notify the customer; there is no tracking number or carrier field; "Record a payment" is manual; quote requests are answered by hand with no due date.
- **No production paperwork.** An order has no printable production sheet (ring, material, size, STL name, order ID, QR), no batch view of what to print this week, and no CSV export of orders.
- **Disk and retention.** `var/` is 2.3 GB after one week of internal testing; 7 Hi3D models, their raw meshes and every candidate image and movie are kept forever; 13.9 GB free of 52.5 GB. The system page says so, but nothing acts on it.
- **Backups are manual.** The only database copies are the ones made by hand before migrations, on the same disk. There is no scheduled backup and no off-box copy of the database or the assets that orders depend on.
- **No monitoring.** Failures surface only when someone opens the Admin; there is no uptime check, no alert on provider errors or a full disk, no error aggregation beyond journald.

### 2.6 Backend / architecture / performance

- **Page weight.** The homepage transfers 4.1 MB, of which 4.0 MB are ten gallery images at their full 1024 px size (0.3–1.2 MB each); the hero stacks eight of them, My Designs and the Admin lists use the same full-size files, and assets carry only ETag/Last-Modified, so every page load revalidates every image. There are no thumbnails anywhere.
- **Delivery.** nginx compresses HTML only (`gzip_types` is commented out, so 80 KB of JavaScript and every JSON response go uncompressed); Tailwind runs as a runtime JIT from a CDN (`cdn.tailwindcss.com`), which its authors say not to do in production; `index.html` is 209 KB of inline templates (75 of them). None of this matters on the LAN; all of it matters on a phone on cellular.
- **Server speed is fine.** On the box the page, the gallery API and the Admin sessions list answer in 5–40 ms; the dashboard in 150 ms. What a Windows client sees is a 2.9 s name lookup for `proto` before every new connection, because the name is not in DNS (it resolves by fallback). A proper DNS record or a FQDN removes it.
- **Security posture for a public launch.** The site is HTTP on the LAN; the admin key and the 6-letter customer codes travel in clear. There are no security headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options). Customer sign-in codes are permanent six upper-case letters (26⁶ ≈ 309 M), accepted on `/api/token-status` with no rate limit or lockout, emailed in plain text and shown in the Admin users table. Uploads are capped at 10 MB and normalised; order placement is idempotent; the Admin cookie is HttpOnly and SameSite=Lax.
- **Single process, single file.** SQLite in WAL mode with one uvicorn process is right for the pilot. The background runner resumes jobs after a restart. Growth limits: one process means one CPU for geometry work, and the sessions summary rebuilds every journey on every list call (fast today at 14 sessions; it is O(sessions × queries)).

### 2.7 Checkout / payments / order flow

- **What exists is sound**: server quotes, snapshot lines, promo snapshot, terms version and timestamp, idempotent placement, a confirmation that matches the email, order lifecycle with a final state, manual payment record with a note.
- **No payment means no revenue path at scale.** "We will contact you to arrange payment" is acceptable for a pilot with known customers and wrong for strangers; the seam exists (`PaymentProvider`), so a hosted checkout (Stripe Checkout or PayPal) is a contained change: order stays "new" until the webhook confirms, which is exactly the current model.
- **Tax and duties.** "No sales tax added; duties paid by the recipient" is stated plainly, but EU/UK consumers expect landed prices and US states may require sales tax collection at volume. A later item, but decide the markets before marketing.
- **Address quality.** Validation is a seam with no provider; shipping is a flat worldwide pair (free up to 14 business days, express $45 about 5). Fine for a pilot; wrong addresses will become the top operational cost.
- **Customer visibility.** After the confirmation, the customer's only view is a line in the account panel; no status page, no emails on progress, no cancellation window.

### 2.8 3D pipeline / manufacturing workflow

- **The pipeline is well thought out**: one model per design, measure once exactly, scale by arithmetic, production readiness with reasons (no bore, bore not round, open edges, volume heuristic), review states that block production until a human decides, STL names that identify design, option, order, material and size.
- **Production scaling ignores sintering shrinkage.** The production STL is the raw model scaled uniformly to the target inner diameter; there is no per-material shrinkage compensation (NanoParticle Jetting parts shrink on sintering, typically by a material-specific factor), so a ring printed from this STL will come out smaller than the target size. The compensation factor belongs on the material table next to density.
- **Uniform scaling also scales band width and thickness** (documented as a known limitation): a size 12 version of a design is proportionally thicker than a size 5. Acceptable for a pilot; a real range needs the band cross-section held constant while the bore changes.
- **No manufacturability checks** beyond watertightness and the bore: minimum wall and feature thickness, enclosed voids that trap support material, overhang orientation and the stone seats that the AI likes to draw. One failing print costs more than the check.
- **No feedback loop.** Actual printed weight and the fit on a mandrel are never recorded against the model, so density, shrinkage and the fixed-price basis cannot be calibrated from reality. Two fields on the order line (printed weight, fit result) close the loop.

---

## 3. Bugs and inconsistencies found in this pass

| # | Where | What | Severity |
|---|---|---|---|
| B1 | Design screen, phone | The strip "Showing · Original · Variations · My Designs" truncates ("VARIAT…") at 375 px. | Medium |
| B2 | Customer site | Small text below 12 px in 105 places; gold labels, grey helper text and white-on-gold buttons fail AA contrast. | High |
| B3 | Homepage | 4.1 MB per load, ten full-size PNGs; no thumbnails; assets revalidated on every load. | High |
| B4 | Sign-in dialog | Two flows in one dialog (verification link and 6-letter code), so the next step is unclear. | High |
| B5 | Homepage, `How it works` | Heading order jumps from H2 to H4; no `<header>` landmark or skip link. | Low |
| B6 | Header chip | "Customize Check • 49": account name plus a bare number. | Low |
| B7 | nginx | JavaScript and JSON are not compressed (`gzip_types` commented out). | Low |
| B8 | Hostname | `proto` costs a 2.9 s name lookup from Windows clients outside the LAN DNS; the site feels slow although the server answers in milliseconds. | Medium (infra) |

Checked and found fine: the "2-year warranty" badge is backed by a Warranty section under Shipping & Returns; dialogs trap focus and close on Escape; the lightbox renders opaque (an apparent transparency was the test browser pausing animations in a background tab).

---

## 4. Prioritised list

### Critical — before real customers pay real money

| # | Item | Why | Action |
|---|---|---|---|
| C1 | **Automated, off-box backups** of the database and the assets orders depend on | Everything (orders, 3D models, customer accounts) lives in one SQLite file and one folder on one disk; the only copies are manual, on the same disk. | Nightly `sqlite3 .backup` + rsync of `var/` to a second host or object storage; keep 30 days; test a restore once; alert on failure. One evening of work. |
| C2 | **HTTPS, security headers and sign-in hardening** | The admin key and customer codes travel in clear; codes are permanent 6-letter tokens guessable without limit. | TLS on nginx (Let's Encrypt or the internal CA), HSTS/CSP/X-Frame-Options/nosniff, rate-limit and lockout on `/api/token-status` and registration, consider a code that expires and rotates on each sign-in. |
| C3 | **Sintering shrinkage compensation and a wall-thickness check** before the first production print | The production STL is scaled to size with no shrinkage factor; a printed ring will come out small. | Add a shrinkage factor per material to the material table, apply it to the production scale, record it in the geometry result and the STL name; add a minimum wall/feature check to the review items. Agree the factors with process engineering. |
| C4 | **A payment provider behind the existing seam** | Without it every order is a reservation collected by email and phone; conversion and cash depend on follow-up by hand. | Stripe Checkout (hosted page, webhook confirms "paid"); keep the manual record as the fallback; add the "Pay now" link to the confirmation email for orders placed before the provider existed. |

### High — the next two weeks

| # | Item | Why | Action |
|---|---|---|---|
| H1 | **Design first, sign in second** | The sign-in wall is the first thing a visitor meets; two flows in one dialog. | Let the prompt (and a pasted photo) be entered without an account; ask for the email when generating; one flow: 6-digit code typed on the same screen, link as the alternative. Keep the pending prompt like the pending gallery pick. |
| H2 | **Thumbnails and asset caching** | 4 MB homepages and full-size images in every list. | Write 320 px and 800 px WebP thumbnails when a candidate becomes ready (and once for existing assets); `srcset` on tiles, hero, My Designs and Admin lists; `Cache-Control: public, max-age=1y, immutable` on `/assets`; `gzip_types` for JS/JSON. |
| H3 | **Readable premium typography** | AA failures and 10–11 px text across the site. | Minimum 12 px for labels and captions, 13–14 px for helper text; text gold `#8A6420` (5:1) with `#C9A96E` kept for rules and ornaments; button labels 12 px semibold on the darker gold. |
| H4 | **A ring on the phone's first screen** | Mobile visitors see no product until they scroll 2,500 px. | A compact hero visual under the headline on phones (one rotating ring, ~60% width), CTA within the first screen. |
| H5 | **Customer communication after the order** | Only a confirmation email exists; status changes are silent; no tracking. | Emails on Payment confirmed, In production and Shipped (with carrier and tracking number fields on the order and in the Admin); a simple order status page from the account panel. |
| H6 | **Internal accounts excluded from statistics** | All metrics today are XJet's own activity. | An "internal" flag on accounts; dashboard, gallery statistics and Needs attention exclude internal unless toggled, like mock today. |
| H7 | **Monitoring and alerts** | Failures are discovered by opening the Admin. | Uptime check on `/api/health`, alerts on disk < 10 GB, provider error rate, failed emails; error aggregation (Sentry-style) for the app. |

### Medium — the following month

| # | Item | Why | Action |
|---|---|---|---|
| M1 | Mobile Design screen: sticky composer or action bar; fix the truncated strip (B1) | The next step is below the fold on phones. | Bottom-anchored composer on phones; "Refine" and "Customize" in the fixed bar. |
| M2 | Reassurance near decisions | Delivery, real metal, warranty and returns are three clicks from Customize and the bag. | Two lines under the price in Customize and in the bag: "Made to order in real metal by XJet · ships in 7–10 business days · 2-year warranty". |
| M3 | Recovery emails | Abandoned designs, bags and pending payments get nothing. | Three scheduled emails with sensible delays and a one-click return link; opt-out. |
| M4 | Production paperwork and exports | The shop floor has no sheet; nothing exports. | Printable production sheet per order (QR to the order), "to print this week" list, CSV export of orders. |
| M5 | Content: FAQ, Technology, sizing | Thin pages; sizing questions go unanswered. | Ten FAQ answers (sizing, resizing, care, delivery, warranty, materials, duties), a short technology story with one image, a size guide reachable from the FAQ. |
| M6 | Material naming | "Fashion Jewelry" undersells. | "Sterling & Steel" / "Gold — made to quote", same facts. |
| M7 | Retention policy | 2.3 GB per week of testing, kept forever. | Delete candidate images and movies of designs removed by customers after 90 days unless ordered; keep raw meshes of ordered designs; show the policy on the system page. |
| M8 | Gold pricing path | Luxury customers leave with no number. | "From $x" per gold material from the 3D weight × price, labelled as an estimate, with the quote request as confirmation. |
| M9 | Address validation provider | The seam has no provider; wrong addresses will cost most. | Connect one provider behind `AddressValidator`; keep "Corrected/Verified/Failed" states as designed. |
| M10 | Share previews | Shared ring links show no image. | Server-rendered Open Graph tags for `?gallery=<id>` links. |
| M11 | Manufacturing feedback loop | Density, shrinkage and prices are never calibrated against reality. | Printed weight and fit result on the order line; a dashboard comparison against the predicted weight. |
| M12 | Tailwind build and bundle | Runtime JIT from a CDN; 209 KB HTML. | Build Tailwind at deploy, split the page templates, keep `?v=` versioning. |

### Later

| # | Item | Why |
|---|---|---|
| L1 | Landed prices: VAT/sales tax by market, multi-currency | Required before marketing in the EU/UK/US at volume. |
| L2 | Band cross-section held constant across sizes | Uniform scaling thickens large sizes; needs a geometry step, not arithmetic. |
| L3 | Self-hosted analytics and attribution | To know which rings and channels convert. |
| L4 | Localisation (Hebrew/RTL) and SEO landing pages per gallery design | Growth, not pilot. |
| L5 | Postgres and a second app process | Only when one box is not enough; SQLite + one process is right today. |
| L6 | Reviews, gifting, wishlist, appointment with a designer | Premium extras once the core converts. |

---

## 5. Recommended order of work

1. **C1 backups** (half a day) and **H7 monitoring** (half a day) — nothing else is safe to build on top of a single unbacked disk.
2. **C2 HTTPS and sign-in hardening** (one to two days) — a prerequisite for any public link.
3. **H2 thumbnails and caching + B7 gzip** (one day) and **H3 typography/contrast** (one day) — the biggest perceived-quality gains per hour.
4. **H1 design-first sign-in** (two days) and **H4 mobile hero** (half a day) — the conversion items.
5. **C3 shrinkage and wall checks** (one to two days with process engineering) — before the first paid print leaves the building.
6. **C4 payment provider** (two to three days) and **H5 order communication** (one to two days) — together they make an order self-service from click to doorstep.
7. **H6 internal flag** (half a day) — before the first real customer, so the numbers start clean.
8. Then the Medium list in the order given, M1–M4 first.

Nothing above changes the gallery-master architecture, the naming, the lineage rules or the 3D model-per-design rule; they are the parts to keep.
