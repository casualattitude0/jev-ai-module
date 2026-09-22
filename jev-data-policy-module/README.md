# jev-data-policy-module

Decides who may receive data of a given sensitivity. Deterministic, fails
closed, and knows nothing about models, routing or any particular vendor.

Stdlib only. No dependencies.

## Why it is separate

Sensitivity is not a preference for something else to weigh. A probabilistic
answer is the wrong tool for where regulated data may go, so this never becomes
a signal inside another system's scoring — it removes options before that system
sees them.

It also keeps the consumer's core goal intact. A model router should decide
which model suits a task; folding clearance into it made both harder to read.

## Use

```python
from jevpolicy import approved_targets, assert_service, guarded_call
```

Narrow someone else's allow-list:

```python
allow = approved_targets("internal")      # -> ids cleared for that class
```

Or let this module do the whole guarded call. It takes the consumer as an
argument, so neither module imports the other:

```python
from jevagentrouter import select_model

sel = guarded_call(
    select_model,
    data_class="internal",
    service="jevai.org",       # this call sends task text there
    task="reconcile a player inventory bug",
    stakes="medium",
)
```

`guarded_call` refuses before running the consumer, so a policy failure never
reaches the network.

Inspect from the shell:

```bash
python3 -m jevpolicy                             # the whole policy
python3 -m jevpolicy --class internal            # who is cleared
python3 -m jevpolicy --check jevai.org internal  # one decision
```

## Two rules that shape everything

**Approval is yours, not the vendor's.** Everything in `policy.json` ships
approved for `public` only, with `approved_by: null`. Raising
`approved_classes` is an operator decision belonging to your own legal and
security review; this repo cannot know your agreements with a provider. A
target or service absent from the policy entirely is treated as public-only, so
a new entry cannot silently inherit clearance. A test asserts that nothing
ships pre-approved above `public`, and another asserts anything above `public`
names who approved it.

**Coordination is disclosure.** A service that merely decides *where* work
should go still receives whatever text you hand it. `services` records those,
with what each one sees. At or above `external_class_floor` the caller must
pass redacted text and `redacted=True` — a claim the caller makes, recorded
deliberately rather than defaulted.

    sel = guarded_call(select_model, data_class="regulated", redacted=True,
                       service="jevai.org",
                       task="reconcile a payment mismatch")   # redacted summary
    serve(sel, actual_payload)                                # full payload, local

## The contract

`policy.json`:

| field | meaning |
|---|---|
| `classes` | ordered least to most sensitive |
| `external_class_floor` | at or above this, outbound text must be redacted |
| `targets[]` | things that may *receive* a payload |
| `services[]` | external services that *see* text in passing, with `receives` |
| `approved_classes` | highest class cleared; clearance is inclusive downwards |
| `approved_by`, `approved_on` | who signed it off, and when |

Point `JEV_DATA_POLICY` at another file to use a different policy.

## Verify

    python3 -m jevpolicy.verify

22 offline checks. No network calls, by design — so there is no live layer to be
flaky, and this suite is green or the module is broken.

What those checks actually produce is in [result.md](result.md).
