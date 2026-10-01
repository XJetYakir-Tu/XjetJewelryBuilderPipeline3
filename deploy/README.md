# Deploying Pipeline 3

```
browser ── http://proto/JewelryB2C3/ ──► proto nginx ──► http://tron/JewelryB2C3/ ──► tron nginx ──► 127.0.0.1:8340 (systemd)
```

P3 is operationally separate from Pipeline 2: its own host (tron), process, port, data and tokens. proto only forwards one path to it.

## tron

| Item | Value |
|---|---|
| Checkout | `/home/yakir/git/XjetJewelryBuilderPipeline3` (branch `main`) |
| Python | `.venv` created with `python3 -m venv .venv` (Python 3.12) |
| Config | `.env` in the checkout (never committed): `P3_PROVIDER=mock`, `P3_BASE_PATH=/JewelryB2C3`, `P3_ADMIN_KEY=<random>` |
| Data | `var/` in the checkout (`pipeline3.db`, `accounts.db`, `assets/`, `dev/`, `runtime.json`) |
| Service | `xjet-jewelry-b2c3.service` (`deploy/tron/xjet-jewelry-b2c3.service`), 127.0.0.1:**8340** |
| nginx | `/etc/nginx/snippets/jewelryb2c3.conf` (`deploy/tron/jewelryb2c3.conf`), included from `sites-enabled/dov-hello` (:80) and `conf.d/packtical-ssl.conf` (:443) |
| Health | `http://tron/JewelryB2C3/api/health` |
| Tools Hub | "XJet Atelier Pipeline 3" · Service · `/JewelryB2C3/` · port 8340 |

### Update to the latest `main`

```bash
cd ~/git/XjetJewelryBuilderPipeline3
git pull --ff-only
.venv/bin/pip install -r requirements.txt
sudo systemctl restart xjet-jewelry-b2c3.service
curl -s http://127.0.0.1:8340/JewelryB2C3/api/health
```

### Access tokens

```bash
cd ~/git/XjetJewelryBuilderPipeline3 && .venv/bin/python -m p3.cli create-token --label "Name"
```

### Mock / live

The service starts in mock mode, which makes no paid calls. Switch modes from the Home footer: click **Developer** and enter the server's `P3_ADMIN_KEY`. Live mode also needs `FAL_KEY` in `.env` and a typed cost confirmation.

## proto

Two additions to Pipeline 2's deploy files: a portal tile and a forwarding location. P2 application code is not touched.

```nginx
location ^~ /JewelryB2C3 {
    proxy_pass http://172.16.10.32;        # tron; full path passed through unchanged
    proxy_set_header Host tron;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_http_version 1.1;
    proxy_read_timeout 300s;
    client_max_body_size 20m;
}
```

## Rollback

- **proto:** remove the location and the tile, run `nginx -t`, then reload.
- **tron:**
  ```bash
  sudo systemctl disable --now xjet-jewelry-b2c3.service
  ```
  Then remove the two `include` lines and the snippet, run `nginx -t`, and reload.
- Pipeline 2 is unaffected either way.
