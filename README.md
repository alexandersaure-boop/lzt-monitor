# LZT Market Monitor

Watches a lzt.market search URL and pings a Discord webhook whenever a new
account is listed. Built for the DayZ-700h-30d-NSB search; any lzt.market
filter URL works.

Two ways to run it:

- **GitHub Actions** — free, cloud-hosted, 5-minute interval. *Recommended.*
- **Local / VPS** — runs as a loop on your own machine, 2-minute interval.

---

## Option A — GitHub Actions (free, cloud)

You'll end up with a GitHub repo whose only job is to run this script every
5 minutes. Zero servers, zero cost.

### 1. Create the repo

1. Sign in to <https://github.com> (make an account if needed).
2. Click **New repository** (green button top-right or <https://github.com/new>).
3. Name it anything, e.g. `lzt-monitor`.
4. **Public** is recommended — GitHub Actions minutes are unlimited for public
   repos, but capped at 2000/month for private (and this workflow uses ~4300/mo
   at a 5-min interval). Nothing sensitive ends up in the code; tokens live
   in Secrets.
5. Don't tick "Add a README" — we have our own.
6. Click **Create repository**.

### 2. Upload the files

Easiest path (web UI, no git CLI needed):

1. On the new repo page, click **uploading an existing file**.
2. Drag in `lzt_monitor.py`, `requirements.txt`, and `README.md`.
3. Commit.

Now the workflow file, which needs to live at `.github/workflows/monitor.yml`:

4. Click **Add file → Create new file**.
5. In the filename box, type exactly: `.github/workflows/monitor.yml`
   (the slashes auto-create the folders).
6. Paste the contents of `monitor.yml`.
7. Commit.

### 3. Add your secrets

1. Go to **Settings** (tab along the top of the repo) **→ Secrets and variables → Actions**.
2. Click **New repository secret**.
3. Name: `LZT_API_TOKEN` — Value: your token from <https://lzt.market/account/api>.
   Click **Add secret**.
4. Click **New repository secret** again.
   Name: `DISCORD_WEBHOOK_URL` — Value: your Discord webhook URL.
   Click **Add secret**.
5. (Optional) Add `LZT_SEARCH_URL` if you want to override the default search
   without editing the script.

### 4. Kick off the first run

1. Go to the **Actions** tab.
2. If prompted, click **I understand my workflows, go ahead and enable them**.
3. Click **LZT Market Monitor** in the left sidebar.
4. Click **Run workflow → Run workflow** (green button).

The first run captures current listings as a baseline (no alerts). From then on,
any truly new listing triggers a Discord message within ~5 minutes.

### 5. Verify it's working

- **Actions tab**: each run shows green ✓ or red ✗. Click a run to see logs.
- **Commits**: you'll see "Update seen_items.json [skip ci]" commits from
  github-actions[bot] whenever state changes.
- **Discord**: sit tight — this search doesn't get multiple new listings every
  5 minutes. If after 24h you've seen zero alerts, something's wrong; check
  the Actions logs.

### Creating a Discord webhook

In case you don't have one yet:

1. Discord → the server you want alerts in → **Server Settings → Integrations → Webhooks**.
2. **New Webhook**, name it, pick the channel, **Copy Webhook URL**.

### Changing the search later

Option 1: Edit the `SEARCH_URL` constant in `lzt_monitor.py` and commit.
Option 2: Set a `LZT_SEARCH_URL` secret — overrides the constant.

After changing filters you probably want to re-baseline so you don't get
alerted on the first batch of results that match the new filters:

1. Go to the repo, click `seen_items.json`, click the pencil (edit) icon.
2. Replace the contents with `{"seen": [], "initialized": false}`.
3. Commit.

The next run treats whatever's there as the new baseline.

---

## Option B — Local / VPS

### 1. Install Python + deps

Python 3.10+. Then:

```bash
pip install -r requirements.txt
```

### 2. Configure

Either edit the three constants at the top of `lzt_monitor.py`:

```python
LZT_API_TOKEN       = "..."
DISCORD_WEBHOOK_URL = "..."
SEARCH_URL          = "..."
```

Or use environment variables (they win over the constants):

```bash
export LZT_API_TOKEN="..."
export DISCORD_WEBHOOK_URL="..."
export LZT_SEARCH_URL="..."   # optional
```

### 3. Run

```bash
python3 lzt_monitor.py
```

Runs forever, polling every 2 minutes. `Ctrl+C` to stop.

To run in the background on Linux:

```bash
nohup python3 lzt_monitor.py > /dev/null 2>&1 &
```

For proper 24/7 use a systemd unit or a `screen`/`tmux` session.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `401 Unauthorized` | Token is wrong or expired. Regenerate. |
| `403 Forbidden` | Token is missing the `market` scope. |
| Actions workflow failing on "Commit" step | Under **Settings → Actions → General → Workflow permissions**, select **Read and write permissions**. |
| Discord webhook errors | Test your webhook URL with a `curl -X POST` containing a sample payload. |
| Want to re-baseline | Replace `seen_items.json` contents with `{"seen": [], "initialized": false}`. |

## Files

- `lzt_monitor.py` — the monitor script
- `requirements.txt` — one dependency (`requests`)
- `.github/workflows/monitor.yml` — GitHub Actions schedule
- `seen_items.json` — state file (auto-created; don't edit unless re-baselining)
- `monitor.log` — local-run append-only log (not relevant on GitHub Actions)

## Rate-limit notes

The LZT search endpoint allows 10 requests/minute. 5-min or 2-min intervals
are both well under that. Multiple instances using the same token share one
bucket — don't run too many in parallel.
