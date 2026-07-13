import { api, fmt } from '/web/app.js';
import { barChart, CHART } from '/web/charts.js';

const RANGES = [
  { key: '7d',  label: '7d',  days: 7 },
  { key: '30d', label: '30d', days: 30 },
  { key: '90d', label: '90d', days: 90 },
  { key: 'all', label: 'All', days: null },
];

function readRange() {
  const q = (location.hash.split('?')[1] || '');
  const m = /(?:^|&)range=([^&]+)/.exec(q);
  const k = m && decodeURIComponent(m[1]);
  return RANGES.find(r => r.key === k) || RANGES[1];
}

function writeRange(key) {
  const base = (location.hash.replace(/^#/, '').split('?')[0]) || '/agents';
  location.hash = '#' + base + '?range=' + encodeURIComponent(key);
}

function sinceIso(range) {
  if (!range.days) return null;
  return new Date(Date.now() - range.days * 86400 * 1000).toISOString();
}

// /api/agents returns one row per (agent_type, model); fold to one row per
// agent_type for the table, keeping the per-model split for the tooltip line.
function groupByType(rows) {
  const byType = new Map();
  for (const r of rows) {
    const g = byType.get(r.agent_type) || {
      agent_type: r.agent_type, runs: 0, sessions: 0, models: [],
      input_tokens: 0, output_tokens: 0, cache_read_tokens: 0,
      cache_create_tokens: 0, reasoning_output_tokens: 0,
      cost_usd: 0, cost_known: true, last_used: null,
    };
    g.runs += r.runs;
    g.sessions += r.sessions;
    g.models.push(r.model);
    g.input_tokens += r.input_tokens;
    g.output_tokens += r.output_tokens;
    g.cache_read_tokens += r.cache_read_tokens;
    g.reasoning_output_tokens += r.reasoning_output_tokens || 0;
    g.cache_create_tokens += r.cache_create_5m_tokens + r.cache_create_1h_tokens;
    if (r.cost_usd == null) g.cost_known = false; else g.cost_usd += r.cost_usd;
    if (!g.last_used || r.last_used > g.last_used) g.last_used = r.last_used;
    byType.set(r.agent_type, g);
  }
  const out = [...byType.values()];
  for (const g of out) g.total_tokens = g.input_tokens + g.output_tokens + g.cache_read_tokens + g.cache_create_tokens;
  out.sort((a, b) => b.total_tokens - a.total_tokens);
  return out;
}

function groupRuns(rows) {
  const byRun = new Map();
  for (const r of rows) {
    const key = `${r.source}:${r.session_id}:${r.agent_id}`;
    const run = byRun.get(key) || {
      ...r, models: [], model_calls: 0, input_tokens: 0, output_tokens: 0,
      cache_read_tokens: 0, cache_create_tokens: 0, reasoning_output_tokens: 0,
      cost_usd: 0, cost_known: true, peak_context_utilization: 0,
    };
    run.models.push(r.model);
    run.model_calls += r.model_calls || 0;
    run.input_tokens += r.input_tokens || 0;
    run.output_tokens += r.output_tokens || 0;
    run.cache_read_tokens += r.cache_read_tokens || 0;
    run.cache_create_tokens += (r.cache_create_5m_tokens || 0) + (r.cache_create_1h_tokens || 0);
    run.reasoning_output_tokens += r.reasoning_output_tokens || 0;
    run.peak_context_utilization = Math.max(run.peak_context_utilization, r.peak_context_utilization || 0);
    if (r.cost_usd == null) run.cost_known = false; else run.cost_usd += r.cost_usd;
    if (!run.started || r.started < run.started) run.started = r.started;
    if (!run.ended || r.ended > run.ended) run.ended = r.ended;
    byRun.set(key, run);
  }
  const runs = [...byRun.values()];
  for (const run of runs) {
    run.total_tokens = run.input_tokens + run.output_tokens + run.cache_read_tokens + run.cache_create_tokens;
  }
  return runs.sort((a, b) => (b.ended || '').localeCompare(a.ended || ''));
}

function runHref(run) {
  return `#/sessions/${encodeURIComponent(run.session_id)}?source=${encodeURIComponent(run.source)}&agent_id=${encodeURIComponent(run.agent_id)}`;
}

export default async function (root) {
  const range = readRange();
  const since = sinceIso(range);
  const suffix = since ? '?since=' + encodeURIComponent(since) : '';
  const [roleRows, runRows] = await Promise.all([
    api('/api/agents' + suffix), api('/api/agent-runs' + suffix),
  ]);
  const agents = groupByType(roleRows);
  const runs = groupRuns(runRows);

  const totalRuns = runs.length;
  const totalTokens = runs.reduce((s, r) => s + r.total_tokens, 0);
  const totalCost = runs.every(a => a.cost_known)
    ? runs.reduce((s, r) => s + r.cost_usd, 0) : null;

  const rangeTabs = `
    <div class="range-tabs" role="tablist">
      ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === range.key ? 'active' : ''}">${r.label}</button>`).join('')}
    </div>`;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">Agents</h2>
      <span class="muted" style="font-size:12px">${range.days ? `last ${range.days} days` : 'all time'}</span>
      <div class="spacer"></div>
      ${rangeTabs}
    </div>

    <div class="row cols-3">
      <div class="card kpi"><div class="label">Agent types</div><div class="value">${fmt.int(agents.length)}</div></div>
      <div class="card kpi"><div class="label">Subagent runs</div><div class="value">${fmt.int(totalRuns)}</div></div>
      <div class="card kpi"><div class="label">Total tokens (API-equivalent ${totalCost == null ? '' : '$' + totalCost.toFixed(2)})</div><div class="value">${fmt.int(totalTokens)}</div></div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>Tokens by agent type</h3>
      <div id="ch-agents" style="height:320px"></div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>Individual agent runs</h3>
      <p class="muted" style="margin:-4px 0 14px;font-size:12px">Every agent identity gets its own saved token total. Open a run to inspect only that agent's transcript records.</p>
      <table>
        <thead><tr>
          <th>agent</th><th>role</th><th>source</th><th>parent</th>
          <th class="num">calls</th><th class="num">tokens</th><th class="num">reasoning</th>
          <th class="num">peak context</th><th class="num">cost</th><th>last used</th>
        </tr></thead>
        <tbody>${runs.map(run => `<tr>
          <td><a href="${runHref(run)}">${fmt.htmlSafe(run.agent_name || run.agent_id)}</a></td>
          <td><span class="badge">${fmt.htmlSafe(run.agent_type)}</span></td>
          <td><span class="badge">${fmt.htmlSafe(run.source)}</span></td>
          <td class="mono">${fmt.htmlSafe(run.parent_session_id ? fmt.short(run.parent_session_id, 12) : '—')}</td>
          <td class="num">${fmt.int(run.model_calls)}</td><td class="num">${fmt.int(run.total_tokens)}</td>
          <td class="num">${fmt.int(run.reasoning_output_tokens)}</td>
          <td class="num">${run.peak_context_utilization ? fmt.pct(run.peak_context_utilization) : '—'}</td>
          <td class="num">${run.cost_known ? fmt.usd(run.cost_usd) : '—'}</td>
          <td class="mono">${fmt.ts(run.ended)}</td>
        </tr>`).join('') || '<tr><td colspan="10" class="muted">no agent runs in this range</td></tr>'}</tbody>
      </table>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>All agents</h3>
      <p class="muted" style="margin:-4px 0 14px;font-size:12px">Claude sidechains use their <code>meta.json</code> role; Codex child threads use the recorded agent nickname or role.</p>
      <table>
        <thead><tr>
          <th>agent</th>
          <th class="num">runs</th>
          <th class="num">input</th>
          <th class="num">output</th>
          <th class="num">reasoning</th>
          <th class="num">cache read</th>
          <th class="num">cache create</th>
          <th class="num">est. cost</th>
          <th>last used</th>
        </tr></thead>
        <tbody>
          ${agents.map(a => `
            <tr title="models: ${fmt.htmlSafe([...new Set(a.models)].join(', '))}">
              <td><span class="badge">${fmt.htmlSafe(a.agent_type)}</span></td>
              <td class="num">${fmt.int(a.runs)}</td>
              <td class="num">${fmt.int(a.input_tokens)}</td>
              <td class="num">${fmt.int(a.output_tokens)}</td>
              <td class="num">${fmt.int(a.reasoning_output_tokens)}</td>
              <td class="num">${fmt.int(a.cache_read_tokens)}</td>
              <td class="num">${fmt.int(a.cache_create_tokens)}</td>
              <td class="num">${a.cost_known ? '$' + a.cost_usd.toFixed(2) : '<span class="muted">—</span>'}</td>
              <td class="mono">${fmt.ts(a.last_used)}</td>
            </tr>`).join('') || '<tr><td colspan="9" class="muted">no subagent runs in this range</td></tr>'}
        </tbody>
      </table>
    </div>
  `;

  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => writeRange(btn.dataset.range));
  });

  const top = agents.slice(0, 12);
  barChart(document.getElementById('ch-agents'), {
    categories: top.map(t => t.agent_type.length > 26 ? t.agent_type.slice(0, 25) + '…' : t.agent_type),
    values: top.map(t => t.total_tokens),
    color: CHART.series[3],
  });
}
