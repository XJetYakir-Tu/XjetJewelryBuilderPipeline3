# XJet Jewelry Builder — Pipeline 3

A leaner ring-design flow:

**six images → select (with zoom) → refine the selected image into six more, or proceed → Customize with the static image plus a Minimax camera-controls movie → fixed 1 cm³ Fashion pricing / preview-only Luxury.**

There is no Visual Hull and no measurement. Hitem3D/STL is developer-only.

This repository is independent of the commercial Pipeline 2 (`XjetJewelryBuilder`). It shares no code, data, configuration, or deployment with it.

- Spec (planning baseline): [docs/PIPELINE_3_SPEC.md](docs/PIPELINE_3_SPEC.md)
- What was built, the decisions made, and the open items: [docs/IMPLEMENTATION.md](docs/IMPLEMENTATION.md)
- Pricing rules and what still needs approval: [docs/PRICING.md](docs/PRICING.md)

## Setup (Windows, Python 3.13)

```bash
py -3.13 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
cp .env.example .env        # then edit .env
```

## Run locally (mock provider — no network, no cost)

The default is `P3_PROVIDER=mock`. For a usable local price, add these to `.env`:

```
P3_PRICING_PROFILE=config/pricing_profile.dev-example.json
P3_ALLOW_UNAPPROVED_PRICING=true
P3_ADMIN_KEY=<any long random string, to enable /dev>
```

Then issue yourself an access token and start the server:

```bash
.venv/Scripts/python -m p3.cli create-token --label "me"
.venv/Scripts/python -m uvicorn p3.app:App --port 8310
```

Open http://localhost:8310 and enter the token. Developer mesh tools are at http://localhost:8310/dev (they require `P3_ADMIN_KEY`).

## Mock vs. live mode

The site always shows which mode it is in:

- **Mock** (default): an amber "Mock mode" banner plus a **Mock** chip in the nav. Images, movies and meshes are simulated placeholders, nothing is sent to any AI provider, and nothing is charged.
- **Live**: a green **Live AI** chip in the nav. Requests go to fal.ai and are billed.

`GET /api/health` reports `"mode": "mock" | "live"`.

## Going live (paid)

1. In `.env`, set `P3_PROVIDER=fal` and `FAL_KEY=<key>`. Ideally use a **Pipeline 3 key with a spending limit**, not the one Pipeline 2 uses.
2. Check the key without running any model. This makes a free 1×1 storage upload:
   ```bash
   .venv/Scripts/python -m p3.cli check-provider
   ```
3. Restart the server (it does not auto-reload), then confirm the nav shows **Live AI**.

Costs at published fal.ai rates:
- Each image batch or refinement is six Nano Banana Pro requests, about $0.90.
- Each Proceed on a new candidate is one Minimax request (price not published).
- Each developer mesh is about $2.10.

See docs/IMPLEMENTATION.md §4 before running live.

## Tests

```bash
.venv/Scripts/python -m pytest
```

All provider calls in the tests are mocked; no test uses the network.

## Runtime data

The SQLite database and generated assets live in `var/` (gitignored), or wherever `P3_DATA_DIR` points. Customer assets are in `var/assets/`. Developer meshes are in `var/dev/` and are never served statically.
