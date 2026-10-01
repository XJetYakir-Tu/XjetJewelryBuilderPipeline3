# Accounts, tokens and credits — current design and the shared-auth integration point

## Now (Phase 1)

P3 is operationally separate from P2. It has its own accounts, tokens, usage and database, and makes no connection to P2's `tokens.db` / `designs.db`.

```
            HTTP  X-Access-Token
                     │
                     ▼
p3/auth.py  RequirePrincipal ──► AccountProvider (p3/accounts/__init__.py)   ◄── the ONLY integration point
                                        │
                                        ├── LocalAccountProvider (p3/accounts/local.py)  ← today
                                        │      var/accounts.db: accounts · access_tokens (sha256) · usage_events
                                        │
                                        └── (future) SharedAccountProvider  → shared Users/Auth/Tokens
                     │
                     ▼  Principal(AccountId="p3local:acct_…", DisplayName, Issuer)
services (images, movies, customize, designs) — see only Principal / AccountId
                     │
                     ▼
var/pipeline3.db  (P3 application data only) — designs.owner_account_id, bag_lines.owner_account_id
```

### Rules the code follows

- **One boundary.** Routes resolve the caller with `Ctx.Accounts.Authenticate(token)` into a `Principal`. Services never see the token.
- **No direct identity-storage access outside `p3/accounts/`.** The one exception is the one-time migration in `p3/migrations.py`. `tests/test_accounts.py::test_only_the_accounts_package_touches_identity_storage` fails if any other module references token or account tables.
- **Structured, provider-issued IDs.** `AccountId` is `"<issuer>:<id>"`, for example `p3local:acct_9f…`. Application tables store only this string; there are no foreign keys into the identity domain.
- **Two separate stores.** Identity lives in `var/accounts.db`; application data lives in `var/pipeline3.db`. Neither file contains tables from the other domain (enforced by a test).
- **Credits behind the provider.** Every paid action calls `AuthorizeSpend(Who, Kind, Units)` *before* anything is created or submitted, and `RecordUsage(AccountId, Kind, Units, RefId)` once per actual provider submission:

  | Kind | Units | When |
  |---|---|---|
  | `image` | 4 per batch | a batch or refinement |
  | `image` | 1 | a slot retry |
  | `movie` | 1 | a movie |

  `LocalAccountProvider.AuthorizeSpend` currently always allows. A provider that raises `InsufficientCredits` produces HTTP 402 `insufficient_credits`, and nothing is started (tested).
- **Background jobs** look up the owner from the design (`designs.owner_account_id`). Usage is therefore recorded correctly even for jobs resumed after a restart.
- **Tokens are stored hashed** (SHA-256). The plaintext token is shown once, by `python -m p3.cli create-token`.

### Configuration

`P3_ACCOUNT_PROVIDER=local` (default; the only value implemented). The factory is `p3.accounts.BuildProvider`.

## Future: shared Users / Auth / Tokens for P2 and P3

Target:

```
Shared Users / Auth / Tokens / Credits   (one service or one shared database)
        ├── Pipeline 2  (P2-specific data stays in P2)
        └── Pipeline 3  (P3-specific data stays in pipeline3.db)
```

What has to be built, all of it outside P3's application code:

1. **Add `SharedAccountProvider`.** Implement the `AccountProvider` protocol against the shared system (an HTTP API or a shared database), then register it in `BuildProvider` and set `P3_ACCOUNT_PROVIDER=shared`. No route, service or table in P3 changes.
2. **Account identity.** P2 today keys everything by the plaintext token (`tokens.token` is the primary key; name, email and quota live on the same row). The shared system needs a stable account ID that is separate from the token value (for example `shared:<uuid>`), with tokens as credentials attached to it.
3. **Credits.** P2 counts `generations_used` / `max_generations`, with one generation charged per 360° video. P3 reports `image` and `movie` units. The shared provider owns the conversion policy (for example "a P3 four-image batch costs N credits"). That policy is an **open product decision**. P3 already calls `AuthorizeSpend` before work and `RecordUsage` after each submission, so it plugs straight in.
4. **Migrating P3's existing accounts.** For each `p3local:` account in `accounts.db`, create or link a shared account and rewrite `designs.owner_account_id` / `bag_lines.owner_account_id` with a one-time mapping, the same approach as `p3/migrations.py`. Because the IDs are namespaced, a mapping table (`p3local:acct_x → shared:uuid`) is enough. Alternatively, the shared provider can keep accepting `p3local:` IDs as aliases.
5. **Token hashing.** P2 stores tokens in plaintext and P3 stores SHA-256 hashes. A shared system should store hashes. Existing P2 tokens can be hashed on import, and users keep the same token string.
6. **What stays per pipeline.** P3 keeps `pipeline3.db` (designs, batches, candidates, customizations, movies, meshes, bag). Do **not** merge P3 application tables into P2's `designs.db`; the schemas and semantics differ (four candidates per batch, no measurement).

Not done now, deliberately: P3 does not read or write P2's SQLite files, and does not accept P2 tokens.

## Data migration already performed

Databases created before this change kept tokens and usage inside `pipeline3.db` and keyed designs by the raw token. On first start, `p3/migrations.py`:
- backs up the database to `pipeline3.pre-accounts-<UTC>.db`;
- imports each token into `accounts.db`, keeping the same token string;
- rebuilds `designs` and `bag_lines` with `owner_account_id`;
- moves usage across, then drops the old tables;
- verifies foreign keys before committing.

Re-running it does nothing.
