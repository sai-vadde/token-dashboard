import { api, fmt, $ } from '/web/app.js';

function money(financial) {
  const value = financial?.api_equivalent_usd;
  return `${financial?.is_lower_bound && value != null ? '≥' : ''}${fmt.usd(value)}`;
}

function card(platform, rank, editable) {
  const fin = platform.financial || {};
  const cache = platform.cache || {};
  const plans = Object.entries(platform.plan_options || {});
  const disabled = platform.status !== 'available';
  return `<article class="platform-card ${disabled ? 'coming-soon' : ''}" style="--platform:${platform.accent}" tabindex="0">
    <div class="platform-rank">${String(rank).padStart(2, '0')}</div>
    <div class="platform-card-top"><div class="platform-mark">${fmt.htmlSafe(platform.mark)}</div>
      <span class="status-dot ${platform.enabled ? 'enabled' : ''}">${disabled ? 'coming soon' : platform.enabled ? 'enabled' : 'off'}</span></div>
    <h3>${fmt.htmlSafe(platform.name)}</h3><p>${fmt.htmlSafe(platform.description)}</p>
    ${disabled ? '' : `<div class="platform-stats">
      <div><span>API equivalent</span><b>${money(fin)}</b></div>
      <div><span>tokens</span><b>${fmt.compact(platform.tokens || 0)}</b></div>
      <div><span>cache saved</span><b>${fmt.usd(cache.savings_usd)}</b></div>
      <div><span>cache writes</span><b>${cache.create_tokens == null ? 'not reported' : fmt.compact(cache.create_tokens)}</b></div>
    </div>`}
    ${editable && !disabled ? `<div class="platform-controls">
      <label class="platform-toggle"><input type="checkbox" data-enabled="${platform.source}" ${platform.enabled ? 'checked' : ''}> scan this platform</label>
      <label class="field-label">Plan<select data-plan="${platform.source}">
        ${plans.map(([id,p]) => `<option value="${id}" ${id === platform.plan ? 'selected' : ''}>${fmt.htmlSafe(p.label)}${p.monthly ? ` · $${p.monthly}/mo` : ''}</option>`).join('')}
      </select></label></div>` : ''}
    ${platform.route && platform.enabled ? `<a class="platform-open" href="#${platform.route}">Open ${fmt.htmlSafe(platform.name)} analytics →</a>` : ''}
  </article>`;
}

export default async function (root) {
  const data = await api('/api/platforms');
  const available = data.platforms.filter(p => p.status === 'available');
  const upcoming = data.platforms.filter(p => p.status !== 'available');
  const all = data.all;
  root.innerHTML = `<section class="platform-hero">
    <p class="eyebrow">ALL ENABLED PLATFORMS</p><h1>Your AI coding footprint</h1>
    <p>Choose only the local tools you use. “All” reconciles token consumption, API-equivalent cost, subscriptions and cache savings across those enabled providers.</p>
    <div class="platform-hero-metrics">
      <div><span>API equivalent</span><b>${all.financial.is_lower_bound ? '≥' : ''}${fmt.usd(all.financial.api_equivalent_usd)}</b><small>${fmt.pct(all.financial.pricing_coverage)} priced</small></div>
      <div><span>Monthly plans</span><b>${fmt.usd(all.monthly_subscriptions_usd)}</b><small>commitment, not token spend</small></div>
      <div><span>Total tokens</span><b>${fmt.compact((all.input_tokens||0)+(all.output_tokens||0)+(all.cache_read_tokens||0))}</b><small>${fmt.int(all.sessions)} sessions</small></div>
      <div><span>Cache savings</span><b>${fmt.usd(all.cache.savings_usd)}</b><small>vs uncached input</small></div>
    </div></section>
    <div class="section-heading"><div><p class="eyebrow">YOUR PLATFORMS</p><h2>Pick, price and track</h2></div>
      <button class="primary" id="save-platforms">Save & scan</button></div>
    <div class="platform-grid">${available.map((p,i) => card(p,i+1,true)).join('')}</div>
    <p id="platform-message" class="muted"></p>
    <div class="section-heading upcoming-heading"><div><p class="eyebrow">ADAPTER ROADMAP</p><h2>Available next</h2></div></div>
    <div class="platform-grid">${upcoming.map((p,i) => card(p,available.length+i+1,false)).join('')}</div>
    <div class="card telemetry-note"><h3>Why Codex cache writes can show “not reported”</h3>
      <p>Codex token events report cached input reads. They do not currently expose a cache-write token or event field, so the dashboard does not reverse-invent one. A read can come from a cache created before the scanned task. Savings are an API-rate estimate from observed reads.</p></div>`;

  $('#save-platforms').addEventListener('click', async () => {
    const platforms = available.map(p => ({ source:p.source,
      enabled:$(`[data-enabled="${p.source}"]`).checked,
      plan:$(`[data-plan="${p.source}"]`).value }));
    const message = $('#platform-message'); message.textContent = 'Saving and scanning local transcripts…';
    try {
      await api('/api/platforms', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({platforms})});
      message.textContent = 'Saved. Reloading enabled platform navigation…'; message.style.color = 'var(--good)';
      setTimeout(() => location.reload(), 250);
    } catch (error) { message.textContent = String(error); message.style.color = 'var(--bad)'; }
  });
}
