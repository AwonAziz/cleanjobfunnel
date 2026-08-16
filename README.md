# Job Funnel

A self-updating dashboard of open AI / MLOps / AIOps roles, pulled straight
from the same public APIs that power company career pages — no scraping, no
middleman job board.

**How it works:** a GitHub Actions workflow runs every ~20 minutes, hits each
source, filters to your target roles and locations, dedupes against the last
run, and commits the result to `docs/data/jobs.json`. GitHub Pages serves
`docs/index.html`, which reads that file and renders it. Close the tab, come
back tomorrow — the data's already there waiting.

## Sources

**Tier 1 — direct from the company.** Greenhouse, Lever, Ashby,
SmartRecruiters. Freshest signal, scoped to the companies listed in
`config/companies.json`.

**Tier 2 — aggregators.** RemoteOK, We Work Remotely, Remotive. Wider net —
catches roles at companies you haven't added by hand.

**Not covered: LinkedIn, Indeed, Wellfound, Turing, Arc.dev.** None of these
publish a public API for reading job search results, and LinkedIn's terms
explicitly prohibit automated collection — so this funnel doesn't touch
them. Keep checking those manually; everything here just cuts down how often
you need to.

## Setup

1. **Create a new GitHub repo** (public — private repos get a limited free
   Actions minutes budget each month, public repos don't) and push these
   files to it.
2. **Settings → Pages** → Source: `Deploy from a branch` → Branch: `main`,
   folder: `/docs` → Save. The dashboard goes live at
   `https://<your-username>.github.io/<repo-name>/` within a minute or two.
3. **Settings → Actions → General → Workflow permissions** → set to
   *Read and write permissions*. The workflow needs this to commit refreshed
   data back into the repo.
4. **Actions tab → Job Funnel Scan → Run workflow** to trigger the first scan
   immediately instead of waiting on the schedule. Reload the Pages URL once
   it finishes (30–60 seconds).

From there it runs unattended.

## Finding a company's token

`config/companies.json` ships with two verified entries (Grafana Labs, Arize
AI) and several educated guesses for the rest of your target list. A wrong
guess just fails quietly and shows up in the Actions log — nothing breaks.
To confirm or fix one:

1. Open the company's careers page and click into any single job listing.
2. Check the URL pattern:
   - `boards.greenhouse.io/<token>/…` or `job-boards.greenhouse.io/<token>/…` → `"ats": "greenhouse"`
   - `jobs.lever.co/<token>/…` → `"ats": "lever"`
   - `jobs.ashbyhq.com/<token>/…` → `"ats": "ashby"`
   - `jobs.smartrecruiters.com/<token>/…` → `"ats": "smartrecruiters"`
3. Doesn't match any of these (custom site, Workday, etc.)? That company
   can't go in Tier 1 — Tier 2 or a manual check is the fallback.
4. Edit the entry in `config/companies.json`, commit, push. Picked up on the
   next scan.

New company: copy an existing block, fill in `name`, `ats`, `token`, and
`group` (group is just your own label for later filtering).

## Tuning what counts as a match

All in `config/companies.json`:

- **`role_keywords`** — case-insensitive substring match against job titles.
  Add variants you want caught, e.g. `"LLMOps"`, `"ML Infra"`.
- **`location_allow`** — a job is kept if its location text contains any of
  these, *or if the location is missing/ambiguous.* The funnel errs toward
  showing you too much rather than silently dropping a real match.
- **`seniority_flags`** — titles matching these get tagged `is_senior`.
  They're not hidden by default; toggle "Hide senior / staff / lead" on the
  dashboard itself, and that choice is remembered on your device.

## Changing the schedule

`.github/workflows/scan.yml` → the `cron` line (`*/20 * * * *` = every 20
minutes). GitHub doesn't guarantee exact timing on scheduled workflows — under
load a run can slip by a few minutes — so treat it as "within the hour," not
a stopwatch. A manual run from the Actions tab always fires immediately.

## Running it locally

```bash
pip install -r requirements.txt
python scripts/fetch_jobs.py
```

Writes straight into `docs/data/`. To view the result, serve `docs/` rather
than opening `index.html` directly — `file://` URLs block the page's
`fetch()` call:

```bash
cd docs && python -m http.server 8000
# then open http://localhost:8000
```

## Tests

No live network calls — each parser is checked against a sample payload
shaped like that API's real, documented schema.

```bash
python tests/test_parsers.py       # every source parser, in isolation
python tests/test_integration.py   # dedupe, first_seen persistence,
                                    # and the all-sources-failed safety net
```

## Reading the dashboard

- **Pulse dot** — green: data is fresh. Amber: last scan was over 90 minutes
  ago, worth a look at the Actions tab. Red: no scan has ever completed yet.
- **Card's left border** — green: first seen in the last 6 hours. Cyan: last
  24 hours. No color: older. This is the practical stand-in for "under 100
  applicants" — LinkedIn is the only board that exposes that figure, and it
  doesn't expose it outside its own app (see the note in-chat about why this
  funnel doesn't attempt LinkedIn/Indeed automation).
- **Source badge** — colored: Tier 1, one of your named companies. Gray:
  Tier 2, an aggregator catch.

## Known limitations

- A job dropping off the dashboard almost always means it's no longer
  returned by its source's "open postings" endpoint — filled or closed, not
  a bug.
- Greenhouse and Lever parsers were verified against real, live responses.
  Ashby and SmartRecruiters were built from official documentation rather
  than a captured live payload. If one of those returns 0 results for a
  company you know is actively hiring, check that run's Actions log — the
  field names may need a small adjustment, and the raw response is the first
  thing worth looking at.
- This reads the same public job-listing data each ATS's own careers page
  is built from — no login, no bypassed auth, nothing that isn't meant to be
  read this way.
