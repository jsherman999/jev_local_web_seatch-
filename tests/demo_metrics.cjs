// Pure accounting tests: no browser or network calls.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const fields = {text_input: '2.5', text_cached: '.25', text_output: '15', typesafe_input: '.042', typesafe_output: '0'};
const context = vm.createContext({document: {querySelector: selector => ({value: fields[selector.slice(1)]})}});
const source = fs.readFileSync(require('node:path').join(__dirname, '../jev_service/static/app.js'), 'utf8');
vm.runInContext(source.split("function inferredProvider")[0], context);
const cost = group => vm.runInContext(`groupCost(${JSON.stringify(group)})`, context);
const text = {provider: 'text', input_tokens: 1000, output_tokens: 200, cached_tokens: 400, tokens_complete: true, cost_complete: false};
assert.equal(cost(text).value, .0046);
assert.equal(cost({...text, cost_complete: true, reported_cost_usd: .002}).value, .002);
assert.equal(cost({...text, tokens_complete: false}).value, null);
assert.equal(cost({...text, provider: 'typesafe', input_tokens: 10000, output_tokens: 300, cached_tokens: 0}).value, .00042);
fields.text_cached = '';
assert.equal(cost(text).value, null);
assert.equal(cost({...text, cached_tokens: 0}).value, .0055);
fields.text_input = '-1';
assert.equal(cost(text).value, null);
console.log('Cost accounting: 7 checks passed');

const compare = (a, b, label = 'DeepSeek') => vm.runInContext(`costComparison(${JSON.stringify(a)}, ${JSON.stringify(b)}, ${JSON.stringify(label)})`, context);
assert.equal(compare({complete: true, cost: 15}, {complete: true, cost: 1}).title, 'DeepSeek 15× Jev');
assert.equal(compare({complete: true, cost: 1}, {complete: true, cost: 15}).title, 'Jev 15× DeepSeek');
assert.equal(compare({complete: true, cost: 0}, {complete: true, cost: 0}).title, 'Equal API cost');
assert.equal(compare({complete: false, cost: 1}, {complete: true, cost: 0}).title, 'Cost comparison unavailable');
assert.equal(compare({complete: true, cost: 1}, {complete: true, cost: 0}).title, 'DeepSeek costs more');
const isolated = vm.runInContext(`groupCost(${JSON.stringify(text)}, {progress: {model_config: {rates: {input: 1, cached: .1, output: 2}}}})`, context);
assert.equal(isolated.value, .00104);
console.log('Cost comparison and independent model pricing: 6 checks passed');

vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../jev_service/static/batch.js'), 'utf8').split('window.JevBatch =')[0], context);
const bm = (row, batch) => vm.runInContext(`batchMetrics(${JSON.stringify(row)}, ${JSON.stringify(batch)})`, context);
const snapshot = {helper_rates: {input: 2, cached: 1, output: 3}, jev_rates: {input: .1, output: 0}};
const usage = {calls: 1, groups: [{...text, cached_tokens: 0}]};
assert.equal(bm({status: 'pending'}, snapshot).complete, false);
assert.equal(bm({status: 'running', usage}, snapshot).seconds, null);
assert.equal(bm({kind: 'jev', status: 'completed', execution_ms: 1250, usage}, snapshot).cost, .0026);
assert.equal(bm({kind: 'llm', status: 'completed', execution_ms: 1250, usage, rates: {input: 1, output: 1}}, snapshot).cost, .0012);
assert.equal(bm({kind: 'llm', status: 'completed', usage, rates: {}}, snapshot).complete, false);
assert.equal(bm({kind: 'llm', status: 'failed', execution_ms: 1500, usage, rates: {input: 1, output: 1}}, snapshot).seconds, null);
console.log('Batch charts: 6 checks passed');

for (const status of ['failed', 'blocked', 'cancelled', 'timed_out', 'interrupted', 'not_run']) {
  const result = bm({kind:'llm',status,execution_ms:1500,usage,rates:{input:1,output:1}},snapshot);
  assert.equal(result.unsuccessful,true);
  assert.equal(result.complete,false);
  assert.equal(result.seconds,null);
}
assert.equal(compare({unsuccessful:true,complete:false},{complete:true,cost:1}).title,'NA');
console.log('Unsuccessful runs: metrics suppressed');
