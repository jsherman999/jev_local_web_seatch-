'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const terminal = new Set(['completed', 'blocked', 'failed', 'timed_out', 'cancelled', 'interrupted']);
let comparison = null, pollTimer = null, busy = false, restoring = false, config = {};
let practicePreset = false;
let catalogVersion = 0, loadedCatalog = null, modelTimer = null;
const money = value => '$' + value.toFixed(6);
const integer = value => Math.round(value).toLocaleString();
const message = text => { $('#message').textContent = text; };
function rate(name) {
  const value = $('#' + name).value, parsed = Number(value);
  return value === '' || !Number.isFinite(parsed) || parsed < 0 ? null : parsed;
}
function groupCost(group, job = null) {
  if (group.cost_complete) return {value: group.reported_cost_usd, estimated: false};
  if (!group.tokens_complete) return {value: null, estimated: false};
  const provider = group.provider, prices = job?.progress.rate_snapshot?.[provider] || job?.progress.model_config?.rates;
  const input = prices ? prices.input ?? null : rate(provider + '_input');
  const output = prices ? prices.output ?? null : rate(provider + '_output');
  const cached = provider === 'text' ? (prices ? prices.cached ?? null : rate('text_cached')) : input;
  if (input === null || output === null || (group.cached_tokens && cached === null)) return {value: null, estimated: true};
  return {value: ((group.input_tokens - group.cached_tokens) * input + group.cached_tokens * (cached ?? input) + group.output_tokens * output) / 1e6, estimated: true};
}
function metrics(job) {
  const usage = job.result?.usage?.groups ? job.result.usage : (job.progress.usage || {calls: 0, groups: []});
  let tokens = 0, cost = 0, complete = usage.groups.length > 0, tokensComplete = true, estimated = false;
  for (const group of usage.groups) {
    tokens += group.input_tokens + group.output_tokens;
    tokensComplete &&= group.tokens_complete;
    const result = groupCost(group, job);
    complete &&= result.value !== null;
    estimated ||= result.estimated;
    cost += result.value ?? 0;
  }
  return {usage, tokens, tokensComplete, cost, complete, estimated};
}
function duration(job) {
  if (job.progress.execution_ms != null) return job.progress.execution_ms / 1000;
  if (job.status === 'running' && job.progress.started_at) return Math.max(0, (Date.now() - Date.parse(job.progress.started_at)) / 1000);
  return job.result ? job.result.elapsed_ms / 1000 : 0;
}
function renderLane(side, job) {
  window.JevFlow?.update(side, job);
  const root = $('#' + side), m = metrics(job), running = job.status === 'running';
  if (side === 'llm') $('.model', root).textContent = job.progress.model_config?.model || m.usage.groups.find(g => g.provider === 'text')?.model || config.text_model;
  $('.status', root).textContent = job.status.replaceAll('_', ' ');
  $('.status', root).dataset.state = job.status;
  $('.elapsed', root).textContent = duration(job).toFixed(1) + 's';
  $('.tokens', root).textContent = (m.tokensComplete ? '' : '≥ ') + integer(m.tokens);
  $('.cost', root).textContent = m.complete ? (m.estimated ? '≈ ' : '') + money(m.cost) : 'Unavailable';
  $('.calls', root).textContent = m.usage.calls + ' model call' + (m.usage.calls === 1 ? '' : 's');
  const url = job.result?.page.url || job.progress.url || job.request.url;
  $('.address', root).textContent = url;
  $('.address', root).title = url;
  if (job.progress.screenshot) {
    const image = $('.screen', root), source = 'data:image/jpeg;base64,' + job.progress.screenshot;
    if (image.getAttribute('src') !== source) image.src = source;
    image.hidden = false;
    $('.empty', root).hidden = true;
  }
  const actions = job.result?.actions || job.progress.actions || [];
  $('.activity', root).textContent = running ? (m.usage.pending_calls ? 'Choosing the next action…' : 'Working in the browser…') : job.status === 'queued' ? 'Waiting to start…' : `${actions.length} browser actions · ${job.result?.verification === 'passed' ? 'check passed' : 'run stopped'}`;
  $('.outcome', root).textContent = job.error || (job.status === 'completed' ? job.result?.verification === 'passed' ? 'Finished · your final-page check passed.' : 'Agent reports done · no independent outcome check was requested.' : '');
  const usageBox = $('.usage', root);
  usageBox.replaceChildren();
  for (const group of m.usage.groups) {
    const p = document.createElement('p'), c = groupCost(group, job);
    p.textContent = `${group.provider === 'typesafe' ? 'Jev decisions' : side === 'jev' ? 'Text helper' : 'LLM decisions + text'} · ${group.model}\n${integer(group.input_tokens)} input + ${integer(group.output_tokens)} output (${integer(group.cached_tokens)} cached input) · ${group.calls} calls · ${c.value === null ? 'cost unavailable' : (c.estimated ? 'estimated ' : 'reported ') + money(c.value)}${group.tokens_complete ? '' : ' · usage incomplete / call pending'}`;
    usageBox.append(p);
  }
  if (!m.complete || !m.tokensComplete) {
    const p = document.createElement('p');
    p.textContent = 'Only returned usage can be counted. A pending, failed, or cancelled provider request may still incur charges. Missing usage is not zero.';
    usageBox.append(p);
  }
  const list = $('.actions', root); list.replaceChildren();
  for (const action of actions) { const li = document.createElement('li'); li.textContent = `${action.operation || action.kind || ''} · ${action.action}`; list.append(li); }
  $('.result-text', root).textContent = job.result?.page.text || (terminal.has(job.status) ? 'No final page was captured.' : 'The final page text will appear when this agent stops.');
  const link = $('.source', root);
  link.hidden = !job.result || !/^https?:\/\//i.test(url);
  if (!link.hidden) link.href = url;
}
function costComparison(a, b, label) {
  if (!a.complete || !b.complete) return {title: 'Cost comparison unavailable', detail: 'Enter model rates before running, or use a provider that reports cost. Missing usage is not zero.'};
  const prefix = a.estimated || b.estimated ? '≈ ' : '';
  if (a.cost === b.cost) return {title: 'Equal API cost', detail: `${prefix}${money(a.cost)} each.`};
  const llmMore = a.cost > b.cost, high = llmMore ? a.cost : b.cost, low = llmMore ? b.cost : a.cost;
  const expensive = llmMore ? label : 'Jev', cheaper = llmMore ? 'Jev' : label;
  const ratio = low > 0 ? Number((high / low).toPrecision(3)).toLocaleString() + '×' : null;
  return {title: ratio ? `${prefix}${expensive} ${ratio} ${cheaper}` : `${expensive} costs more`,
    detail: `${expensive} cost ${prefix}${money(high - low)} more (${money(high)} vs ${money(low)}).${low === 0 ? ' A ratio is undefined when one cost is zero.' : ''} Jev includes its text helper. This compares API spend, not outcome quality.`};
}
function render() {
  if (window.JevBatch?.active()) return;
  if (!comparison?.llm) return;
  for (const side of ['llm', 'jev']) renderLane(side, comparison[side]);
  busy = !['llm', 'jev'].every(side => terminal.has(comparison[side].status));
  $('#start').disabled = busy;
  $('#stop').disabled = !busy;
  $('#sample').disabled = busy;
  for (const id of ['llm-key', 'llm-provider', 'load-models']) $('#' + id).disabled = busy;
  $('#llm-model').disabled = busy || !loadedCatalog;
  $('#cost-comparison').hidden = busy;
  const summary = $('#summary'); summary.hidden = busy;
  if (!busy) {
    const a = comparison.llm, b = comparison.jev, am = metrics(a), bm = metrics(b);
    const label = a.progress.model_config?.label || am.usage.groups.find(g => g.provider === 'text')?.model || config.text_model || 'Regular LLM';
    const cost = costComparison(am, bm, label);
    $('#cost-ratio').textContent = cost.title; $('#cost-difference').textContent = cost.detail;
    let text = `Regular LLM: ${a.status.replaceAll('_', ' ')} in ${duration(a).toFixed(1)}s. Jev: ${b.status.replaceAll('_', ' ')} in ${duration(b).toFixed(1)}s.`;
    if (a.status === 'completed' && b.status === 'completed' && a.result?.verification === 'passed' && b.result?.verification === 'passed') {
      const difference = Math.abs(duration(a) - duration(b));
      text += ` Both final-page checks passed. ${difference < .1 ? 'Their times were essentially equal.' : (duration(a) < duration(b) ? 'The regular LLM' : 'Jev') + ' finished ' + difference.toFixed(1) + 's sooner.'}`;
    }
    if (am.complete && bm.complete) text += ` Combined ${am.estimated || bm.estimated ? 'estimated' : 'reported'} API cost: ${money(am.cost + bm.cost)}.`;
    else text += ' A full cost total is unavailable; see usage details.';
    summary.textContent = text;
  }
}
async function api(path, options = {}) {
  const headers = {'Content-Type': 'application/json'};
  if ($('#token').value) headers.Authorization = 'Bearer ' + $('#token').value;
  Object.assign(headers, options.headers || {});
  const response = await fetch(path, {...options, headers, cache: 'no-store'});
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || `HTTP ${response.status}`));
    error.status = response.status; throw error;
  }
  return body;
}
async function poll() {
  clearTimeout(pollTimer);
  if (!comparison?.id) return;
  try {
    comparison = await api('/v1/comparisons/' + encodeURIComponent(comparison.id));
    if (restoring) {
      const request = comparison.jev.request;
      $('#url').value = request.url; $('#goal').value = request.goal;
      $('#steps').value = request.max_steps; $('#timeout').value = request.timeout_seconds;
      $('#verify').value = request.verify.text_contains[0] || '';
      practicePreset = $('#verify').value === 'Practice task complete.';
      restoring = false;
    }
    render(); message(busy ? 'Both agents are working in separate browser sessions.' : 'Comparison finished. Review the results below.');
    if (busy) pollTimer = setTimeout(poll, 900);
  } catch (error) {
    message(`Could not refresh: ${error.message}. ${error.status === 401 ? 'Enter your service token in Run settings to reconnect.' : 'Reconnecting; the jobs are not being resubmitted.'}`);
    if (error.status === 404) { busy = false; $('#start').disabled = false; $('#stop').disabled = true; sessionStorage.removeItem('jev-comparison'); }
    else pollTimer = setTimeout(poll, 2500);
  }
}
function inferredProvider(key) {
  for (const [prefix, provider] of [['sk-ant-', 'anthropic'], ['sk-or-v1-', 'openrouter'], ['sk-proj-', 'openai'], ['sk-svcacct-', 'openai'], ['gsk_', 'groq'], ['AIza', 'google']]) if (key.startsWith(prefix)) return provider;
  return '';
}
function setLLMRates(prices = {}) {
  for (const field of ['input', 'cached', 'output']) $('#llm_' + field).value = prices[field] ?? '';
}
function resetCatalog() {
  catalogVersion++; loadedCatalog = null; clearTimeout(modelTimer);
  window.JevBatch?.setCatalog(null);
  $('#llm-model').replaceChildren(new Option($('#llm-key').value.trim() ? 'Load models to choose' : `Service default · ${config.text_model || ''}`, ''));
  $('#llm-model').disabled = true;
  setLLMRates($('#llm-key').value.trim() ? {} : config.default_llm?.rates);
}
async function loadModels() {
  clearTimeout(modelTimer);
  const key = $('#llm-key').value.trim(), provider = $('#llm-provider').value;
  if (!key) { resetCatalog(); $('#model-message').textContent = 'Using the service default. Enter a key to choose another model.'; return; }
  const version = ++catalogVersion;
  loadedCatalog = null; $('#llm-model').disabled = true;
  $('#model-message').textContent = 'Loading provider models…';
  try {
    const result = await api('/v1/demo/models', {method: 'POST', headers: {'X-LLM-API-Key': key}, body: JSON.stringify({provider: provider || null})});
    if (version !== catalogVersion) return;
    loadedCatalog = result; $('#llm-provider').value = result.provider;
    window.JevBatch?.setCatalog(result);
    $('#llm-model').replaceChildren(new Option('Choose a model', ''));
    for (const model of result.models) {
      const option = new Option(model.id + (model.supported ? '' : ' · not a text chat model'), model.id);
      option.disabled = !model.supported; $('#llm-model').append(option);
    }
    $('#llm-model').disabled = busy;
    $('#model-message').textContent = `${result.label} · ${result.models.length} models loaded. Choose a text chat model. Account permissions and chat support still apply. Key is held in memory only.`;
  } catch (error) { if (version === catalogVersion) $('#model-message').textContent = error.message; }
}
$('#llm-key').addEventListener('input', () => {
  resetCatalog();
  const key = $('#llm-key').value.trim(), detected = inferredProvider(key);
  $('#llm-provider').value = detected;
  $('#model-message').textContent = detected ? 'Provider recognized. Loading models…' : key ? 'This key format is ambiguous. Choose its provider, then load models.' : 'Using the service default. Keys are never saved.';
  if (detected) modelTimer = setTimeout(loadModels, 700);
});
$('#llm-provider').addEventListener('change', () => { resetCatalog(); if ($('#llm-key').value.trim()) loadModels(); });
$('#load-models').addEventListener('click', loadModels);
$('#llm-model').addEventListener('change', () => {
  const sameDefault = loadedCatalog?.provider === config.default_llm?.provider && $('#llm-model').value === config.text_model;
  setLLMRates(sameDefault ? config.default_llm.rates : {});
  if (!sameDefault) $('#model-message').textContent = 'Model selected. Enter its token rates under Run settings & pricing for an estimated cost comparison. Provider-reported cost is used when available.';
});
$('#run-form').addEventListener('submit', async event => {
  event.preventDefault(); if (busy) return;
  if (!$('#goal').value.trim()) { message('Enter a task for both agents.'); return; }
  const key = $('#llm-key').value.trim();
  if (key && (!loadedCatalog || !$('#llm-model').value)) { message('Load models and choose the regular LLM model first.'); return; }
  const prices = {input: rate('llm_input'), cached: rate('llm_cached'), output: rate('llm_output')};
  const llm = key ? {provider: loadedCatalog.provider, model: $('#llm-model').value, rates: prices} : null;
  const parsed = new URL($('#url').value);
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) { message('Use an HTTP or HTTPS URL without embedded credentials.'); return; }
  busy = true; $('#start').disabled = true; $('#sample').disabled = true; $('#summary').hidden = true; $('#cost-comparison').hidden = true;
  clearTimeout(pollTimer); message('Starting both agents…');
  try {
    comparison = await api('/v1/comparisons', {method: 'POST', headers: key ? {'X-LLM-API-Key': key} : {}, body: JSON.stringify({
      llm, baseline_rates: prices, url: parsed.href, goal: $('#goal').value, max_steps: Number($('#steps').value),
      timeout_seconds: Number($('#timeout').value), verify: {text_contains: $('#verify').value.trim() ? [$('#verify').value.trim()] : []},
    })});
    sessionStorage.removeItem('jev-batch');
    window.JevBatch?.showSingle();
    sessionStorage.setItem('jev-comparison', comparison.id);
    for (const side of ['llm', 'jev']) { $('.screen', $('#' + side)).hidden = true; $('.empty', $('#' + side)).hidden = false; }
    render(); await poll();
  } catch (error) {
    if (error.status) { busy = false; $('#start').disabled = false; $('#sample').disabled = false; message(error.message); }
    else message('The submission response was lost. A comparison may have started. Do not submit again blindly; inspect the jobs in API docs.');
  }
});
$('#stop').addEventListener('click', async () => {
  if (!comparison) return;
  $('#stop').disabled = true;
  try { comparison = await api(`/v1/comparisons/${comparison.id}/cancel`, {method: 'POST'}); message('Stopping both browsers…'); await poll(); }
  catch (error) { message('Could not stop: ' + error.message); $('#stop').disabled = false; }
});
$('#sample').addEventListener('click', () => {
  $('#url').value = location.origin + '/demo/playground';
  $('#goal').value = 'Search for backpack, open the Trail daypack, and expand its Specifications. Stop when the capacity, weight, and price are visible.';
  $('#verify').value = 'Practice task complete.';
  practicePreset = true;
  message('Practice task loaded. Start the comparison when you’re ready.');
});
for (const field of ['url', 'goal']) $('#' + field).addEventListener('input', () => {
  if (practicePreset) { $('#verify').value = ''; practicePreset = false; }
});
$('#verify').addEventListener('input', () => { practicePreset = false; });
document.querySelectorAll('.prices input').forEach(input => input.addEventListener('input', render));
setInterval(() => { if (comparison?.llm && busy) for (const side of ['llm', 'jev']) $('.elapsed', $('#' + side)).textContent = duration(comparison[side]).toFixed(1) + 's'; }, 100);
async function init() {
  try {
    const responses = await Promise.all([fetch('/demo/config'), fetch('/ready')]);
    config = await responses[0].json(); const ready = await responses[1].json();
    $('#connection').textContent = ready.ready ? 'Service connected' : 'Service needs attention';
    $('#connection').classList.toggle('online', ready.ready);
    $('#token-label').hidden = !config.auth_required;
    if (config.auth_required) $('.settings').open = true;
    $('.model', $('#llm')).textContent = config.text_model;
    $('.model', $('#jev')).textContent = config.jev_model;
    for (const provider of config.providers) $('#llm-provider').append(new Option(provider.label, provider.id));
    resetCatalog();
    for (const [key, value] of Object.entries(config.rates)) $('#' + key).value = value;
    if (!ready.ready) message('The browser or model credentials are unavailable. Check service readiness in API docs.');
    if (sessionStorage.getItem('jev-batch')) { await window.JevBatch.restore(); return; }
    const saved = sessionStorage.getItem('jev-comparison');
    if (saved) { comparison = {id: saved}; busy = true; restoring = true; $('#start').disabled = true; await poll(); }
  } catch { message('Could not connect to the service. Reload the page to try again.'); }
}
init();
