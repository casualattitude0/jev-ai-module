"""Function-calling surface: the tool an agent calls to route and serve an input."""
from . import registry
from .dispatch import serve
from .router import guard_tool_call, review_completion, select_model

SELECT_MODEL_SCHEMA = {
    "name": "select_model",
    "description": (
        "Choose which model AND effort level should handle a task. Jev first "
        "judges how hard the task is and what kind of work it is; that sets a "
        "capability floor, and only models clearing it are considered, so cost "
        "never trades against difficulty. Each (model, effort) pair is one "
        "option. Returns the chosen variant with the floor applied and why each "
        "other model was excluded. Does not run the task."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "What needs doing, in one or two sentences.",
            },
            "stakes": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Cost of getting this wrong. Default medium.",
            },
            "priorities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered, e.g. ['cost','latency','quality'].",
            },
            "constraints": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Hard limits, e.g. ['data must stay in-region'].",
            },
            "allow": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Restrict routing to these variant ids ('claude-opus-5@high') "
                    "or bare model ids, which keeps all of that model's efforts."
                ),
            },
            "efforts": {
                "type": "array",
                "items": {"type": "string",
                          "enum": ["low", "medium", "high", "xhigh", "max"]},
                "description": "Restrict to these effort levels.",
            },
            "min_context_tokens": {
                "type": "integer",
                "description": (
                    "Drop models whose context window is known to be smaller than "
                    "this. Models with an unspecified window are kept."
                ),
            },
            "input_tokens": {
                "type": "integer",
                "description": (
                    "Size of the real input. Implies a context requirement and, "
                    "past the long-context threshold, gates on measured recall "
                    "rather than advertised window size."
                ),
            },
            "kind": {
                "type": "string",
                "enum": ["code", "browser", "research", "writing", "general"],
                "description": (
                    "Skip the assessment call by naming the kind of work. Give "
                    "difficulty too, or the assessment still runs."
                ),
            },
            "difficulty": {
                "type": "number",
                "description": (
                    "Skip the assessment call by scoring difficulty 0-3: "
                    "0 trivial, 1 routine, 2 hard, 3 frontier."
                ),
            },
            "assess_first": {
                "type": "boolean",
                "description": (
                    "Default true. False routes in a single call with no "
                    "capability floor, which is cheaper but lets cost compete "
                    "with difficulty."
                ),
            },
        },
        "required": ["task"],
    },
}

ROUTE_AND_SERVE_SCHEMA = {
    "name": "route_and_serve",
    "description": (
        "Choose a model and effort level for the input with the Jev decision API, "
        "then actually call that variant and return its answer. Use when the caller "
        "wants the work done, not just the routing decision."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "The prompt to serve."},
            "task": {
                "type": "string",
                "description": "Short description used for routing. Defaults to the input.",
            },
            "stakes": {"type": "string", "enum": ["low", "medium", "high"]},
            "system": {"type": "string", "description": "Optional system prompt."},
            "max_tokens": {"type": "integer"},
            "min_context_tokens": {"type": "integer"},
            "input_tokens": {"type": "integer"},
            "kind": {"type": "string",
                     "enum": ["code", "browser", "research", "writing", "general"]},
            "transport": {
                "type": "string", "enum": ["cli"],
                "description": (
                    "How to reach the model. Only the local agent CLI is "
                    "available; no API key reaches a model from here."
                ),
            },
        },
        "required": ["input"],
    },
}

TOOLS = [SELECT_MODEL_SCHEMA, ROUTE_AND_SERVE_SCHEMA]


def as_openai_tools():
    """The same schemas in OpenAI's function-calling shape."""
    return [
        {"type": "function", "function": {
            "name": t["name"],
            "description": t["description"],
            "parameters": t["input_schema"],
        }}
        for t in TOOLS
    ]


def as_anthropic_tools():
    """The same schemas in Anthropic's tool shape (already native)."""
    return TOOLS


def _select_model(task, stakes="medium", priorities=None, constraints=None,
                  allow=None, efforts=None, min_context_tokens=None,
                  input_tokens=None, kind=None, difficulty=None,
                  assess_first=True):
    return select_model(
        task, stakes=stakes, priorities=priorities, constraints=constraints,
        allow=allow, efforts=efforts, min_context_tokens=min_context_tokens,
        input_tokens=input_tokens, kind=kind, difficulty=difficulty,
        assess_first=assess_first,
    ).as_dict()


def _route_and_serve(input, task=None, stakes="medium", system=None,
                     max_tokens=2048, min_context_tokens=None, transport=None,
                     input_tokens=None, kind=None):
    sel = select_model(task or input[:500], stakes=stakes,
                       min_context_tokens=min_context_tokens,
                       input_tokens=input_tokens, kind=kind)
    reply = serve(sel, input, system=system, max_tokens=max_tokens,
                  transport=transport)
    return {"routing": sel.as_dict(), "reply": reply}


HANDLERS = {
    "select_model": _select_model,
    "route_and_serve": _route_and_serve,
}


def call(name, arguments):
    """Dispatch one tool call from an agent. Returns a JSON-serialisable result."""
    fn = HANDLERS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name!r}", "available": list(HANDLERS)}
    try:
        return fn(**arguments)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
