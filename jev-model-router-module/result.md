# Results

Input : output. Last updated 2026-09-22, two-stage routing with capability floors.

## Two-stage routing — task in, floor and variant out

Stage one asks Jev for difficulty and kind; that sets a capability floor applied
locally; only survivors reach stage two. Cost takes no part in the floor.

| input | stage 1 | floor | output |
|---|---|---|---|
| `refactor authentication across 40 files in this repo; requirements are ambiguous` | difficulty 3, code, ambiguous 0.93 | tier>=deep, swe_bench_pro>=70, effort>=high | `claude-opus-5@high` |
| `drive a checkout flow` (kind=browser, difficulty=3) | supplied | tier>=deep, browsecomp>=90, publishes browsecomp, effort>=high | `gpt-5.6-sol@high` — 0.51 |

Exclusions are reported with the decision:

    excluded  gpt-5.6-sol: swe_bench_pro 64.6 below 70        (code, difficulty 3)
    excluded  claude-opus-5: does not publish browsecomp      (browser, difficulty 3)
    excluded  gpt-5.6-luna: mrcr_long_context_recall 41.3 below 85   (600k input)

Sol is cut from hard code work by a score it published itself, and Opus 5 from
browser work by a benchmark it never reported. The researched numbers are
load-bearing, not decoration.

### Floor behaviour

| input | output |
|---|---|
| difficulty 2.49, code | level 2 floor — tier>=balanced, effort>=medium (11 variants) |
| difficulty 2.50, code | level 3 floor — tier>=deep, effort>=high (2 variants) |
| difficulty 3, code | 1 model survives: `claude-opus-5` |
| difficulty 3, browser | 1 model survives: `gpt-5.6-sol` |
| difficulty 0.4, code | no floor bites: all 17 variants |
| any difficulty, 600k input | `gpt-5.6-luna` cut on measured recall, not window size |
| one variant clears the floor | returned directly, **no second Jev call** |
| nothing clears the floor | `ValueError`, not a silent downgrade |

## Single-stage routing (`assess_first=False`)

## Routing — task in, variant out

Against the researched registry (17 variants, real context windows and benchmarks
in each description):

| input | output |
|---|---|
| `refactor authentication across 40 files in this repo; ambiguous requirements` (high) | `claude-opus-5@medium` |
| `read a 600k-token codebase dump and summarise its architecture` (medium, min_context=500,000) | `gpt-5.6-terra@medium` |

Both confirm the rewritten descriptions changed the decision for the better:

- The repo refactor went to Opus 5, not Sol — SWE-bench Pro 79.2 vs 64.6 is now
  stated in the description, and the two cost the same on input.
- The 600k-token task went to Terra, not Luna, even though both advertise a
  1.05M window — Luna's `avoid_when` carries its MRCR recall of 41.3.

Against the earlier registry, before context windows and benchmarks were researched:

| input | output |
|---|---|
| `rename a local variable in one file` (low, cost-first) | `gpt-5.6-luna@low` — 0.75 |
| `redesign the payment reconciliation engine across 40 files with ambiguous requirements; must not lose transactions` (high) | `claude-opus-5@xhigh` — 0.63 |
| `change a log message string in one file` (low) | `gpt-5.6-luna@low` — 0.46 |
| `add pagination to an existing REST endpoint and its tests` (medium) | `gpt-5.6-terra@medium` — 0.73 |
| `add pagination to an existing REST endpoint and its tests` (medium) | `claude-sonnet-5@medium` — 0.73 |
| `redesign the transaction ledger across the service with ambiguous requirements and no downtime` (high) | `gpt-5.6-sol@xhigh` — 0.55 |
| `redesign the transaction ledger across the service with ambiguous requirements and no downtime` (high) | `claude-opus-5@xhigh` — 0.43 |
| `write a regex for ISO dates` (low, efforts=low) | `claude-sonnet-5@low` |
| `summarise a very large document` (min_context=200,001) | `gpt-5.6-terra@medium` |
| `summarise a 3-line changelog` (low) | `gpt-5.6-luna@low` — 0.23 |

Full probability spread for `rename a local variable in one file`:

    0.78  gpt-5.6-luna@low
    0.11  claude-haiku-4-5-20251001@low
    0.06  claude-sonnet-5@low
    0.05  gpt-5.6-terra@low
    0.00  gpt-5.6-terra@high
    0.00  gpt-5.6-terra@medium

## Dispatch — prompt in, reply out

Input to all: `Reply with exactly one word: pong`

| variant | output |
|---|---|
| `gpt-5.6-luna@low` | `pong` |
| `gpt-5.6-luna@medium` | `pong` |
| `gpt-5.6-terra@low` | `pong` |
| `gpt-5.6-terra@medium` | `pong` |
| `gpt-5.6-terra@high` | `pong` |
| `gpt-5.6-sol@medium` | `pong` |
| `gpt-5.6-sol@high` | `pong` |
| `gpt-5.6-sol@xhigh` | `pong` |
| `gpt-5.6-sol@max` | `pong` |
| `claude-opus-5@medium` | `pong` |
| `claude-opus-5@high` | `pong` |
| `claude-opus-5@xhigh` | `pong` |
| `claude-sonnet-5@low` | `pong` |
| `claude-sonnet-5@medium` | `pong` |
| `claude-sonnet-5@high` | `pong` |
| `claude-haiku-4-5-20251001@low` | `pong` |
| `claude-haiku-4-5-20251001@medium` | `pong` |
| `claude-haiku-4-6@low` | **error** — `"claude-haiku-4-6" isn't described by this version's model catalog` |
| `gpt-5.6-sol@none` | `pong` (level exists but no model declares it) |

## Errors — bad input in, error out

| input | output |
|---|---|
| `JEV_API_KEY=jev_bogus` | `HTTP 401: Invalid or missing Jev API key` |
| 12 calls in quick succession | `HTTP 429` — retried with backoff, then surfaced |
| one call during a Jev outage | `HTTP 502` — retried with backoff, then surfaced |
| payload over 32 KiB | rejected locally, no request sent |
| `stakes="critical"` | `ValueError` before any HTTP call |
| `serve(..., transport="api")` with no key | `set ANTHROPIC_API_KEY to dispatch to Anthropic models` |
| `serve(..., transport="cli", api_key=...)` | `api_key is only used by the api transport` |

## Registry corrections from research

| model | was | is |
|---|---|---|
| `claude-opus-5` context | 200,000 | **1,000,000** |
| `claude-sonnet-5` context | 200,000 | **1,000,000** |
| `gpt-5.6-*` context | unspecified | **1,050,000** (but see Luna below) |
| `claude-haiku-4-6` | assumed real | **does not exist** → `claude-haiku-4-5-20251001` |
| `gpt-5.6-sol` efforts | medium, high, xhigh | **+ max** (only tier supporting it) |

Luna advertises the same 1.05M window as its siblings but scores 41.3 on MRCR
long-context recall against Terra's 89.6, so the window is not usable in
practice. That is recorded in its `avoid_when`, not just as a note here.

## Counts

    python3 -m jevagentrouter.verify              47 passed, 0 failed, 0 skipped
    python3 -m jevagentrouter.verify --models     62 passed, 0 failed, 0 skipped
    python3 -m jevagentrouter.verify --live       42 passed, 3 failed

The offline count is from a run on 2026-09-22. The `--models` and `--live` counts
predate registry v3, so they cover 16 variants, not 17, and the pre-research
descriptions. The one substantive `--live` failure was Jev preferring
`gpt-5.6-terra` over Haiku for a fast-tier task; the other two were rate-limit
noise from overlapping runs.

## Not verified

- **The `api` transport.** Implemented and unit-tested, but never given a real
  key, so no HTTPS call has been made to either provider.
- **Registry v3 under `--models` / `--live`.** The offline suite passes at 47/47;
  the two live suites have not been re-run since the research rewrite.
