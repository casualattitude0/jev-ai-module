"""Function-calling surface: the tool an agent calls to pick a workflow."""
from . import registry
from .router import select_workflow

SELECT_WORKFLOW_SCHEMA = {
    "name": "select_workflow",
    "description": (
        "Choose which workflow module should take a request -- discussion, game "
        "code, data design, a style definition, an asset audit, asset generation "
        "or asset integration -- using the Jev decision API. Returns the chosen "
        "workflow's interface (command, entry point, system prompt path, expected "
        "inputs) with confidence and the probability spread. Decides only; the "
        "caller runs the workflow."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "request": {
                "type": "string",
                "description": "What the user is asking for, in their own words.",
            },
            "context": {
                "type": "object",
                "description": (
                    "What is already true. A key naming a workflow reports its "
                    "state, e.g. {'define-art': 'done', 'asset-audit-art': "
                    "'missing'}, and unmet ones come back as blocked_by. Any "
                    "other key is passed through as free context."
                ),
            },
            "stage": {
                "type": "string",
                "description": "Restrict to one stage, e.g. 'generate'.",
            },
            "allow": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Restrict to these workflow ids or commands.",
            },
            "stakes": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Cost of handing this to the wrong workflow.",
            },
            "two_step": {
                "type": "boolean",
                "description": (
                    "Choose the stage of production first, then the workflow "
                    "inside it. Defaults to whichever shape the catalogue size "
                    "calls for."
                ),
            },
        },
        "required": ["request"],
    },
}

LIST_WORKFLOWS_SCHEMA = {
    "name": "list_workflows",
    "description": (
        "List every available workflow module with its stage, what it produces, "
        "and whether anything is implemented behind its interface yet. Makes no "
        "network call."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "stage": {"type": "string", "description": "Filter to one stage."},
        },
    },
}

TOOLS = [SELECT_WORKFLOW_SCHEMA, LIST_WORKFLOWS_SCHEMA]


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


def _select_workflow(request, context=None, stage=None, allow=None,
                     stakes="medium", two_step=None):
    return select_workflow(request, context=context, stage=stage, allow=allow,
                           stakes=stakes, two_step=two_step).as_dict()


def _list_workflows(stage=None):
    reg = registry.load()
    return [registry.interface(m, reg) for m in registry.enabled(reg)
            if not stage or m["stage"] == stage]


HANDLERS = {
    "select_workflow": _select_workflow,
    "list_workflows": _list_workflows,
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
