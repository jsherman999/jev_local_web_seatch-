"""Serve only explicitly listed function definitions, never arbitrary files."""

import ast

from .config import ROOT

UPSTREAM = "vendor/jev-ultrafast/jev_ultrafast/"
SOURCES = {
    "browser_open": (UPSTREAM + "browser.py", "Browser.__init__"),
    "browser_setup": ("jev_service/browser_context.py", "install.IsolatedBrowser.__init__"),
    "browser_read": ("jev_service/browser_adapter.py", "install.CompatibleBrowser.observe"),
    "browser_act": ("jev_service/browser_adapter.py", "install.CompatibleBrowser.act"),
    "jev_choose": (UPSTREAM + "model.py", "choose"),
    "baseline_choose": ("jev_service/baseline.py", "install.choose"),
    "field_text": (UPSTREAM + "model.py", "field_text"),
    "verify_page": ("jev_service/worker.py", "verify_page"),
    "worker_run": ("jev_service/worker.py", "run"),
}


def source_excerpt(key):
    path, symbol = SOURCES[key]
    text = (ROOT / path).read_text()
    node = ast.parse(text)
    for name in symbol.split("."):
        node = next(child for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    and child.name == name)
    return {"file": path, "symbol": symbol, "line": node.lineno,
            "code": "\n".join(text.splitlines()[node.lineno - 1:node.end_lineno])}
