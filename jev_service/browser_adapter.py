"""Compatibility guards around the unchanged upstream browser executor."""

import json

SELECT_DIAGNOSTIC = """action => {
  const e = window.__jevFast?.nodes.get(action.node);
  if (!e?.isConnected) return {failed_check:'connected'};
  const r = e.getBoundingClientRect(), x = r.x+r.width/2, y = r.y+r.height/2;
  const hit = document.elementFromPoint(x,y);
  const describe = node => node ? {tag:node.tagName, id:node.id,
    classes:String(node.className).slice(0,200), role:node.getAttribute('role')} : null;
  const checks = {
    enabled: !e.matches(':disabled') && !e.closest('[aria-disabled="true"],[inert]'),
    visible: e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}),
    in_viewport: !!r.width && !!r.height && x>=0 && y>=0 && x<innerWidth && y<innerHeight,
    hit_target: e.contains(hit),
    native_select: e.tagName==='SELECT',
    enabled_option: e.tagName==='SELECT' && [...e.options].some(o=>o.value===action.value &&
      !o.disabled && !o.closest('optgroup[disabled]'))
  };
  // Some native selects sit under their own aria-hidden visual decoration.
  // Require one select in the widget and a sibling decoration displaying its
  // current value. An unrelated overlay never qualifies for semantic selection.
  const parent = e.parentElement;
  const decoration = parent && [...parent.children].find(sibling => sibling!==e &&
    sibling.getAttribute('aria-hidden')==='true' && sibling.contains(hit));
  const selected = e.tagName==='SELECT' ? e.selectedOptions[0]?.label.trim() : '';
  const decorated_select = !!(decoration && selected &&
    parent.querySelectorAll('select').length===1 &&
    decoration.innerText.includes(selected) &&
    decoration.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}));
  return {failed_check:Object.keys(checks).find(k=>!checks[k]) || null,
    checks, decorated_select, target:describe(e), hit:describe(hit), option:action.value,
    label:action.label, pointer_events:getComputedStyle(e).pointerEvents,
    opacity:getComputedStyle(e).opacity};
}"""


def filter_select_actions(page, diagnostics):
    """Keep upstream identities and freshness markers; omit unusable actions only."""
    blocked = {item["node"] for item in diagnostics if not selectable(item["diagnostic"])}
    return {**page, "actions": [a for a in page["actions"]
                               if not (a["kind"] == "select" and a["node"] in blocked)]}


def selectable(diagnostic):
    if not diagnostic.get("failed_check"):
        return True
    return (diagnostic["failed_check"] == "hit_target" and diagnostic.get("decorated_select", False)
            and all(value for key, value in diagnostic.get("checks", {}).items() if key != "hit_target"))


SELECT_EXECUTE = """action => {
  const diagnostic = (""" + SELECT_DIAGNOSTIC + """)(action);
  const permitted = !diagnostic.failed_check || (diagnostic.failed_check==='hit_target' &&
    diagnostic.decorated_select && Object.entries(diagnostic.checks).every(([k,v])=>k==='hit_target'||v));
  if (!permitted) return {executed:false, diagnostic};
  const e=window.__jevFast.nodes.get(action.node);
  e.value=action.value;
  e.dispatchEvent(new Event('input',{bubbles:true}));
  e.dispatchEvent(new Event('change',{bubbles:true}));
  return {executed:true, diagnostic};
}"""


def install(emit):
    from jev_ultrafast import agent

    original = agent.Browser

    class CompatibleBrowser(original):
        def observe(self, *args, **kwargs):
            page = super().observe(*args, **kwargs)
            selects = {a["node"]: a for a in page["actions"] if a["kind"] == "select"}
            if selects:
                # Test the actual native control, not one arbitrary option. Options were
                # already checked by the snapshot. All options share its hit target.
                expression = "(actions => actions.map(action => { const diagnostic=(" + SELECT_DIAGNOSTIC
                expression += ")(action); delete diagnostic.checks?.enabled_option;"
                expression += " if(diagnostic.failed_check==='enabled_option') diagnostic.failed_check=null;"
                expression += " return {node:action.node,diagnostic}; }))(" + json.dumps(list(selects.values())) + ")"
                diagnostics = self.evaluate(expression)
                if diagnostics:
                    page = filter_select_actions(page, diagnostics)
                    for item in diagnostics:
                        if item["diagnostic"].get("failed_check"):
                            emit({"event": "progress", "browser_diagnostic": {
                                **item["diagnostic"], "handling":
                                "Native select has its own visible decoration; semantic selection supported."
                                if selectable(item["diagnostic"]) else
                                "Excluded unavailable native select; unrelated overlays are not bypassed."}})
            return page

        def act(self, action, page, text=None):
            if action["kind"] == "select":
                from jev_ultrafast.browser import StalePage

                if not self.fresh(page, action):
                    raise StalePage("Dropdown changed before execution; observe again")
                diagnostic = self.evaluate("(" + SELECT_DIAGNOSTIC + ")(" + json.dumps(action) + ")")
                emit({"event": "progress", "browser_diagnostic": diagnostic})
                if diagnostic and not selectable(diagnostic):
                    # No mutation has happened: a fresh observation is safe here.
                    raise StalePage("Dropdown preflight failed before input: " + diagnostic["failed_check"])
                if diagnostic and diagnostic.get("decorated_select"):
                    # Validate and mutate together. Never replay an ambiguous mutation.
                    try:
                        result = self.evaluate("(" + SELECT_EXECUTE + ")(" + json.dumps(action) + ")")
                    except Exception as exc:
                        raise RuntimeError("Dropdown selection was interrupted after dispatch; "
                                           "not replayed automatically") from exc
                    if not result or "executed" not in result:
                        raise RuntimeError("Dropdown selection acknowledgement missing; not replayed")
                    if not result["executed"]:
                        raise StalePage("Dropdown changed before input: "
                                        + str(result["diagnostic"].get("failed_check")))
                    self.after_input = action
                    return {"executed": action["id"]}
            return super().act(action, page, text=text)

    agent.Browser = CompatibleBrowser
