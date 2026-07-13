import { api, fmt, state } from '/web/app.js';
import { barChart, donutChart, groupedBarChart, stackedBarChart, CHART } from '/web/charts.js';

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
  const base = (location.hash.replace(/^#/, '').split('?')[0]) || '/overview';
  location.hash = '#' + base + '?range=' + encodeURIComponent(key);
}

function sinceIso(range) {
  if (!range.days) return null;
  return new Date(Date.now() - range.days * 86400 * 1000).toISOString();
}

function withSince(url, since) {
  if (!since) return url;
  return url + (url.includes('?') ? '&' : '?') + 'since=' + encodeURIComponent(since);
}

export default async function (root) {
  const range = readRange();
  const since = sinceIso(range);

  const [totals, projects, sessions, tools, daily, byModel, sources, platformData] = await Promise.all([
    api(withSince('/api/overview', since)),
    api(withSince('/api/projects', since)),
    api(withSince('/api/sessions?limit=10', since)),
    api(withSince('/api/tools', since)),
    api(withSince('/api/daily', since)),
    api(withSince('/api/by-model', since)),
    api(withSince('/api/sources', since)),
    api(withSince('/api/platforms', since)),
  ]);

  const cacheCreate =
    (totals.cache_create_5m_tokens || 0) +
    (totals.cache_create_1h_tokens || 0);
  const contextPeak = totals.peak_context_utilization || 0;
  const selectedPlatform = platformData.platforms.find(p => p.source === state.source);
  const scopeSummary = state.source === 'all' ? platformData.all : selectedPlatform;
  const financial = scopeSummary?.financial || totals.financial || {};
  const cacheSummary = scopeSummary?.cache || {};
  const cacheCreateKnown = state.source === 'all'
    ? cacheSummary.create_telemetry_complete
    : selectedPlatform?.capabilities?.cache_create === 'reported';
  const subscriptionUsd = state.source === 'all'
    ? scopeSummary?.monthly_subscriptions_usd
    : selectedPlatform?.subscription_usd;
  const cacheCreateDisplay = cacheCreateKnown
    ? fmt.compact(cacheCreate)
    : state.source === 'all' && cacheCreate > 0 ? `${fmt.compact(cacheCreate)}*` : '—';

  const kpi = (label, compactVal, fullVal, cls = '') => `
    <div class="card kpi ${cls}">
      <div class="label">${label}</div>
      <div class="value" title="${fullVal}">${compactVal}</div>
    </div>`;

  const rangeTabs = `
    <div class="range-tabs" role="tablist">
      ${RANGES.map(r => `<button data-range="${r.key}" class="${r.key === range.key ? 'active' : ''}">${r.label}</button>`).join('')}
    </div>`;

  const sourceComparison = state.source === 'all' && sources.length > 1 ? `
    <div class="card" style="margin-top:16px">
      <h3>Platform comparison</h3>
      <p class="muted" style="margin:-4px 0 10px;font-size:12px">Shared metrics stay comparable; provider-native metrics appear when the transcript exposes them.</p>
      <table>
        <thead><tr><th>source</th><th class="num">sessions</th><th class="num">tokens</th><th class="num">API equiv.</th><th class="num">cache saved</th><th class="num">cache writes</th><th class="num">tools</th></tr></thead>
        <tbody>${sources.map(s => `<tr>
          <td><span class="badge">${fmt.htmlSafe(s.source)}</span></td>
          <td class="num">${fmt.int(s.sessions)}</td><td class="num">${fmt.int(s.tokens)}</td>
          <td class="num">${s.financial?.is_lower_bound ? '≥' : ''}${fmt.usd(s.financial?.api_equivalent_usd)}</td>
          <td class="num">${fmt.usd(s.cache?.savings_usd)}</td>
          <td class="num">${s.cache?.create_tokens == null ? 'not reported' : fmt.int(s.cache.create_tokens)}</td>
          <td class="num">${fmt.int(s.tool_calls)}</td>
        </tr>`).join('')}</tbody>
      </table>
    </div>` : '';

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <h2 style="margin:0;font-size:16px;letter-spacing:-0.01em">Overview</h2>
      <span class="muted" style="font-size:12px">${range.days ? `last ${range.days} days` : 'all time'}</span>
      <div class="spacer"></div>
      ${rangeTabs}
    </div>

    <div class="row metrics-grid">
      ${kpi('Sessions',     fmt.int(totals.sessions),       fmt.int(totals.sessions))}
      ${kpi('Turns',        fmt.int(totals.turns),          fmt.int(totals.turns))}
      ${kpi('Model calls',  fmt.int(totals.model_calls),    fmt.int(totals.model_calls))}
      ${kpi('Input',        fmt.compact(totals.input_tokens),       fmt.int(totals.input_tokens) + ' tokens')}
      ${kpi('Output',       fmt.compact(totals.output_tokens),      fmt.int(totals.output_tokens) + ' tokens')}
      ${kpi('Cache read',   fmt.compact(totals.cache_read_tokens),  fmt.int(totals.cache_read_tokens) + ' tokens')}
      ${kpi('Explicit cache writes', cacheCreateDisplay, cacheCreateKnown ? fmt.int(cacheCreate) + ' reported tokens' : state.source === 'all' ? 'Partial total: at least one enabled provider does not report cache writes' : 'This provider does not report cache-write events')}
      ${kpi('Cache savings', fmt.usd(cacheSummary.savings_usd), 'Estimated savings versus uncached input pricing')}
      ${kpi('Reasoning',    fmt.compact(totals.reasoning_output_tokens), fmt.int(totals.reasoning_output_tokens) + ' output tokens')}
      ${kpi('Peak context', fmt.pct(contextPeak), fmt.pct(contextPeak) + ' of the context window')}
      <div class="card kpi cost">
        <div class="label">API-equivalent cost</div>
        <div class="value" title="${fmt.pct(financial.pricing_coverage)} of model calls priced">${financial.is_lower_bound && financial.api_equivalent_usd != null ? '≥' : ''}${fmt.usd(financial.api_equivalent_usd)}</div>
        <div class="sub">${fmt.pct(financial.pricing_coverage)} pricing coverage</div>
      </div>
      ${kpi('Monthly plans', fmt.usd(subscriptionUsd), 'Configured subscription commitment; separate from token usage')}
    </div>

    <details class="card glossary" style="margin-top:16px">
      <summary><h3 style="display:inline-block;margin:0">What do these numbers mean?</h3><span class="muted" style="font-size:12px">— click to expand</span></summary>
      <dl>
        <dt>Session</dt><dd>One local coding-assistant run. Claude stores project JSONL files; Codex stores dated session JSONL files.</dd>
        <dt>Turn</dt><dd>One message you sent to the assistant. Each turn can trigger one or more model responses and tool calls.</dd>
        <dt>Input tokens</dt><dd>The new text you and tool results sent to the model this turn. Billed at the input rate when pricing is known.</dd>
        <dt>Output tokens</dt><dd>The text the model wrote back. Usually the biggest cost driver per turn.</dd>
        <dt>Reasoning tokens</dt><dd>Provider-reported internal reasoning output. Codex exposes this as part of output; sources that do not report it show zero.</dd>
        <dt>Peak context</dt><dd>The fullest model request: new input plus cached input divided by the provider-reported context window.</dd>
        <dt>Cache read</dt><dd>Tokens the model re-used from a cache, such as instructions, previously-read files, or conversation context. High cache-read counts usually mean better cost hygiene.</dd>
        <dt>Cache write</dt><dd>A provider-reported cache creation. Claude exposes write buckets; current Codex token events expose reads but not writes, so Codex shows unavailable instead of a false zero.</dd>
        <dt>API equivalent</dt><dd>A token-rate estimate for comparing workloads. It is not the same as a ChatGPT or Claude subscription charge. Unknown models reduce the displayed pricing coverage.</dd>
      </dl>
    </details>

    ${sourceComparison}

    <div class="row cols-2" style="margin-top:16px">
      <div class="card">
        <h3>Your daily work</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:12px">Tokens counted as billable work: what you sent (<b>input</b>), what the model wrote (<b>output</b>), and what got stored for re-use (<b>cache create</b>).</p>
        <div id="ch-daily-billable" style="height:260px"></div>
      </div>
      <div class="card">
        <h3>Daily cache reads</h3>
        <p class="muted" style="margin:-4px 0 10px;font-size:12px"><b>Cache reads</b> are re-used context tokens. High numbers here are usually a good thing.</p>
        <div id="ch-daily-cache" style="height:260px"></div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card"><h3>Tokens by project</h3><div id="ch-projects" style="height:320px"></div></div>
      <div class="card">
        <h3>Token usage by model</h3>
        <p class="muted" style="margin:-4px 0 4px;font-size:12px">Share of billable tokens per model.</p>
        <div id="ch-model" style="height:300px"></div>
      </div>
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card"><h3>Top tools (by call count)</h3><div id="ch-tools" style="height:320px"></div></div>
      <div class="card">
        <h3 style="display:flex;align-items:center"><span>Recent sessions</span><span class="spacer"></span><a href="#/sessions" style="font-weight:400;font-size:12px">all →</a></h3>
        <table>
          <thead><tr><th>started</th><th>project</th><th class="num">tokens</th></tr></thead>
          <tbody>
            ${sessions.map(s => `
              <tr>
                <td class="mono">${fmt.ts(s.started)}</td>
                <td><a href="#/sessions/${encodeURIComponent(s.session_id)}${s.source ? `?source=${encodeURIComponent(s.source)}` : ''}">${fmt.htmlSafe(s.project_name || s.project_slug)}</a></td>
                <td class="num">${fmt.compact(s.tokens)}</td>
              </tr>`).join('') || '<tr><td colspan="3" class="muted">no sessions in this range</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
  `;

  // range buttons
  root.querySelectorAll('.range-tabs button').forEach(btn => {
    btn.addEventListener('click', () => writeRange(btn.dataset.range));
  });

  // Your daily work — billable tokens (input + output + cache create)
  stackedBarChart(document.getElementById('ch-daily-billable'), {
    categories: daily.map(d => d.day),
    series: [
      { name: 'input',        values: daily.map(d => d.input_tokens),        color: CHART.input },
      { name: 'output',       values: daily.map(d => d.output_tokens),       color: CHART.output },
      { name: 'cache create', values: daily.map(d => d.cache_create_tokens), color: CHART.cacheCreate },
    ],
  });

  // Daily cache reads (separate — scale is 100× larger)
  stackedBarChart(document.getElementById('ch-daily-cache'), {
    categories: daily.map(d => d.day),
    series: [
      { name: 'cache read', values: daily.map(d => d.cache_read_tokens), color: CHART.cacheRead },
    ],
  });

  // by-model doughnut
  donutChart(document.getElementById('ch-model'),
    byModel.map(m => ({
      name: fmt.modelShort(m.model) || 'unknown',
      value: (m.input_tokens || 0) + (m.output_tokens || 0)
           + (m.cache_create_5m_tokens || 0) + (m.cache_create_1h_tokens || 0),
    })).filter(d => d.value > 0),
  );

  // tokens by project — input vs output
  const topProjects = projects.slice(0, 8);
  groupedBarChart(document.getElementById('ch-projects'), {
    categories: topProjects.map(p => {
      const name = p.project_name || p.project_slug;
      return name.length > 20 ? name.slice(0, 19) + '…' : name;
    }),
    series: [
      { name: 'input',  values: topProjects.map(p => p.input_tokens  || 0), color: CHART.input },
      { name: 'output', values: topProjects.map(p => p.output_tokens || 0), color: CHART.output },
    ],
  });

  // top tools
  const topTools = tools.slice(0, 8);
  barChart(document.getElementById('ch-tools'), {
    categories: topTools.map(t => t.tool_name),
    values: topTools.map(t => t.calls),
    color: CHART.primary,
  });
}
