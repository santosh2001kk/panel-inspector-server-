const API = window.location.origin;

/* ── Auth guard ── */
if (localStorage.getItem('pi_auth') !== '1') { window.location.replace('login.html'); }

function doLogout(){
  localStorage.removeItem('pi_auth');
  localStorage.removeItem('pi_user');
  window.location.replace('login.html');
}

/* ── Greeting ── */
(function(){
  const user = localStorage.getItem('pi_user') || 'Technician';
  const name = user.charAt(0).toUpperCase() + user.slice(1);
  const h = new Date().getHours();
  const word = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  document.getElementById('greetingWord').textContent = word;
  document.getElementById('greetingName').textContent = name;
  document.getElementById('navUser').textContent = name;
  document.getElementById('navAvatar').textContent = name.charAt(0).toUpperCase();
})();

/* ── SLD card scroll anchor ── */
document.querySelector('a[href="index.html#sld-compare"]').addEventListener('click', e => {
  sessionStorage.setItem('pi_scroll_to', 'sld-compare');
});

/* ── Load stats + recent ── */
(async function(){
  try {
    const res = await fetch(`${API}/api/scans`);
    if (!res.ok) throw new Error('fetch failed');
    const scans = await res.json();

    /* Stats */
    const total = scans.length;
    const projects = new Set(scans.map(s => s.project_name || s.project || '').filter(Boolean)).size;
    const warns = scans.filter(s => {
      const r = s.result_json || s.result || {};
      const warns = r.safety_warnings || r.warnings || [];
      return warns.length > 0;
    }).length;
    const panelTypes = new Set(scans.map(s => s.panel_type).filter(Boolean)).size;

    animateNum('statTotal', total);
    animateNum('statProjects', projects);
    animateNum('statWarns', warns);
    animateNum('statPanelTypes', panelTypes);

    document.querySelectorAll('.stat-loading').forEach(el => el.classList.remove('stat-loading'));

    /* Recent (last 3) */
    const recent = [...scans].sort((a,b) => new Date(b.created_at||0) - new Date(a.created_at||0)).slice(0,3);
    renderRecent(recent);

  } catch(e) {
    ['statTotal','statProjects','statWarns','statPanelTypes'].forEach(id => {
      const el = document.getElementById(id);
      if (el) { el.textContent = '—'; el.classList.remove('stat-loading'); }
    });
    document.getElementById('recentGrid').innerHTML = `
      <div class="empty-state">
        <i class="fas fa-circle-exclamation" style="color:rgba(255,71,87,.3)"></i>
        <p>Could not load data — server may be warming up.</p>
      </div>`;
  }
})();

function animateNum(id, target){
  const el = document.getElementById(id);
  if (!el) return;
  let cur = 0;
  const step = Math.max(1, Math.ceil(target / 20));
  const t = setInterval(() => {
    cur = Math.min(cur + step, target);
    el.textContent = cur;
    if (cur >= target) clearInterval(t);
  }, 40);
}

function renderRecent(scans){
  const grid = document.getElementById('recentGrid');
  if (!scans.length){
    grid.innerHTML = `
      <div class="empty-state">
        <i class="fas fa-search"></i>
        <p>No inspections yet — start your first one above.</p>
      </div>`;
    return;
  }
  grid.innerHTML = scans.map(s => {
    const proj = s.project_name || s.project || 'Unnamed Project';
    const site = s.site_name || s.site || '';
    const type = s.panel_type || '';
    const date = s.created_at ? new Date(s.created_at).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'}) : '';
    const imgUrl = s.image_url ? `${API}/api/scan-image/${encodeURIComponent(s.image_url.split('/').pop())}` : null;
    const thumb = imgUrl
      ? `<img class="recent-thumb" src="${imgUrl}" alt="${proj}" loading="lazy" onerror="this.outerHTML='<div class=\\'recent-thumb-ph\\'><i class=\\'fas fa-bolt\\'></i></div>'">`
      : `<div class="recent-thumb-ph"><i class="fas fa-bolt"></i></div>`;
    return `
      <a class="recent-card" href="history.html">
        ${thumb}
        <div class="recent-body">
          <div class="recent-proj">${esc(proj)}</div>
          <div class="recent-site">${esc(site) || '&nbsp;'}</div>
          <div class="recent-meta">
            <span class="recent-type">${esc(type) || 'Unknown'}</span>
            <span class="recent-date">${date}</span>
          </div>
        </div>
      </a>`;
  }).join('');
}

function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;') }
