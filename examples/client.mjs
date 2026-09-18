// Run: node examples/client.mjs (uses paid model APIs). Requires Node 18+.
const base = process.env.JEV_URL || 'http://127.0.0.1:8776';
const headers = {'Content-Type': 'application/json'};
if (process.env.JEV_API_TOKEN) headers.Authorization = `Bearer ${process.env.JEV_API_TOKEN}`;
async function call(path, body) {
  const response = await fetch(base + path, {
    method: body === undefined ? 'GET' : 'POST', headers,
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(10000)
  });
  if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
  return response.json();
}
let job = await call('/v1/jobs', {
  url: 'https://example.com', goal: 'Read this page and stop when Example Domain is visible.',
  verify: {text_contains: ['Example Domain']}
});
console.log('Job:', job.id);
const terminal = new Set(['completed', 'failed', 'blocked', 'timed_out', 'cancelled', 'interrupted']);
const deadline = Date.now() + 300000;
while (!terminal.has(job.status)) {
  if (Date.now() > deadline) {
    await call(`/v1/jobs/${job.id}/cancel`, {});
    throw new Error('Client deadline exceeded; cancellation requested');
  }
  await new Promise(resolve => setTimeout(resolve, 1000));
  job = await call(`/v1/jobs/${job.id}`);
}
console.log(JSON.stringify(job, null, 2));
if (job.status !== 'completed' || job.result?.verification !== 'passed') process.exitCode = 1;
