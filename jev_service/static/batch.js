'use strict';
function batchMetrics(row, batch) {
  const rates = row.kind === 'jev' ? {text: batch.helper_rates, typesafe: batch.jev_rates} : {text: row.rates};
  const m = metrics({progress: {usage: row.usage?.groups ? row.usage : {calls: 0, groups: []}, rate_snapshot: rates}});
  const ended = terminal.has(row.status);
  const seconds = ended && row.execution_ms != null ? row.execution_ms / 1000 : null;
  return {...m, seconds, ended, complete: ended && m.complete};
}
function batchOutcome(row) {
  if (row.status === 'completed') return row.verification === 'passed' ? 'Check passed' : 'Completed · unverified';
  return row.status.replaceAll('_', ' ');
}
window.JevBatch = (() => {
  let selected = new Map(), listing = null, current = null, timer = null;
  let starting = false;
  const active = () => starting || current?.status === 'running';
  const el = (tag, text, className) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (className) n.className = className; return n; };
  function picker() {
    const root = $('#batch-models'); root.replaceChildren();
    $('#batch-choice-count').textContent = `Compare models · ${selected.size} / 10 selected`;
    if (!listing) { root.textContent = 'Enter a key and load models first.'; return; }
    const query = $('#batch-search').value.trim().toLowerCase();
    for (const model of listing.models.filter(m => m.id.toLowerCase().includes(query))) {
      const row = el('div', null, 'batch-choice'), label = el('label'), check = el('input');
      check.type = 'checkbox'; check.checked = selected.has(model.id);
      check.disabled = active() || !model.supported || (!check.checked && selected.size >= 10);
      label.append(check, el('span', model.id + (model.supported ? '' : ' · unavailable for text chat'))); row.append(label);
      check.addEventListener('change', () => {
        if (check.checked && selected.size < 10) {
          const rates = listing.provider === config.default_llm?.provider && model.id === config.text_model ? config.default_llm.rates : {};
          selected.set(model.id, {...rates});
        } else selected.delete(model.id);
        picker();
      });
      if (check.checked) {
        const prices = el('div', null, 'batch-prices');
        for (const name of ['input', 'cached', 'output']) {
          const priceLabel = el('label', name), input = el('input');
          input.type = 'number'; input.min = '0'; input.step = 'any'; input.placeholder = 'Unknown';
          input.value = selected.get(model.id)[name] ?? ''; input.disabled = active();
          input.setAttribute('aria-label', `${model.id} ${name} price`);
          input.addEventListener('input', () => { selected.get(model.id)[name] = input.value === '' ? null : Number(input.value); });
          priceLabel.append(input); prices.append(priceLabel);
        }
        row.append(prices);
      }
      root.append(row);
    }
    if (!root.children.length) root.textContent = 'No matching models.';
  }
  function setCatalog(value) { listing = value; selected.clear(); picker(); }
  function lock(value) {
    busy = value;
    for (const id of ['start', 'sample', 'llm-key', 'llm-provider', 'load-models', 'batch-start']) $('#' + id).disabled = value;
    $('#llm-model').disabled = value || !loadedCatalog;
    $('#stop').disabled = true;
    $('#batch-cancel').disabled = !value || current?.cancel_requested;
    picker();
  }
  function showSingle() {
    $('#batch-results').hidden = true;
    $('.race').hidden = false; $('.how-it-works').hidden = false;
  }
  function chart(root, rows, field) {
    root.replaceChildren();
    const values = rows.map(r => field === 'cost' ? (r.m.complete ? r.m.cost : null) : r.m.seconds);
    const maximum = Math.max(...values.filter(v => v !== null), 0);
    rows.forEach((r, index) => {
      const value = values[index], item = el('div', null, 'batch-bar-row');
      const valueLabel = value === null ? 'Unavailable' : field === 'cost' ? (r.m.estimated ? '≈ ' : '') + money(value) : value.toFixed(2) + 's';
      item.append(el('div', `${r.row.model} · ${batchOutcome(r.row)}`, 'batch-bar-label'));
      const track = el('div', null, 'batch-track'), bar = el('div', null, 'batch-bar');
      if (r.row.kind === 'jev') bar.classList.add('reference');
      if (r.row.status !== 'completed') bar.classList.add('unsuccessful');
      bar.style.width = value === null || maximum === 0 ? '0%' : `${value / maximum * 100}%`;
      track.setAttribute('aria-label', `${r.row.model}: ${valueLabel}, ${batchOutcome(r.row)}`);
      track.append(bar); item.append(track, el('span', valueLabel)); root.append(item);
    });
  }
  function renderBatch() {
    $('#batch-results').hidden = false; $('.race').hidden = true; $('.how-it-works').hidden = true;
    $('#summary').hidden = true; $('#cost-comparison').hidden = true;
    const rows = current.runs.map(row => ({row, m: batchMetrics(row, current)}));
    const done = rows.filter(r => r.m.ended).length;
    const running = current.runs.find(r => ['running', 'queued'].includes(r.status));
    $('#batch-progress').textContent = `${done} / ${rows.length} runs ended · ${current.status}${running ? ' · ' + running.model : ''}${current.cancel_requested && current.status === 'running' ? ' · stopping' : ''}${current.error ? ' · ' + current.error : ''}`;
    chart($('#batch-cost-chart'), rows, 'cost'); chart($('#batch-time-chart'), rows, 'seconds');
    const table = $('#batch-table'); table.replaceChildren();
    const reference = rows[0].m;
    for (const {row, m} of rows) {
      const tr = el('tr');
      const ratio = m.complete && reference.complete && reference.cost > 0 ? (m.cost / reference.cost).toFixed(2) + '×' : '—';
      for (const value of [row.model, batchOutcome(row), m.seconds == null ? '—' : m.seconds.toFixed(2),
        m.usage.groups?.length ? (m.tokensComplete ? '' : '≥ ') + integer(m.tokens) : '—',
        m.complete ? (m.estimated ? '≈ ' : '') + money(m.cost) : 'Unavailable', ratio]) tr.append(el('td', value));
      if (row.error) tr.children[1].append(el('small', row.error));
      table.append(tr);
    }
    lock(active());
  }
  async function pollBatch() {
    clearTimeout(timer);
    try {
      current = await api('/v1/batches/' + encodeURIComponent(current.id)); renderBatch();
      if (active()) timer = setTimeout(pollBatch, 1000);
    } catch (error) {
      $('#batch-progress').textContent = `Could not refresh: ${error.message}. The batch is not being resubmitted.`;
      if (error.status === 404) { current = null; sessionStorage.removeItem('jev-batch'); lock(false); }
      else timer = setTimeout(pollBatch, 2500);
    }
  }
  async function restore() {
    current = {id: sessionStorage.getItem('jev-batch'), status: 'running'}; lock(true);
    await pollBatch();
    if (current?.request) {
      const r = current.request;
      $('#url').value = r.url; $('#goal').value = r.goal; $('#steps').value = r.max_steps;
      $('#timeout').value = r.timeout_seconds; $('#verify').value = r.verify.text_contains[0] || '';
      practicePreset = $('#verify').value === 'Practice task complete.';
    }
  }
  $('#batch-search').addEventListener('input', picker);
  $('#batch-start').addEventListener('click', async () => {
    if (busy || active()) return;
    if (!$('#run-form').reportValidity()) return;
    if (!listing || !selected.size || !$('#llm-key').value.trim()) {
      $('#batch-picker-message').textContent = 'Enter a key, load models, and select 1–10 models.'; return;
    }
    const key = $('#llm-key').value.trim();
    const body = {url: $('#url').value, goal: $('#goal').value, max_steps: Number($('#steps').value),
      timeout_seconds: Number($('#timeout').value), verify: {text_contains: $('#verify').value.trim() ? [$('#verify').value.trim()] : []},
      models: [...selected].map(([model, rates]) => ({provider: listing.provider, model, rates})),
      helper_rates: {input: rate('text_input'), cached: rate('text_cached'), output: rate('text_output')},
      jev_rates: {input: rate('typesafe_input'), output: rate('typesafe_output')}};
    starting = true; lock(true); clearTimeout(pollTimer);
    $('#batch-picker-message').textContent = 'Starting sequential comparison…';
    try {
      current = await api('/v1/batches', {method: 'POST', headers: {'X-LLM-API-Key': key}, body: JSON.stringify(body)});
      starting = false; comparison = null; sessionStorage.removeItem('jev-comparison'); sessionStorage.setItem('jev-batch', current.id);
      $('#batch-picker-message').textContent = 'Comparison started. Results appear below.';
      message(''); renderBatch(); await pollBatch();
    } catch (error) {
      if (error.status) { starting = false; lock(false); $('#batch-picker-message').textContent = error.message; }
      else $('#batch-picker-message').textContent = 'Submission response lost. A batch may have started; do not submit again blindly. Inspect the service before retrying.';
    }
  });
  $('#batch-cancel').addEventListener('click', async () => {
    if (!current?.id) return;
    $('#batch-cancel').disabled = true;
    try { current = await api(`/v1/batches/${current.id}/cancel`, {method: 'POST'}); renderBatch(); await pollBatch(); }
    catch (error) { $('#batch-progress').textContent = error.message; $('#batch-cancel').disabled = false; }
  });
  return {active, setCatalog, restore, showSingle};
})();
