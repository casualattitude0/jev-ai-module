# jev-data-policy-module

Judges one thing: how sensitive is this content — does it carry personal data
or secrets? It answers with a class, and stops there.

Stdlib only. No dependencies.

## What it does not do

It does not know who may receive anything. There are no vendors, no models, no
targets, no allow-lists and no approval register in here. Deciding that
`confidential` may go to one place and not another is a decision about your
agreements and your risk, and it belongs where those live — not inside the
thing that reads the text.

That separation is the whole point of the module. One job: look at content,
name its class. What happens next is the caller's.

## Use

```python
from jev_data_policy import classify

c = classify("user wang.mei@example.com, phone 0912-345-678")
c.data_class            # 'confidential'
c.confidence            # 0.98
c.probabilities         # {'confidential': 0.98, 'internal': 0.02, ...}
c.at_least("internal")  # True
```

From the shell:

```bash
python3 -m jev_data_policy "客戶 A123456789 的帳單地址是..."
python3 -m jev_data_policy --json "..."
python3 -m jev_data_policy --classes        # the ladder; makes no network call
cat suspect.log | python3 -m jev_data_policy
```

```
backend:    openrouter  (from jev.json modules.jev_data_policy.backend)
content:    102 bytes
class:      confidential
confidence: 1.00
            confidential=1.00  internal=0.00  public=0.00  regulated=0.00
```

## The ladder

Four classes, ordered least to most sensitive. Each is defined by what the
content **contains**, never by who may see it:

| class | what is in it |
|---|---|
| `public` | nothing identifying anyone, nothing that grants access |
| `internal` | no personal data, no credentials, but not for outsiders |
| `confidential` | identifies a specific person, or grants access to a system |
| `regulated` | personal data a statute protects, or a credential that moves money or reaches production |

They live in [`classes.json`](jev_data_policy/classes.json), and each
description is handed to Jev verbatim as that class's criteria — **editing a
description is editing the decision**. Point `JEV_DATA_POLICY` (or
`DATA_POLICY_DATA_POLICY`) at another file to use a different ladder. A test
asserts no description ever starts talking about approvals, vendors or
recipients; that is how this module stays one job wide.

## Being probabilistic, it fails closed upwards

The judgement is Jev's: one typed `choice` decision, returning a class and its
probabilities. Jev returns typed decisions, not prose, so there is no honest
way to make it enumerate *which* span of text gave the answer away. A class is
what it can say, so a class is what this returns.

A probability is the wrong shape for "is there personal data in here", so an
unsure answer is never rounded down:

```python
c = classify(text, min_confidence=0.9)
c.data_class       # 'confidential'
c.escalated_from   # 'public' — what Jev actually said, kept on the record
```

Below `min_confidence`, the answer is raised to the most sensitive class still
carrying probability, and what Jev said is preserved rather than overwritten.
Escalation only ever raises. The default is `0.0`: Jev, reported verbatim.

Everything else fails the same direction. An outage raises instead of
answering `public`. A class outside the ladder is refused rather than passed
on. Content over 16 KiB is refused rather than trimmed, because classifying
the first half of something is how a regulated payload comes back clean.

## Classifying content discloses it

This module sends the text to Jev. That is the honest cost of asking, and it
cannot be designed away: if the content itself must not leave the machine,
pass a description of it rather than the thing.

```python
classify("a support ticket containing a customer's ID number and address")
```

The OpenRouter key is checked against the model slug before every call, so it
reaches a Jev decisions model and nothing else — pointing it at
`openai/*` or `anthropic/*` is refused.

## Configuration

Same resolver as the other modules: `DATA_POLICY_<NAME>` beats `JEV_<NAME>`
beats `jev.json`'s `modules.jev_data_policy` beats its top level beats the
built-in default.

| setting | shared | scoped | `jev.json` |
|---|---|---|---|
| which backend answers | `JEV_BACKEND` | `DATA_POLICY_BACKEND` | `backend` |
| the class ladder | `JEV_DATA_POLICY` | `DATA_POLICY_DATA_POLICY` | `data_policy` |
| Jev endpoint | `JEV_BASE_URL` | `DATA_POLICY_BASE_URL` | `base_url` |

Keys only ever come from `.env`; a key written into `jev.json` is refused,
because that file is checked in.

## Verify

```bash
python3 -m jev_data_policy.verify          # 32 offline checks
python3 -m jev_data_policy.verify --live   # + 6 real classifications
```

The offline layer stubs the transport and makes no network call, so it is
green or the module is broken. What both layers actually produce is in
[result.md](result.md).
