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

    $ python3 -m jev_model_router "refactor authentication across 40 files; requirements are ambiguous"
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

## Scope

This module chooses a model and runs it. It does **not** judge whether a task is
safe, whether a tool call should be allowed, or whether an agent's work is
finished — those are somebody else's decisions, and routing them through the
thing that picks models is how a router turns into a policy engine. The one
security property it does own is the one it creates itself: it spawns
subprocesses, so it is responsible for what is in their environment (see
[Serving](#transports--how-the-chosen-model-is-reached)).

Stdlib only. No dependencies.

Self-contained: everything lives under `jev_model_router/`, including `models.json`.
Copy that one folder into any project and `import jev_model_router` works.

## Setup

    cp .env.example .env    # at the repo root, then add JEV_API_KEY

`.env` is looked up by walking up from the caller's working directory, and only
then beside the package — so a host project's own `.env` wins over anything
shipped here, and this module carries none of its own.

A decision needs a key for whichever backend answers it: `JEV_API_KEY` for
native, `OPENROUTER_API_KEY` for openrouter. Serving needs no key at all — it
goes through the chosen model's own CLI.

Every `JEV_*` setting can be answered for this module alone by prefixing the
package name: `MODEL_ROUTER_API_KEY`, `MODEL_ROUTER_BASE_URL`,
`MODEL_ROUTER_BACKEND`. The scoped name wins, the shared `JEV_` name is the
fallback, so one root `.env` can put this module on a different endpoint, key
or backend without moving the others with it.

## Use

    python3 -m jev_model_router "refactor the billing retry logic across 40 files"
    python3 -m jev_model_router --stakes low --priorities cost,latency "rename a var"
    python3 -m jev_model_router --allow claude-opus-5@high,claude-sonnet-5 "..."
    python3 -m jev_model_router --efforts low,medium "..."
    python3 -m jev_model_router --min-context 400000 "summarise this repo"
    python3 -m jev_model_router --input-tokens 600000 "summarise this dump"
    python3 -m jev_model_router --kind code --difficulty 3 "..."   # skip stage one
    python3 -m jev_model_router --no-assess "..."                  # one call, no floor
    python3 -m jev_model_router --serve "explain this stack trace: ..."
    python3 -m jev_model_router --list
    python3 -m jev_model_router --json "..."        # machine-readable

`--allow` takes variant ids (`claude-opus-5@high`) or bare model ids, which keep
all of that model's efforts.

As a library:

    from jev_model_router import select_model, serve
    sel = select_model("port the parser to async", stakes="high")
    print(sel.variant_id, sel.effort, sel.confidence)
    print(sel.assessment.difficulty, sel.assessment.kind)
    print(sel.floor, sel.excluded)      # the audit trail for the decision
    print(serve(sel, "port the parser to async, here is the file: ..."))

`serve()` uses the effort Jev chose, so the selected level is the level actually
sent to the model.

## Function calling

`jev_model_router.tools` exposes two tools an agent can call:

- `select_model` — route only, returns the chosen id with the probability spread
- `route_and_serve` — route, then call the chosen model and return its reply

Wire them up in either shape:

    from jev_model_router import as_anthropic_tools, as_openai_tools, call

    tools = as_anthropic_tools()          # or as_openai_tools()
    result = call("select_model", {"task": "...", "stakes": "high"})

`call()` catches its own exceptions and returns `{"error": ...}`, so a tool-use
loop never dies on a transport failure.

## Backends — where a decision comes from

The same decision is available two ways, and they return the same shape:

| backend | where | key |
|---|---|---|
| `native` (default) | the Jev API at `www.jevai.org` | `JEV_API_KEY` |
| `openrouter` | the Jev model published as `~typesafe/jev-latest` | `OPENROUTER_API_KEY` |

The standing choice lives in the project root's `jev.json`, checked in so the
routing decision is visible and reviewable rather than hidden in `.env`:

    {"backend": "openrouter",
     "modules": {"jev_model_router": {"backend": "openrouter"},
                 "jev_workflow": {"backend": "openrouter"}}}

Override it for one run without touching a committed file:

    JEV_BACKEND=openrouter python3 -m jev_model_router "..."   # both modules
    MODEL_ROUTER_BACKEND=openrouter python3 -m jev_model_router "..."   # this one

Precedence: argument > scoped env > shared env > `jev.json` `modules.<module>`
> `jev.json` top level > `native`. Every run prints which path it took and
where that came from:

    backend:    openrouter  (from jev.json modules.jev_model_router.backend)

A credential in `jev.json` is refused — that file is committed; keys stay in
`.env`. Pin a Jev version with `JEV_OPENROUTER_MODEL` instead of `-latest`.

OpenRouter serves only the generic `{state, questions}` decisions endpoint, not
Jev's named presets, so the one preset this module uses (`model-route`) is
written out here as the question it is made of and the flattened answer is
rebuilt on the way back. A check fails the suite if this module ever calls an
endpoint with no translation, so the backends cannot drift apart silently.

**One difference, by design:** OpenRouter's decision questions are
`choice | score | noul` only — there is no free-text type — so `guidance` comes
back empty on that backend, tagged `guidance_source: "unavailable_on_openrouter"`.
Nothing is invented locally to fill it.

## Transports — how the chosen model is reached

Only one, and it takes no key:

| transport | how |
|---|---|
| `cli` (default, and the only one) | shells out to the model's own agent CLI — `claude -p --model X --effort Y`, `codex exec` |

Claude and GPT are never called with an API key. Three things enforce it, and
each has a check behind it:

- There is no HTTP path to Anthropic, OpenAI or any gateway in front of them.
  `dispatch.py` imports no HTTP client and reads no credential from the
  environment; `serve(..., api_key=...)` is rejected outright.
- Every API key is **stripped from the CLI's environment** before it runs
  (`STRIPPED_KEY_ENV`). An agent CLI will authenticate with `ANTHROPIC_API_KEY`
  or `OPENAI_API_KEY` if it finds one, so a key left in the shell or in `.env`
  would otherwise become a metered API call by a longer route. The CLI uses its
  own logged-in session instead. The Jev key is stripped too — the CLI has no
  use for it, and a credential that never enters a subprocess cannot be logged
  or forwarded by one.
- `OPENROUTER_API_KEY` reaches Jev and nothing else: the model slug is checked
  before every decision, so `JEV_OPENROUTER_MODEL` cannot be pointed at
  `openai/gpt-5.6-sol` or `anthropic/claude-opus-5`. Pinning a Jev version is
  allowed; anything else is refused.

`resolve_transport` and the `JEV_DISPATCH` setting stay, so another transport
can be added deliberately rather than by default.

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

    jev_model_router/
      models.json        the registry / contract
      __main__.py        CLI  (python -m jev_model_router)
      client.py          Jev transport: native + openrouter backends, 429 backoff
      registry.py        load + validate models.json, expand effort variants
      router.py          select_model() — the two-stage routing decision
      dispatch.py        cli transport — call the chosen variant, no keys
      tools.py           function-calling schemas + executor
      verify.py          test suite  (python -m jev_model_router.verify)

Point `JEV_MODELS` at another file to use a different registry.

## Verification

    python3 -m jev_model_router.verify              # offline, no API calls
    python3 -m jev_model_router.verify --models     # every model+effort, live CLI call each
    python3 -m jev_model_router.verify --live       # live Jev routing checks
    python3 -m jev_model_router.verify --all        # everything

`JEV_TEST_GAP` sets the pause between live Jev calls (default 45s).

## Notes

The Jev API rate-limits bursts and sends no `Retry-After`. `client.post()` retries
429 three times with exponential backoff; sustained bursts still fail.
