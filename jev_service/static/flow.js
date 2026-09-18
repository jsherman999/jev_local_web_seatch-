'use strict';
window.JevFlow = (() => {
  const terminalStates = new Set(['completed', 'blocked', 'failed', 'timed_out', 'cancelled', 'interrupted']);
  const definitions = {
    setup: ['Open browser', 'Fresh session · local code', 'browser_setup', 'Create a fresh browser context and navigate to the starting URL. This uses the browser, not a model API.'],
    read: ['Read the page', 'Browser.observe()', 'browser_read', 'Read visible text and available controls into an indexed action list. Both agents use this same observation code. Screenshots are captured for the display, not sent to either policy.'],
    decide: ['Choose the next action', 'Jev API · typed decisions', 'jev_choose', 'Jev receives the observed state plus constrained questions for the operation and possible targets. Those questions travel in one API request. The hosted model’s internals are not visible here.'],
    route: ['Validate & route', 'Use the selected target', 'jev_choose', 'Validate the returned operation and matching target. Ignore unused target answers. TYPE_TEXT can request field text; DONE and BLOCKED skip browser actions and proceed to the final result.'],
    helper: ['Write field text', 'Text LLM · only if typing', 'field_text', 'A separate text-model call generates the value for an editable field. Ordinary clicks, scrolling, and DONE do not need this call. A stale retry can reuse text only when its input context is unchanged.'],
    act: ['Execute in browser', 'Browser.act()', 'browser_act', 'Check that the page and target are still valid, then perform the selected action. No model call occurs here. The next observation feeds the loop again; stale targets cause a fresh decision rather than a blind action retry.'],
    verify: ['Capture & check', 'Final page · literal checks', 'verify_page', 'Capture page text and links, then run any requested literal text/URL checks. These deterministic checks are separate from the model’s DONE decision. Without checks, completion remains the agent’s claim.'],
    return: ['Return to your app', 'Text + links + metrics', 'worker_run', 'Return the final page, source links, status, and usage. Your app can use its own analysis model to interpret the information or prepare a report; this demo does not run that downstream analysis.'],
  };
  const panels = {};
  const el = (tag, className, text) => { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; };
  function definition(side, node) {
    const d = [...definitions[node]];
    if (side === 'llm' && node === 'decide') return ['Generate action + text', 'Regular LLM · one JSON reply', 'baseline_choose', 'The regular text model receives page state and allowed actions. It generates a JSON response containing the operation, target, and any field text in one call. This is this demo’s baseline policy, not a claim about every LLM agent.'];
    if (side === 'llm' && node === 'route') return ['Validate the JSON', 'Observed targets only', 'baseline_choose', 'Check that the model’s operation and target are supported, and that typing text is valid. Invalid output stops execution. Field text is already included in the LLM reply, so no second model call is needed.'];
    return d;
  }
  function select(panel, node) {
    panel.selected = node;
    const d = definition(panel.side, node);
    panel.detailTitle.textContent = d[0]; panel.detailText.textContent = d[3];
    panel.code.hidden = true; panel.codeLabel.textContent = ''; panel.sourceKey = d[2];
    panel.source.textContent = 'View actual function ↗';
    for (const [key, button] of Object.entries(panel.nodes)) { button.classList.toggle('selected', key === node); button.setAttribute('aria-pressed', String(key === node)); }
  }
  function mount(side) {
    const lane = document.getElementById(side), root = el('section', 'flow-panel');
    root.setAttribute('aria-label', `${side === 'jev' ? 'Jev' : 'Regular LLM'} execution diagram`);
    const heading = el('div', 'flow-heading'), title = el('h3', '', 'Under the hood'), mode = el('span', 'flow-mode', 'Waiting for a run');
    heading.append(title, mode); root.append(heading);
    const diagram = el('div', 'flow-diagram'); root.append(diagram);
    const panel = {side, root, mode, nodes: {}, events: [], index: -1, timer: null, job: null, replaying: false, selected: null};
    function node(key, wide = true) {
      const d = definition(side, key), button = el('button', 'flow-node' + (wide ? ' wide' : ''));
      button.type = 'button'; button.dataset.node = key; button.setAttribute('aria-pressed', 'false');
      button.append(el('span', '', d[0]), el('small', '', d[1]), el('span', 'flow-count'));
      button.addEventListener('click', () => select(panel, key)); diagram.append(button); panel.nodes[key] = button;
    }
    function wire(text) { diagram.append(el('div', 'flow-wire wide', text)); }
    node('setup'); wire('↓'); node('read'); wire('↓ text + indexed controls'); node('decide');
    wire(side === 'jev' ? '↓ operation + target answers in one response' : '↓ generated action, target, and text');
    node('route');
    if (side === 'jev') { wire('↓ TYPE_TEXT only: use helper · other actions: skip helper'); node('helper'); }
    wire('↓ supported action'); node('act');
    wire('↶ Read the updated page and repeat · DONE / BLOCKED ↓');
    node('verify', false); node('return', false);
    panel.counters = el('p', 'flow-counters', 'Model calls appear here as the run progresses.'); root.append(panel.counters);
    panel.event = el('div', 'flow-event'); panel.event.setAttribute('role', 'status'); panel.event.setAttribute('aria-live', 'polite'); root.append(panel.event);
    const controls = el('div', 'flow-controls');
    panel.play = el('button', '', 'Replay'); panel.play.type = 'button';
    panel.prev = el('button', '', '← Event'); panel.prev.type = 'button';
    panel.next = el('button', '', 'Event →'); panel.next.type = 'button';
    const label = el('label', '', 'Recorded event'); panel.range = el('input'); panel.range.type = 'range'; panel.range.min = 0; panel.range.max = 0; panel.range.value = 0; label.append(panel.range);
    controls.append(panel.play, panel.prev, panel.next, label); root.append(controls);
    panel.play.addEventListener('click', () => {
      if (panel.timer) { stop(panel); draw(panel); return; }
      panel.replaying = true;
      if (panel.index < 0 || panel.index >= panel.events.length - 1) panel.index = 0;
      panel.timer = setInterval(() => { if (panel.index >= panel.events.length - 1) stop(panel); else panel.index++; draw(panel); }, 500);
      draw(panel);
    });
    function step(index) { stop(panel); panel.replaying = true; panel.index = Math.max(0, Math.min(panel.events.length - 1, index)); draw(panel); }
    panel.prev.addEventListener('click', () => step(panel.index < 0 ? panel.events.length - 2 : panel.index - 1));
    panel.next.addEventListener('click', () => step(panel.index < 0 ? 0 : panel.index + 1));
    panel.range.addEventListener('input', () => step(Number(panel.range.value)));
    root.append(el('p', 'flow-key', 'Fill = live or replayed call; outline = selected explanation. Replay uses recorded events at 0.5s per event for readability; it makes no model calls and does not replay the browser.'));
    const detail = el('div', 'flow-detail'); panel.detailTitle = el('h4'); panel.detailText = el('p');
    panel.source = el('button', 'flow-source-button', 'View actual function ↗'); panel.source.type = 'button';
    panel.codeLabel = el('div', 'flow-code-label'); panel.code = el('pre', 'flow-code'); panel.code.hidden = true;
    panel.source.addEventListener('click', async () => {
      const key = panel.sourceKey; panel.source.textContent = 'Loading function…';
      try {
        const result = await api('/v1/demo/source/' + encodeURIComponent(key));
        if (key !== panel.sourceKey) return;
        panel.codeLabel.textContent = `${result.file}:${result.line} · ${result.symbol}`;
        panel.code.textContent = result.code; panel.code.hidden = false; panel.source.textContent = 'View actual function ↗';
      } catch (error) { if (key === panel.sourceKey) panel.source.textContent = 'Could not load source: ' + error.message; }
    });
    detail.append(panel.detailTitle, panel.detailText, panel.source, panel.codeLabel, panel.code); root.append(detail);
    lane.querySelector('.lane-foot').after(root); select(panel, 'decide'); panels[side] = panel; draw(panel);
  }
  function stop(panel) { clearInterval(panel.timer); panel.timer = null; }
  function draw(panel) {
    const done = panel.job && terminalStates.has(panel.job.status), all = panel.events;
    const index = panel.replaying ? panel.index : all.length - 1;
    const events = all.slice(0, index + 1), last = events.at(-1), active = new Map(), counts = {};
    for (const event of events) {
      if (event.phase === 'start') active.set(event.span_id, event.node);
      if (event.phase === 'end' || event.phase === 'error') active.delete(event.span_id);
      if (event.phase === 'start' || event.phase === 'instant') counts[event.node] = (counts[event.node] || 0) + 1;
    }
    const activeNodes = new Set((!done || panel.replaying) ? active.values() : []);
    for (const [node, button] of Object.entries(panel.nodes)) {
      button.classList.toggle('active', activeNodes.has(node) || (panel.replaying && last?.phase === 'instant' && last.node === node));
      button.classList.toggle('recent', last?.node === node && !activeNodes.has(node));
      button.querySelector('.flow-count').textContent = counts[node] ? `${counts[node]}×` : '';
    }
    panel.mode.textContent = panel.replaying ? 'Recorded replay · slowed' : done ? `Stopped · ${panel.job.status}` : all.length ? 'Live · actual events' : 'Waiting for a run';
    const current = last ? `${(last.at_ms / 1000).toFixed(3)}s · ${last.cycle ? 'decision ' + last.cycle : 'startup'} · ${definition(panel.side, last.node)?.[0] || last.node} · ${last.phase === 'start' ? 'started' : last.phase === 'end' ? 'finished' : last.phase === 'error' ? 'stopped with an error' : 'returned'}` : panel.job ? 'This run has no recorded function trace. Start a new comparison to see its moving parts.' : 'Start a comparison to light up the real calls.';
    panel.event.replaceChildren(el('strong', '', current));
    if (last) {
      const d = last.details, pieces = [];
      if (d.api) pieces.push(d.api);
      if (d.model) pieces.push(d.model);
      if (d.questions?.length) pieces.push('Questions: ' + d.questions.join(', '));
      if (d.operation) pieces.push(d.operation + (d.target ? ' → target ' + d.target : ''));
      if (d.available_actions !== undefined) pieces.push(d.available_actions + ' supported actions observed');
      if (d.kind) pieces.push('Execute ' + d.kind);
      if (d.duration_ms !== undefined) pieces.push(d.duration_ms + 'ms');
      if (d.verification) pieces.push('Verification: ' + d.verification);
      if (d.error_type) pieces.push(d.error_type + ' · see run outcome');
      if (d.text_chars !== undefined) pieces.push(d.text_chars + ' characters + ' + d.source_links + ' source links');
      if (done && !panel.replaying && last.phase === 'start') pieces.push('Run stopped before this call returned; no completion event was received.');
      panel.event.append(el('span', '', pieces.join(' · ')));
    }
    panel.counters.textContent = panel.side === 'jev' ? `Jev API: ${counts.decide || 0} calls · Text LLM helper: ${counts.helper || 0} calls` : `Text LLM: ${counts.decide || 0} calls · Separate helper: 0 calls`;
    for (const control of [panel.play, panel.prev, panel.next, panel.range]) control.disabled = !done || !all.length;
    panel.play.textContent = panel.timer ? 'Pause replay' : 'Replay';
    panel.range.max = Math.max(0, all.length - 1); panel.range.value = Math.max(0, index);
    panel.range.setAttribute('aria-valuetext', `Event ${Math.max(0, index + 1)} of ${all.length}`);
  }
  ['llm', 'jev'].forEach(mount);
  document.getElementById('show-flow').addEventListener('change', event => {
    for (const panel of Object.values(panels)) { panel.root.hidden = !event.target.checked; if (panel.root.hidden) stop(panel); }
  });
  return {update(side, job) {
    const panel = panels[side];
    if (panel.job?.id !== job.id) { stop(panel); panel.index = -1; panel.replaying = false; }
    panel.job = job; panel.events = job.progress.trace || []; draw(panel);
  }};
})();
