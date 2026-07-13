import { api, fmt } from '/web/app.js';

const RANGES = [
  { key: '7d', label: '7d', days: 7 },
  { key: '30d', label: '30d', days: 30 },
  { key: '90d', label: '90d', days: 90 },
  { key: 'all', label: 'All', days: null },
];

function readRange() {
  const params = new URLSearchParams((location.hash.split('?')[1] || ''));
  return RANGES.find(range => range.key === params.get('range')) || RANGES[1];
}

function sinceIso(range) {
  return range.days ? new Date(Date.now() - range.days * 86400 * 1000).toISOString() : null;
}

function endpoint(path, since) {
  const params = new URLSearchParams({ source: 'codex' });
  if (since) params.set('since', since);
  return `${path}?${params}`;
}

function duration(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.round((ms % 60000) / 1000);
  return `${minutes}m ${seconds}s`;
}

function contextPct(value) {
  if (value == null) return '—';
  const percent = Number(value) * 100;
  return `${percent > 0 && percent < 1 ? percent.toFixed(1) : percent.toFixed(0)}%`;
}

function resetAt(epoch) {
  return epoch ? new Date(epoch * 1000).toLocaleString() : 'unknown';
}

function meter(label, used, windowMinutes, resetsAt) {
  if (used == null) return '';
  const value = Math.max(0, Math.min(100, Number(used)));
  const tone = value >= 90 ? 'bad' : value >= 70 ? 'warn' : 'good';
  return `<div class="quota-meter">
    <div class="flex"><strong>${fmt.htmlSafe(label)}</strong><span class="spacer"></span><span class="mono">${value.toFixed(0)}%</span></div>
    <div class="meter-track"><span class="${tone}" style="width:${value}%"></span></div>
    <div class="muted" style="font-size:11px">${fmt.int(windowMinutes)} minute window · resets ${fmt.htmlSafe(resetAt(resetsAt))}</div>
  </div>`;
}

function chips(values) {
  return Object.entries(values || {}).map(([name, count]) =>
    `<span class="badge" title="${fmt.htmlSafe(name)}">${fmt.htmlSafe(fmt.short(name, 36))} · ${fmt.int(count)}</span>`
  ).join(' ') || '<span class="muted">no data</span>';
}

export default async function (root) {
  const range = readRange();
  const since = sinceIso(range);
  const [summary, turns, limits, pipelines] = await Promise.all([
    api(endpoint('/api/codex/summary', since)),
    api(endpoint('/api/codex/turns', since) + '&limit=100'),
    api('/api/codex/rate-limits?source=codex&limit=1'),
    api('/api/pipelines'),
  ]);
  const latestLimit = limits[0] || null;
  const codexPipeline = pipelines.find(item => item.source === 'codex');
  const kpi = (label, value, title='') => `<div class="card kpi"><div class="label">${label}</div><div class="value" title="${fmt.htmlSafe(title)}">${value}</div></div>`;

  root.innerHTML = `
    <div class="flex" style="margin-bottom:14px">
      <div>
        <h2 style="margin:0;font-size:16px">Codex pipeline</h2>
        <div class="muted" style="font-size:12px">native lifecycle, reasoning, context, policies, and quota telemetry</div>
      </div>
      <div class="spacer"></div>
      <div class="range-tabs" role="tablist">
        ${RANGES.map(item => `<button data-range="${item.key}" class="${item.key === range.key ? 'active' : ''}">${item.label}</button>`).join('')}
      </div>
    </div>

    <div class="pipeline-banner">
      <span class="badge openai">custom source pipeline</span>
      <span>Canonical messages, tools, agents, and costs still feed global views.</span>
      <span>Codex-only events stay isolated here.</span>
      ${codexPipeline ? `<span class="muted">${codexPipeline.features.length} capabilities · ${codexPipeline.replay_changed_files ? 'context replay' : 'incremental'}</span>` : ''}
    </div>

    <div class="row metrics-grid" style="margin-top:16px">
      ${kpi('Turns', fmt.int(summary.turns), 'Logical Codex task turns')}
      ${kpi('Completed', fmt.int(summary.completed_turns))}
      ${kpi('Model calls', fmt.int(summary.model_calls))}
      ${kpi('Avg duration', duration(summary.avg_duration_ms))}
      ${kpi('Avg TTFT', duration(summary.avg_ttft_ms), 'Time to first token')}
      ${kpi('Reasoning', fmt.compact(summary.reasoning_output_tokens), `${fmt.int(summary.reasoning_output_tokens)} tokens`)}
      ${kpi('Peak context', contextPct(summary.peak_context_utilization))}
      ${kpi('Agent runs', fmt.int(summary.agent_runs))}
      ${kpi('Tool errors', fmt.int(summary.tool_errors), `${fmt.int(summary.tool_calls)} total tool calls`)}
      ${kpi('API equivalent', `${summary.financial?.is_lower_bound ? '≥' : ''}${fmt.usd(summary.financial?.api_equivalent_usd)}`, `${fmt.pct(summary.financial?.pricing_coverage)} pricing coverage`)}
      ${kpi('Codex credits', summary.credits?.estimated_credits == null ? '—' : fmt.int(summary.credits.estimated_credits), 'Published Codex token rate-card estimate when the model is covered')}
      ${kpi('Cache reads', fmt.compact(summary.cache?.read_tokens), `${fmt.int(summary.cache?.read_events)} calls used cached input`)}
      ${kpi('Cache savings', fmt.usd(summary.cache?.savings_usd), 'Estimated versus uncached API input rates')}
      ${kpi('Cache writes', summary.cache?.create_tokens == null ? '—' : fmt.compact(summary.cache.create_tokens), summary.cache?.create_tokens == null ? 'Codex token events do not report cache creation' : 'Provider-reported writes')}
    </div>

    <div class="row cols-2" style="margin-top:16px">
      <div class="card">
        <h3>Current Codex limits</h3>
        <p class="muted" style="margin:-4px 0 12px;font-size:11px">Configured billing: <strong>${fmt.htmlSafe(summary.plan_label || summary.plan || 'not set')}</strong>${summary.subscription_usd ? ` · $${summary.subscription_usd}/mo` : ''} · <a href="#/platforms">change</a></p>
        ${latestLimit ? `
          <div class="muted" style="margin:-4px 0 12px;font-size:11px">${fmt.htmlSafe(latestLimit.plan_type || 'unknown')} plan · snapshot ${fmt.ts(latestLimit.timestamp)}</div>
          ${meter('Primary', latestLimit.primary_used_percent, latestLimit.primary_window_minutes, latestLimit.primary_resets_at)}
          ${meter('Secondary', latestLimit.secondary_used_percent, latestLimit.secondary_window_minutes, latestLimit.secondary_resets_at)}
          ${latestLimit.rate_limit_reached_type ? `<p class="bad-text">Reached: ${fmt.htmlSafe(latestLimit.rate_limit_reached_type)}</p>` : ''}
        ` : '<p class="muted">No rate-limit snapshot has been written yet.</p>'}
      </div>
      <div class="card">
        <h3>Execution profile</h3>
        <div class="profile-row"><span class="muted">Reasoning effort</span><div>${chips(summary.efforts)}</div></div>
        <div class="profile-row"><span class="muted">Approval policy</span><div>${chips(summary.approval_policies)}</div></div>
        <div class="profile-row"><span class="muted">Sandbox</span><div>${chips(summary.sandbox_policies)}</div></div>
        <div class="profile-row"><span class="muted">Collaboration</span><div>${chips(summary.collaboration_modes)}</div></div>
        <p class="muted" style="margin:14px 0 0;font-size:11px">Policies are recorded per turn, so changes inside one session remain visible.</p>
      </div>
    </div>

    <div class="card" style="margin-top:16px">
      <h3>Codex logical turns</h3>
      <p class="muted" style="margin:-4px 0 12px;font-size:12px">One row per task lifecycle, aggregating all model and tool calls caused by that turn.</p>
      <div class="table-scroll"><table>
        <thead><tr><th>last event</th><th>model</th><th>effort</th><th>status</th><th class="num">duration</th><th class="num">TTFT</th><th class="num">calls</th><th class="num">tools</th><th class="num">tokens</th><th class="num">reasoning</th><th class="num">context</th></tr></thead>
        <tbody>${turns.map(turn => `<tr>
          <td class="mono"><a href="#/sessions/${encodeURIComponent(turn.session_id)}?source=codex">${fmt.ts(turn.last_event_at)}</a></td>
          <td><span class="badge openai">${fmt.htmlSafe(fmt.modelShort(turn.model || 'unknown'))}</span></td>
          <td>${fmt.htmlSafe(turn.effort || '—')}</td><td><span class="badge">${fmt.htmlSafe(turn.status || 'unknown')}</span></td>
          <td class="num">${duration(turn.duration_ms)}</td><td class="num">${duration(turn.time_to_first_token_ms)}</td>
          <td class="num">${fmt.int(turn.model_calls)}</td><td class="num">${fmt.int(turn.tool_calls)}${turn.tool_errors ? ` <span class="bad-text">(${turn.tool_errors})</span>` : ''}</td>
          <td class="num">${fmt.int(turn.total_tokens)}</td><td class="num">${fmt.int(turn.reasoning_output_tokens)}</td>
          <td class="num">${turn.peak_context_utilization ? contextPct(turn.peak_context_utilization) : '—'}</td>
        </tr>`).join('') || '<tr><td colspan="11" class="muted">No Codex turns in this range. Run a scan after using Codex.</td></tr>'}</tbody>
      </table></div>
    </div>
  `;

  root.querySelectorAll('[data-range]').forEach(button => button.addEventListener('click', () => {
    location.hash = '#/codex?range=' + encodeURIComponent(button.dataset.range);
  }));
}
