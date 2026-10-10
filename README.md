# Job Funnel

A self-updating dashboard of open AI / MLOps / AIOps roles, pulled straight
from the same public APIs that power company career pages — no scraping, no
middleman job board.

**How it works:** a GitHub Actions workflow runs every ~20 minutes, hits each
source, filters to your target roles and locations, dedupes against the last
run, and commits the result to `docs/data/jobs.json`. GitHub Pages serves
`docs/index.html`, which reads that file and renders it. Close the tab, come
back tomorrow — the data's already there waiting.

**v2 rewrite:** the scanner is now a small package (`jobfunnel/`) instead of a
single script, with a CLI (`scan` / `validate` / `doctor` / `clean`), word-boundary
keyword matching with synonyms and score explanations, cross-source duplicate
merging, retries + ETag caching, and triple the sources — including Hacker
News' monthly "Who is hiring?" thread. Everything below reflects the new shape.

## Setup

1. **Create a new GitHub repo** (public — private repos get a limited free
   Actions minutes budget each month, public repos don't) and push these
   files to it.
2. **Settings → Pages** → Source: `Deploy from a branch` → Branch: `main`,
   folder: `/docs` → Save. The dashboard goes live at
   `https://<your-username>.github.io/<repo-name>/` within a minute or two.
3. **Settings → Actions → General → Workflow permissions** → set to *Read and
   write permissions*. The workflow needs this to commit refreshed data back
   into the repo.
4. **Actions tab → Job Funnel Scan → Run workflow** to trigger the first scan
   immediately instead of waiting on the schedule. Reload the Pages URL once
   it finishes (30–60 seconds).

From there it runs unattended. CI (`ci.yml`) runs lint, typecheck, config
validation, and the offline test suite on every push.

## Sources

**Tier 1 — direct from the company.** Greenhouse, Lever, Ashby,
SmartRecruiters, Recruitee, Breezy, Personio. Freshest signal, scoped to the
companies listed in `config/companies.json` — 50+ verified boards covering
OpenAI, Anthropic, Scale, Databricks, Cohere, Perplexity, Cursor, Arize,
Grafana, Datadog, and the rest of the AI-native / ML-platform / infra set.
Every entry was verified against its live board on 2026-10-10.

**Tier 2 — aggregators.** RemoteOK, Remotive (JSON API), Himalayas,
Working Nomads, We Work Remotely (RSS), and the latest monthly *Ask HN: Who
is hiring?* thread via the Algolia API. Wider net — catches roles at
companies you haven't added by hand.

**Not covered: LinkedIn, Indeed, Wellfound, Turing, Arc.dev.** None of these
publish a public API for reading job search results, and LinkedIn's terms
explicitly prohibit automated collection — so this funnel doesn't touch
them. **Workday boards** are also excluded: their job-search API sits behind
anti-bot challenges (500s without a browser session), and a flaky source
worse than no source. Keep checking those manually; everything here just
cuts down how often you need to.

## Using the CLI

```bash
pip install -r requirements.txt

python -m jobfunnel scan                 # the full pipeline (default command)
python -m jobfunnel scan --dry-run       # fetch + filter, write nothing
python -m jobfunnel scan --only remoteok # just one source (repeatable; never writes)
python -m jobfunnel scan --min-score 55  # tighter quality bar for this run
python -m jobfunnel validate             # sanity-check the config, offline
python -m jobfunnel doctor               # probe every source, live, with timings
python -m jobfunnel clean                # drop the local HTTP cache
```

## Tuning what counts as a match

All in `config/companies.json`:

- **`role_keywords`** — any-of matching against job titles, on *word
  boundaries with light stemming*. That means `SRE` doesn't match inside
  other words, "Platform Engineer" still matches "Platform Engineering", and
  `AI/ML Engineer` matches literally.
- **`synonyms`** — maps a canonical keyword to alternate spellings
  ("SRE" → "Site Reliability Engineer"). A synonym hit scores the canonical
  term and the dashboard shows *why* something matched, hover the score badge.
- **`exclude_keywords`** — hard drops (internships, unpaid, clearance…).
- **`location_allow`** — a job is kept if its location text contains any of
  these, *or if the location is missing/ambiguous.* The funnel errs toward
  showing you too much rather than silently dropping a real match.
- **`location_exclude`** — checked *after* allow. This is how region-locked
  remote roles die: "Remote (US)" keeps the word "remote" and then trips
  "usa"/"us" here. Short codes (us, uk) match on word boundaries, so
  "focus"/"ukulele" are safe. US states and major US cities are listed
  because ATS boards love "Remote - California" locations.
- **`seniority_flags`** — titles matching these get tagged `is_senior`.
  They're not hidden by default; toggle "Hide senior / staff / lead" on the
  dashboard itself, and that choice is remembered on your device.
- **`min_score`** — 0..100. Matches below this are filtered out.
- **`feeds`** — enable/disable each aggregator. `hn_whoshiring` takes a
  `threads` count (default 1 = current month only).

### Scoring

Every kept job gets a 0–100 score: title match (+30 per keyword, capped at
+50), direct-from-company board (+15), remote-friendly or unspecified
location (+10), and recency (+10 within a week, +5 within a month). The
dashboard sorts by it and shows the individual reasons on hover.

### Duplicate merging

The same role routinely appears on the company's board *and* an aggregator.
Jobs with the same (company, title) fingerprint or the same normalized apply
URL are merged into one card — Tier 1 wins over Tier 2, salary and tags are
enriched from the loser, and the losing source shows up as "also on …".
Typical runs merge 3–20 duplicates.

## Finding a company's token

`config/companies.json` ships with 50+ verified entries. A wrong token just
fails quietly and shows up in the scan log (and `doctor` output) — nothing
breaks. To confirm or add one:

1. Open the company's careers page and click into any single job listing.
2. Check the URL pattern:
   - `boards.greenhouse.io/<token>/…` or `job-boards.greenhouse.io/<token>/…` → `"ats": "greenhouse"`
   - `jobs.lever.co/<token>/…` → `"ats": "lever"`
   - `jobs.ashbyhq.com/<token>/…` → `"ats": "ashby"`
   - `jobs.smartrecruiters.com/<token>/…` → `"ats": "smartrecruiters"`
   - `<token>.recruitee.com` → `"ats": "recruitee"`
   - `<token>.breezy.hr` → `"ats": "breezy"`
   - `<token>.jobs.personio.de` → `"ats": "personio"`
3. Doesn't match any of these (custom site, Workday, etc.)? That company
   can't go in Tier 1 — Tier 2 or a manual check is the fallback.
4. Edit the entry in `config/companies.json`, commit, push. Picked up on
   the next scan.

New company: copy an existing block, fill in `name`, `ats`, `token`, and
`group` (group is just your own label for later filtering).

## Changing the schedule

`.github/workflows/scan.yml` → the `cron` line (`*/20 * * * *` = every 20
minutes). GitHub doesn't guarantee exact timing on scheduled workflows — under
load a run can slip by a few minutes — so treat it as "within the hour," not
a stopwatch. A manual run from the Actions tab always fires immediately.

## Running it locally

```bash
pip install -r requirements.txt
python -m jobfunnel scan
```

Writes straight into `docs/data/`. To view the result, serve `docs/` rather
than opening `index.html` directly — `file://` URLs block the page's
`fetch()` call:

```bash
cd docs && python -m http.server 8000
# then open http://localhost:8000
```

Scans cache HTTP responses (ETag-aware) in `.cache/`, so a second run inside
15 minutes is nearly instant. `--no-cache` forces a fresh fetch.

## Tests

No live network calls — each parser is checked against a trimmed *real*
payload captured from that provider's live API (`tests/fixtures/`), and the
integration suite runs the whole pipeline in a temp directory.

```bash
pip install -r requirements-dev.txt
python -m pytest                       # 87 tests, offline
ruff check . && ruff format --check .  # lint
mypy jobfunnel                         # typecheck
```

## Reading the dashboard

- **Score badge** — hover it for the reasons: which keyword matched, tier,
  recency. Sorted best-first by default.
- **also on …** — the same role was found on another source; they've been
  merged into this card.
- **Salary** — shown when the source publishes one (Remotive, Himalayas,
  RemoteOK, Breezy…).
- **Pulse dot** — green: data is fresh. Amber: last scan was over 90 minutes
  ago, worth a look at the Actions tab. Red: no scan has ever completed yet.
- **Card's left border** — green: first seen in the last 6 hours. Cyan: last
  24 hours. No color: older. This is the practical stand-in for "under 100
  applicants" — LinkedIn is the only board that exposes that figure, and it
  doesn't expose it outside its own app (see the note above about why this
  funnel doesn't attempt LinkedIn/Indeed automation).
- **Source badge** — colored: Tier 1, one of your named companies. Gray:
  Tier 2, an aggregator catch.

## Known limitations

- A job dropping off the dashboard almost always means it's no longer
  returned by its source's "open postings" endpoint — filled or closed, not
  a bug.
- Hacker News posts are freeform; the "Company | Role | Location" parse is
  best-effort (it skips job-seekers' posts and grabs locations from the
  title's parentheses when there's no third pipe field). Expect a little
  noise — the score badge and keyword filter still apply.
- Location filtering is intentionally opinionated: it keeps remote-anywhere
  and Pakistan/Gulf/EMEA/APAC-wide roles and drops region-locked ones. That
  lives entirely in `location_allow`/`location_exclude` — trim it when your
  situation changes.
- Greenhouse and Lever parsers were verified against real, live responses.
  Ashby, SmartRecruiters, Recruitee, Breezy, and Personio were verified the
  same way. Workday was *attempted* and rejected (see above).
- This reads the same public job-listing data each ATS's own careers page
  is built from — no login, no bypassed auth, nothing that isn't meant to be
  read this way.
