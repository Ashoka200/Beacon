# Deploying Beacon to the public

This takes Beacon from your laptop to an always-on public HTTPS URL.
Two parts: **GitHub** (stores the code) and **Render** (runs it 24/7).

> ⚠️ Your `.env` is git-ignored and will NOT be uploaded. Your API key is set
> directly in Render's dashboard instead. Never commit secrets.

---

## Part 1 — Put the code on GitHub

1. Create a free account at https://github.com (skip if you have one).
2. Create a **new private repository** named `beacon` (do NOT initialize with a README).
3. Copy the repo URL GitHub shows you, e.g. `https://github.com/<you>/beacon.git`.
4. Back here, run (the local repo + first commit are already prepared):

   ```sh
   git remote add origin https://github.com/<you>/beacon.git
   git branch -M main
   git push -u origin main
   ```

   GitHub will ask you to authenticate in the browser the first time.

---

## Part 2 — Deploy on Render

1. Create a free account at https://render.com and click **Sign in with GitHub**
   (so Render can see your repo).
2. Click **New +  →  Blueprint**.
3. Select your `beacon` repository. Render detects `render.yaml` automatically.
4. It will ask for the two secret values (because they're marked `sync: false`):
   - **ANTHROPIC_API_KEY** — paste your Claude key (from `.env`).
   - **BEACON_ACCESS_CODE** — invent a code clients will type to enter
     (e.g. `henderson-2026`). This is what locks the door.
5. Click **Apply**. Render builds the Docker image and deploys (~3–5 min).
6. When it finishes, Render gives you a URL like
   `https://beacon-xxxx.onrender.com` — **that's your live public link.**

Visitors hit that URL, see the access-code prompt, type the code, and use Beacon.

---

## Part 3 — Your own domain (optional, later)

1. Buy a domain (e.g. at https://cloudflare.com or https://namecheap.com, ~$12/yr).
2. In Render: your service → **Settings → Custom Domains → Add** `app.yourbrand.com`.
3. Render shows a DNS record (CNAME). Add it at your domain registrar.
4. HTTPS is issued automatically within minutes.

---

## Costs

| Item | Cost |
|---|---|
| GitHub private repo | Free |
| Render `starter` plan (always-on) | ~$7/mo |
| Render `free` plan (sleeps after inactivity, wakes on visit) | Free |
| Domain | ~$12/yr |
| Claude Opus 4.8 usage | Pay-per-use — **set a spend limit in the Anthropic Console** |

> Set a monthly spend cap at https://platform.claude.com (Billing → Limits) so a
> traffic spike can never produce a surprise bill.

---

## Updating Beacon later

Any change you make → commit and push, and Render auto-redeploys:

```sh
git add -A
git commit -m "describe your change"
git push
```
