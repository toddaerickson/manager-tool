"""Sanity tests for the sidebar nav dispatch table.

We can't import web_app at test time (it runs main() at module level which
requires a Streamlit runtime), so we parse the source via AST. Lightweight
regression for the bug class where renaming or deleting a page handler
leaves a stale _DISPATCH entry that crashes Streamlit on first navigation
rather than at startup."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_APP = ROOT / "web_app.py"


def _module_ast() -> ast.Module:
    return ast.parse(WEB_APP.read_text(), filename=str(WEB_APP))


def _dispatch_value_names(module: ast.Module) -> dict[str, str]:
    """Return {dispatch_key: handler_function_name} for the top-level
    `_DISPATCH = {...}` assignment. Raises if not found."""
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_DISPATCH":
                    if not isinstance(node.value, ast.Dict):
                        raise AssertionError("_DISPATCH must be a dict literal")
                    out = {}
                    for k, v in zip(node.value.keys, node.value.values):
                        if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                            raise AssertionError(f"_DISPATCH key must be a string literal, got {ast.dump(k)}")
                        if not isinstance(v, ast.Name):
                            raise AssertionError(f"_DISPATCH value for {k.value!r} must be a bare name, got {ast.dump(v)}")
                        out[k.value] = v.id
                    return out
    raise AssertionError("_DISPATCH assignment not found in web_app.py")


def _top_level_defs(module: ast.Module) -> set[str]:
    """Names of all top-level `def` and `async def` in the module."""
    return {
        n.name
        for n in module.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_dispatch_values_all_resolve_to_defined_functions():
    """Every _DISPATCH value must be a function defined in web_app.py.
    Catches the 'alias points at a deleted handler' regression — that's
    silent on import and only crashes when the user clicks the affected
    nav button."""
    module = _module_ast()
    dispatch = _dispatch_value_names(module)
    defs = _top_level_defs(module)
    missing = {k: v for k, v in dispatch.items() if v not in defs}
    assert not missing, f"_DISPATCH entries pointing at undefined functions: {missing}"


def test_dispatch_legacy_aliases_redirect_timeline_to_team():
    """Timeline and Member Timeline used to be standalone pages. They are
    now folded into Team detail; both keys must redirect to page_team_roster
    so any cached nav_page strings in active user sessions resolve to the
    new home rather than 404 / silently fall through to Dashboard."""
    dispatch = _dispatch_value_names(_module_ast())
    assert dispatch.get("Timeline") == "page_team_roster", \
        "legacy 'Timeline' alias must redirect to page_team_roster"
    assert dispatch.get("Member Timeline") == "page_team_roster", \
        "legacy 'Member Timeline' alias must redirect to page_team_roster"


def test_dispatch_has_canonical_keys():
    """The post-restructure canonical nav keys must all be present.
    Spot check; not exhaustive."""
    dispatch = _dispatch_value_names(_module_ast())
    canonical = {
        "Dashboard", "Upcoming", "Journal", "Schedule", "Actions",
        "Decisions", "1:1 Notes", "Delegations", "Feedback", "Goals",
        "Career Dev", "Analytics", "History", "Resources", "Team",
        "Settings",
    }
    missing = canonical - dispatch.keys()
    assert not missing, f"canonical nav keys missing from _DISPATCH: {missing}"
