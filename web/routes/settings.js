import { api, fmt } from '/web/app.js';

export default async function (root) {
  const [platforms, planData] = await Promise.all([api('/api/platforms'), api('/api/plan')]);
  root.innerHTML = `<div class="row cols-2">
    <div class="card"><h2>Platform settings</h2>
      <p class="muted">Provider enablement, transcript roots, and billing plans are stored locally in SQLite and drive both scanning and “All” totals.</p>
      ${platforms.platforms.filter(p => p.status === 'available').map(p => `<div class="profile-row">
        <span class="badge" style="border-color:${p.accent};color:${p.accent}">${fmt.htmlSafe(p.name)}</span>
        <div><strong>${p.enabled ? 'Enabled' : 'Disabled'}</strong><br><span class="muted">${fmt.htmlSafe(p.plan_label)}${p.subscription_usd ? ` · $${p.subscription_usd}/mo` : ''}</span></div>
      </div>`).join('')}
      <p style="margin:18px 0 0"><a class="platform-open" href="#/platforms">Manage platforms and plans →</a></p>
    </div>
    <div class="card"><h2>Privacy</h2>
      <p class="muted">All transcript parsing and analytics stay local. Disabled providers are not scanned and are excluded from default aggregate views; their historical rows remain available if re-enabled.</p>
      <p class="muted">Press <code>Cmd/Ctrl + B</code> anywhere to blur prompt text and other sensitive content for screenshots.</p>
    </div></div>
    <div class="card" style="margin-top:16px"><h2>Offline pricing table</h2>
      <p class="muted">API-equivalent estimates use the checked-in <code>pricing.json</code>. Subscription fees and token estimates are intentionally displayed separately. Unknown models lower pricing coverage instead of being shown as free.</p>
      <div class="table-scroll"><table><thead><tr><th>model</th><th class="num">input</th><th class="num">cached input</th><th class="num">output</th></tr></thead>
      <tbody>${Object.entries(planData.pricing.models).map(([name,r]) => `<tr><td><span class="badge ${fmt.modelClass(name)}">${fmt.htmlSafe(name)}</span></td>
        <td class="num">$${Number(r.input).toFixed(3)}</td><td class="num">$${Number(r.cache_read).toFixed(3)}</td><td class="num">$${Number(r.output).toFixed(3)}</td></tr>`).join('')}</tbody></table></div>
      <p class="muted" style="font-size:11px">Rates per 1M tokens · ${fmt.htmlSafe(planData.pricing.provenance?.note || '')}</p>
    </div>`;
}
