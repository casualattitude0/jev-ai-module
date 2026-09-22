# Jev model router

Picks which model **and effort level** should handle an input, by complexity and
stakes, using the [Jev decision API](https://www.jevai.org/docs) — then optionally
calls it and returns the answer.

A routing option is one **(model, effort) pair**, called a variant, with an id like
`claude-opus-5@high`. Effort is chosen at the same time as the model rather than
bolted on afterwards, because "Sonnet thinking hard" and "Opus answering fast" are
genuinely different options with different cost and latency.

## Difficulty decides, cost only breaks ties

Routing runs in two stages:

    input -> Jev judges difficulty and kind -> capability floor (local) -> Jev picks
                                                 cost takes no part        cost decides

Stage one asks Jev how hard the task is (0-3) and what kind of work it is. That
answer sets a **capability floor**, applied locally in `registry.qualified()`.
Only models clearing the floor reach stage two, so cost can never trade against
difficulty — it is a tiebreaker among candidates already able to do the job, not
a competing objective. `default_priorities` ranks `cost` last for the same reason.

    $ python3 -m jevagentrouter "refactor authentication across 40 files; requirements are ambiguous"
    difficulty: 3  (code, ambiguous 0.93)
    floor:      tier>=deep, swe_bench_pro>=70, effort>=high
      excluded  gpt-5.6-sol: swe_bench_pro 64.6 below 70
      excluded  claude-sonnet-5: tier balanced below deep
    model:      Claude 5 Opus  (claude-opus-5)
    effort:     high

The floor makes the researched benchmarks load-bearing rather than decorative:
Sol is cut by a number it published itself.

Pass `kind` and `difficulty` yourself to skip stage one, or `assess_first=False`
to route in one call with no floor at all. When exactly one variant clears the
floor it is returned directly, with no second call.

### Two rules about missing data

No code benchmark spans both model families — OpenAI and Anthropic publish
different ones, and Terminal-Bench versions differ. So a gated metric a model
does not publish is treated as **unknown, not failure**, the same way an
unspecified context window is.

The exception is `require_published`, used for a kind with a specialist
benchmark. For browser work, a model that never reported BrowseComp is not a
browser model, so silence really is evidence of absence. This is why Opus 5 is
excluded from browser tasks despite its tier.

Context window is a routing signal and `min_context_tokens` a hard filter, but
window size is not usable context: past the long-context threshold the floor
gates on measured recall instead. Luna advertises 1.05M and scores 41.3 on MRCR
against Terra's 89.6, so it is cut from large inputs. Pass `input_tokens` and
both the window requirement and the recall gate are derived for you.

Stdlib only. No dependencies.

Self-contained: everything lives under `jevagentrouter/`, including `models.json`.
Copy that one folder into any project and `import jevagentrouter` works.

## Setup

    cp .env.example .env    # at the repo root, then add JEV_API_KEY

`.env` is looked up by walking up from the caller's working directory, and only
then beside the package — so a host project's own `.env` wins over anything
shipped here, and this module carries none of its own.

Provider keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) are only needed to actually
serve an input, not to route one.

## Use

    python3 -m jevagentrouter "refactor the billing retry logic across 40 files"
    python3 -m jevagentrouter --stakes low --priorities cost,latency "rename a var"
    python3 -m jevagentrouter --allow claude-opus-5@high,claude-sonnet-5 "..."
    python3 -m jevagentrouter --efforts low,medium "..."
    python3 -m jevagentrouter --min-context 400000 "summarise this repo"
    python3 -m jevagentrouter --input-tokens 600000 "summarise this dump"
    python3 -m jevagentrouter --kind code --difficulty 3 "..."   # skip stage one
    python3 -m jevagentrouter --no-assess "..."                  # one call, no floor
    python3 -m jevagentrouter --serve "explain this stack trace: ..."
    python3 -m jevagentrouter --list
    python3 -m jevagentrouter --json "..."        # machine-readable

`--allow` takes variant ids (`claude-opus-5@high`) or bare model ids, which keep
all of that model's efforts.

As a library:

    from jevagentrouter import select_model, serve
    sel = select_model("port the parser to async", stakes="high")
    print(sel.variant_id, sel.effort, sel.confidence)
    print(sel.assessment.difficulty, sel.assessment.kind)
    print(sel.floor, sel.excluded)      # the audit trail for the decision
    print(serve(sel, "port the parser to async, here is the file: ..."))

`serve()` uses the effort Jev chose, so the selected level is the level actually
sent to the model.

## Function calling

`jevagentrouter.tools` exposes two tools an agent can call:

- `select_model` — route only, returns the chosen id with the probability spread
- `route_and_serve` — route, then call the chosen model and return its reply

Wire them up in either shape:

    from jevagentrouter import as_anthropic_tools, as_openai_tools, call

    tools = as_anthropic_tools()          # or as_openai_tools()
    result = call("select_model", {"task": "...", "stakes": "high"})

`call()` catches its own exceptions and returns `{"error": ...}`, so a tool-use
loop never dies on a transport failure.

## Transports

Calling a model goes through one of two transports:

| transport | how | status |
|---|---|---|
| `cli` (default) | shells out to an agent CLI — `claude -p --model X --effort Y` | in use today |
| `api` | HTTPS with an `api_key` — no CLI needed | implemented, ready for later |

Pick with `serve(..., transport="api", api_key=...)`, the `JEV_DISPATCH` env var,
or a `"transport"` field on a model. Precedence: argument > env > model > `cli`.

The `api` transport maps effort to each provider's own knob — Anthropic
`thinking.budget_tokens`, OpenAI `reasoning_effort` (`xhigh`/`max` fold to `high`).

## The contract

`models.json` is the registry. Each entry:

| field | meaning |
|---|---|
| `id` | the id sent to the provider when dispatching |
| `provider` | `anthropic` or `openai` — picks the adapter in `dispatch.py` |
| `tier` | `fast` \| `balanced` \| `deep` |
| `efforts` | which effort levels this model offers — one option each |
| `base_cost`, `base_latency` | `low` \| `medium` \| `high`, before the effort shift |
| `context_tokens` | routing signal and `min_context_tokens` filter; `null` = unspecified, never filtered out |
| `description` | what Jev reads when choosing |
| `jev_role` | what this model is for in a Jev-routed system |
| `avoid_when` | folded into the description Jev sees |
| `benchmarks` | measured scores; the capability floor gates on these |
| `enabled` | `false` takes it out of routing without deleting it |
| `verified` | `false` means the id has not been confirmed against the provider |

`thresholds` holds the capability floors, keyed by kind then difficulty level
(`min_tier`, `min_effort`, `require`, `require_published`). Difficulty rounds to
the nearest level, so 2.49 stays at level 2 and only 2.5 reaches level 3.

A variant's cost and latency are the base values shifted by the effort's `step`
(from the `efforts` table), clamped to low/medium/high. Routing needs at least two
variants; `registry.load()` raises if not.

## Layout

    jevagentrouter/
      models.json        the registry / contract
      __main__.py        CLI  (python -m jevagentrouter)
      client.py          Jev transport, 429 backoff, 32 KiB body cap
      registry.py        load + validate models.json, expand effort variants
      router.py          select_model(), guard_tool_call(), review_completion()
      dispatch.py        cli + api transports — actually call the chosen variant
      tools.py           function-calling schemas + executor
      verify.py          test suite  (python -m jevagentrouter.verify)

Point `JEV_MODELS` at another file to use a different registry.

## Verification

    python3 -m jevagentrouter.verify              # offline, no API calls
    python3 -m jevagentrouter.verify --models     # every model+effort, live CLI call each
    python3 -m jevagentrouter.verify --live       # live Jev routing checks
    python3 -m jevagentrouter.verify --all        # everything

`JEV_TEST_GAP` sets the pause between live Jev calls (default 45s).

## Notes

The Jev API rate-limits bursts and sends no `Retry-After`. `client.post()` retries
429 three times with exponential backoff; sustained bursts still fail.
