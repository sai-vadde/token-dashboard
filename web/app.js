// app.js — router, state, fetch helpers

export const $  = (sel, root=document) => root.querySelector(sel);
export const $$ = (sel, root=document) => Array.from(root.querySelectorAll(sel));

const COMPACT = new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 });
export const fmt = {
  int: n => (n ?? 0).toLocaleString(), compact: n => COMPACT.format(n ?? 0),
  usd: n => n == null ? '—' : '$' + Number(n).toFixed(2),
  usd4: n => n == null ? '—' : '$' + Number(n).toFixed(4),
  pct: n => n == null ? '—' : (n * 100).toFixed(0) + '%',
  short: (s, n=80) => s == null ? '' : (s.length > n ? s.slice(0, n - 1) + '…' : s),
  htmlSafe: s => (s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  modelClass: m => {
    const s = (m || '').toLowerCase();
    if (s.includes('opus')) return 'opus'; if (s.includes('sonnet')) return 'sonnet';
    if (s.includes('haiku')) return 'haiku';
    if (s.includes('gpt') || s.includes('codex') || s.startsWith('o')) return 'openai';
    return '';
  },
  modelShort: m => (m || '').replace('claude-', '').replace('openai/', ''),
  ts: t => (t || '').slice(0, 16).replace('T', ' '),
};

export const state = {
  plan: 'api', pricing: null, source: localStorage.getItem('td.source') || 'all',
  platforms: [], enabledSources: [],
};

function withSource(path) {
  if (!path.startsWith('/api/') || path === '/api/plan' || path.startsWith('/api/platforms') ||
      state.source === 'all' || /[?&]source=/.test(path)) return path;
  return path + (path.includes('?') ? '&' : '?') + 'source=' + encodeURIComponent(state.source);
}

export async function api(path, opts) {
  const response = await fetch(withSource(path), opts);
  if (!response.ok) throw new Error(`${path} → ${response.status}`);
  return response.json();
}

const ROUTES = {
  '/overview': () => import('/web/routes/overview.js'), '/prompts': () => import('/web/routes/prompts.js'),
  '/sessions': () => import('/web/routes/sessions.js'), '/projects': () => import('/web/routes/projects.js'),
  '/skills': () => import('/web/routes/skills.js'), '/agents': () => import('/web/routes/agents.js'),
  '/platforms': () => import('/web/routes/platforms.js'), '/tips': () => import('/web/routes/tips.js'),
  '/settings': () => import('/web/routes/settings.js'), '/codex': () => import('/web/routes/codex.js'),
};
const NAV_ROUTES = ['/overview','/prompts','/sessions','/projects','/skills','/agents','/platforms','/tips','/settings'];

function buildTopbar() {
  const wrap = document.createElement('header');
  wrap.className = 'topbar';
  wrap.innerHTML = `<div class="brand">Token Dashboard</div><nav>
    ${NAV_ROUTES.map(p => `<a href="#${p}" data-route="${p}">${p.slice(1)}</a>`).join('')}
    </nav><div class="spacer"></div>
    <label class="source-switch" title="Show all enabled platforms or one platform"><span>source</span>
      <select id="source-select"><option value="all">All enabled</option>
        ${state.platforms.filter(p => p.enabled).map(p => `<option value="${p.source}">${fmt.htmlSafe(p.name)}</option>`).join('')}
      </select></label>
    <span class="pill" id="plan-pill">${state.enabledSources.length} platform${state.enabledSources.length === 1 ? '' : 's'}</span>
    <span class="pill muted" title="Cmd/Ctrl+B blurs sensitive text">⌘B blur</span>`;
  document.body.prepend(wrap);
  $('#source-select').value = state.source;
  $('#source-select').addEventListener('change', event => {
    state.source = event.target.value; localStorage.setItem('td.source', state.source); render();
  });
}

function setActiveTab(routeKey) {
  $$('header.topbar nav a').forEach(a => a.classList.toggle('active', a.dataset.route === routeKey));
}

async function render() {
  const hash = location.hash.replace(/^#/, '') || '/overview';
  const path = hash.split('?')[0];
  let key = path; if (path.startsWith('/sessions/')) key = '/sessions';
  setActiveTab(key === '/codex' ? '/platforms' : key);
  const loader = ROUTES[key] || ROUTES['/overview'];
  const root = $('#app'); root.innerHTML = '';
  try { await (await loader()).default(root); }
  catch (error) { root.innerHTML = `<div class="card"><h2>Error</h2><pre>${fmt.htmlSafe(String(error.stack || error))}</pre></div>`; }
}

function setupCard(platform) {
  const plans = Object.entries(platform.plan_options || {});
  return `<article class="platform-card setup-card" style="--platform:${platform.accent}">
    <label class="platform-toggle"><input type="checkbox" data-enabled="${platform.source}" ${platform.enabled ? 'checked' : ''}> enable</label>
    <div class="platform-mark">${fmt.htmlSafe(platform.mark)}</div><h3>${fmt.htmlSafe(platform.name)}</h3>
    <p>${fmt.htmlSafe(platform.description)}</p><label class="field-label">Your plan
      <select data-plan="${platform.source}">${plans.map(([id,p]) => `<option value="${id}" ${id === platform.plan ? 'selected' : ''}>${fmt.htmlSafe(p.label)}${p.monthly ? ` · $${p.monthly}/mo` : ''}</option>`).join('')}</select>
    </label></article>`;
}

async function firstRun(platformData) {
  if (platformData.configured) return;
  const available = platformData.platforms.filter(p => p.status === 'available');
  const overlay = document.createElement('div'); overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal platform-setup"><p class="eyebrow">FIRST-RUN SETUP</p>
    <h2>Choose the coding platforms you use</h2>
    <p>Only enabled local transcript folders are scanned. You can change platforms and plans later.</p>
    <div class="platform-grid setup-grid">${available.map(setupCard).join('')}</div>
    <div class="actions"><div class="spacer"></div><button class="primary" id="firstsave">Save & scan selected</button></div></div>`;
  document.body.appendChild(overlay);
  await new Promise(resolve => $('#firstsave', overlay).addEventListener('click', async () => {
    const platforms = available.map(p => ({ source: p.source,
      enabled: $(`[data-enabled="${p.source}"]`, overlay).checked,
      plan: $(`[data-plan="${p.source}"]`, overlay).value }));
    await api('/api/platforms', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({platforms}) });
    overlay.remove(); resolve();
  }));
}

async function boot() {
  const planResponse = await api('/api/plan'); state.plan = planResponse.plan; state.pricing = planResponse.pricing;
  let platformData = await api('/api/platforms'); await firstRun(platformData); platformData = await api('/api/platforms');
  state.platforms = platformData.platforms; state.enabledSources = platformData.enabled_sources;
  if (state.source !== 'all' && !state.enabledSources.includes(state.source)) {
    state.source = 'all'; localStorage.setItem('td.source', 'all');
  }
  buildTopbar(); window.addEventListener('hashchange', render); await render();
  window.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'b') {
      event.preventDefault(); document.body.classList.toggle('privacy-on');
    }
  });
  try {
    const events = new EventSource('/api/stream');
    events.onmessage = event => { try { if (JSON.parse(event.data).type === 'scan') render(); } catch {} };
  } catch {}
}

boot();
