if (localStorage.getItem('pi_auth') !== '1') { window.location.replace('login.html'); }
function doLogout(){ localStorage.removeItem('pi_auth'); localStorage.removeItem('pi_user'); window.location.replace('login.html'); }

const API = window.location.origin;
let allScans = [];

async function loadHistory() {
  try {
    const res = await fetch(`${API}/api/scans`);
    if (!res.ok) throw new Error('Failed to load');
    allScans = await res.json();
    renderStats();
    renderGrid(allScans);
    document.getElementById('statsBar').style.display = 'flex';
    document.getElementById('filtersRow').style.display = 'flex';
  } catch(e) {
    document.getElementById('mainContent').innerHTML = `
      <div class="state-box">
        <i class="fas fa-triangle-exclamation" style="color:rgba(255,71,87,.3)"></i>
        <p>Could not load history.<br><small style="color:rgba(106,158,192,.4)">${e.message}</small></p>
      </div>`;
  }
}

function renderStats() {
  const projects = new Set(allScans.map(s => s.project_id).filter(Boolean));
  const warns    = allScans.filter(s => (s.safety_warnings||[]).length > 0);
  document.getElementById('statTotal').textContent    = allScans.length;
  document.getElementById('statProjects').textContent = projects.size;
  document.getElementById('statWarns').textContent    = warns.length;
}

function applyFilters() {
  const q    = document.getElementById('searchInput').value.toLowerCase();
  const task = document.getElementById('taskFilter').value;
  const pt   = document.getElementById('panelFilter').value;
  const filtered = allScans.filter(s => {
    const haystack = [s.project_name, s.site, s.panel_type, s.inspector, s.username, s.notes].join(' ').toLowerCase();
    if (q && !haystack.includes(q)) return false;
    if (task && s.task !== task) return false;
    if (pt && !(s.panel_type||'').includes(pt)) return false;
    return true;
  });
  document.getElementById('filterCount').textContent = `${filtered.length} of ${allScans.length} scans`;
  renderGrid(filtered);
}

function panelBadgeClass(pt) {
  const p = (pt||'').toLowerCase();
  if (p.includes('prismaset g') || p.includes('prisma g')) return 'badge-pg';
  if (p.includes('prismaset p') || p.includes('prisma p')) return 'badge-pp';
  if (p.includes('okken'))                                  return 'badge-ok';
  if (p.includes('abb'))                                    return 'badge-ab';
  return 'badge-un';
}

function fmtDate(ts) {
  if (!ts) return '—';
  const d = new Date(ts + (ts.endsWith('Z') ? '' : 'Z'));
  return d.toLocaleString('en-GB', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' });
}

function cap(s){ return s ? s[0].toUpperCase() + s.slice(1) : '—'; }

let _renderedScans = [];

function renderGrid(scans) {
  if (!scans.length) {
    document.getElementById('mainContent').innerHTML = `
      <div class="state-box">
        <i class="fas fa-folder-open"></i>
        <p>No inspections found.<br><small>Try adjusting your filters or run a new inspection.</small></p>
      </div>`;
    return;
  }
  _renderedScans = scans;
  const html = scans.map((s, idx) => {
    const imgSrc   = s.image_path
      ? (s.image_path.startsWith('http') ? s.image_path : `${API}/api/scan-image/${s.image_path}`)
      : null;
    const ptCls    = panelBadgeClass(s.panel_type);
    const projName = s.project_name || 'No project';
    const site     = s.site || s.username || '—';
    const warns    = (s.safety_warnings||[]).length;
    const task     = cap(s.task);
    const date     = fmtDate(s.timestamp);
    const tech     = s.inspector || s.username || '—';

    return `<div class="scan-card" onclick="openDetail(${idx})">
      ${imgSrc
        ? `<img class="scan-thumb" src="${imgSrc}" alt="Panel" loading="lazy" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
        : ''}
      <div class="scan-thumb-ph" ${imgSrc ? 'style="display:none"' : ''}>
        <i class="fas fa-solar-panel"></i>
      </div>
      <div class="scan-body">
        <div class="scan-proj">${esc(projName)}</div>
        <div class="scan-site"><i class="fas fa-location-dot" style="font-size:9px;margin-right:4px"></i>${esc(site)}</div>
        <div class="scan-meta-row">
          <span class="scan-badge ${ptCls}">${esc(s.panel_type||'Unknown')}</span>
          <span class="scan-badge badge-task"><i class="fas fa-list-check" style="font-size:9px"></i>${esc(task)}</span>
          ${warns ? `<span class="scan-badge badge-warn"><i class="fas fa-triangle-exclamation" style="font-size:9px"></i>${warns} warning${warns>1?'s':''}</span>` : ''}
        </div>
        <div class="scan-footer">
          <div class="scan-date"><i class="fas fa-clock" style="font-size:9px;margin-right:3px"></i>${esc(date)}</div>
          <div class="scan-user"><i class="fas fa-user" style="font-size:9px"></i>${esc(tech)}</div>
        </div>
      </div>
    </div>`;
  }).join('');
  document.getElementById('mainContent').innerHTML = `<div class="scans-grid">${html}</div>`;
}

function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

let _currentScan = null;

function openDetail(idx) {
  const scan = _renderedScans[idx];
  if (!scan) return;
  _currentScan = scan;
  const imgSrc = scan.image_path
    ? (scan.image_path.startsWith('http') ? scan.image_path : `${API}/api/scan-image/${scan.image_path}`)
    : null;
  const dateStr = fmtDate(scan.timestamp);
  const warns   = (scan.safety_warnings || []);
  const ptCls   = panelBadgeClass(scan.panel_type);

  document.getElementById('detailTitle').textContent = scan.project_name || 'Inspection Details';

  // Build component counts section
  const hasComponents = (scan.breaker_count || scan.acb_count || scan.mccb_count || scan.mcb_count || scan.cubicle_count);
  const countsHtml = hasComponents ? `
    <div class="detail-section">
      <div class="detail-label">Components Detected</div>
      <div class="detail-counts">
        ${scan.acb_count > 0 ? `<div class="count-chip"><div class="num">${scan.acb_count}</div><div class="lbl">ACB</div></div>` : ''}
        ${scan.mccb_count > 0 ? `<div class="count-chip"><div class="num">${scan.mccb_count}</div><div class="lbl">MCCB</div></div>` : ''}
        ${scan.mcb_count > 0 ? `<div class="count-chip"><div class="num">${scan.mcb_count}</div><div class="lbl">MCB</div></div>` : ''}
        ${scan.cubicle_count > 0 ? `<div class="count-chip"><div class="num">${scan.cubicle_count}</div><div class="lbl">Cubicles</div></div>` : ''}
        ${scan.breaker_count > 0 ? `<div class="count-chip"><div class="num" style="color:var(--green)">${scan.breaker_count}</div><div class="lbl">Total</div></div>` : ''}
        ${scan.busbar_side && scan.busbar_side !== 'unknown' ? `<div class="count-chip"><div class="num" style="font-size:13px;padding-top:4px">${scan.busbar_side.toUpperCase()}</div><div class="lbl">Busbar</div></div>` : ''}
      </div>
    </div>` : '';

  // Warnings
  const warnHtml = warns.length ? `
    <div class="detail-section">
      <div class="detail-label"><i class="fas fa-triangle-exclamation" style="color:var(--amber)"></i> Safety Warnings (${warns.length})</div>
      ${warns.map(w => {
        const crit = w.toLowerCase().includes('arc') || w.toLowerCase().includes('energi') || w.toLowerCase().includes('live') || w.includes('🔥');
        return `<div class="detail-warn-item ${crit ? 'crit' : 'warn'}">${esc(w)}</div>`;
      }).join('')}
    </div>` : '';

  const summaryHtml = scan.result_summary ? `
    <div class="detail-section">
      <div class="detail-label">AI Summary</div>
      <div class="detail-summary">${esc(scan.result_summary)}</div>
    </div>` : '';

  document.getElementById('detailScroll').innerHTML = `
    ${imgSrc
      ? `<img class="detail-img" src="${imgSrc}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
      : ''}
    <div class="detail-img-ph" ${imgSrc ? 'style="display:none"' : ''}>
      <i class="fas fa-solar-panel"></i>
    </div>
    <div class="detail-section" style="margin-top:16px">
      <div class="detail-meta-grid">
        <div class="detail-meta-item"><label>Date</label><div class="val" style="font-size:12px">${esc(dateStr)}</div></div>
        <div class="detail-meta-item"><label>Technician</label><div class="val">${esc(scan.inspector || scan.username || '—')}</div></div>
        <div class="detail-meta-item"><label>Site</label><div class="val">${esc(scan.site || '—')}</div></div>
        <div class="detail-meta-item"><label>Task</label><div class="val">${esc(cap(scan.task))}</div></div>
      </div>
      <div class="detail-badges">
        <span class="scan-badge ${ptCls}">${esc(scan.panel_type || 'Unknown')}</span>
        ${warns.length ? `<span class="scan-badge badge-warn"><i class="fas fa-triangle-exclamation" style="font-size:9px"></i>${warns.length} warning${warns.length > 1 ? 's' : ''}</span>` : '<span class="scan-badge badge-dead"><i class="fas fa-check" style="font-size:9px"></i>No warnings</span>'}
      </div>
    </div>
    ${countsHtml}
    ${warnHtml}
    ${summaryHtml}
  `;

  document.getElementById('detailActions').innerHTML = `
    <a class="detail-btn detail-btn-return" href="index.html?return=${encodeURIComponent(scan.id)}">
      <i class="fas fa-rotate-right"></i> Return Visit — Continue on This Panel
    </a>
    <button class="detail-btn detail-btn-print" onclick="openPrint(_currentScan)">
      <i class="fas fa-print"></i> Print Report
    </button>
  `;

  document.getElementById('detailOverlay').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeDetail(e) {
  if (e && e.target !== document.getElementById('detailOverlay')) return;
  document.getElementById('detailOverlay').classList.remove('open');
  document.body.style.overflow = '';
}

function openPrint(scan) {
  const imgSrc = scan.image_path
    ? (scan.image_path.startsWith('http') ? scan.image_path : `${API}/api/scan-image/${scan.image_path}`)
    : null;
  const date   = new Date((scan.timestamp||'') + (scan.timestamp&&scan.timestamp.endsWith('Z')?'':'Z'));
  const dateStr = date.toLocaleString('en-GB', { dateStyle:'full', timeStyle:'short' });
  const warns  = (scan.safety_warnings||[]);
  const warnRows = warns.map(w => {
    const crit = w.toLowerCase().includes('arc')||w.toLowerCase().includes('energi')||w.toLowerCase().includes('live')||w.includes('🔥');
    return `<div class="warn-item ${crit?'crit':'warn'}">${w}</div>`;
  }).join('');

  const html = `<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Panel Inspection Report — ${scan.project_name||'Unnamed'}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap');
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'Inter',system-ui,sans-serif;color:#0f1923;background:#fff;padding:28px 32px;font-size:12px;line-height:1.5}
  .rpt-header{display:flex;align-items:center;justify-content:space-between;padding-bottom:14px;border-bottom:2.5px solid #3DCD58;margin-bottom:20px}
  .rpt-brand{display:flex;align-items:center;gap:12px}
  .rpt-logo{width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,#3DCD58,#2BAE47);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:900;color:#010912;flex-shrink:0}
  .rpt-brand-name{font-size:15px;font-weight:800;color:#0f1923;letter-spacing:-.2px}
  .rpt-brand-sub{font-size:9px;color:#6A9EC0;text-transform:uppercase;letter-spacing:.6px;margin-top:1px}
  .rpt-date{font-size:10px;color:#555;text-align:right}
  .rpt-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#3DCD58;margin-bottom:2px}
  .proj-box{background:#f7fdf9;border:1px solid #d4f0dc;border-radius:12px;padding:16px 20px;margin-bottom:16px}
  .proj-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
  .pf label{font-size:8.5px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#888;display:block;margin-bottom:3px}
  .pf .val{font-size:12px;font-weight:600;color:#0f1923}
  .photo-box{margin-bottom:16px;border-radius:12px;overflow:hidden;border:1px solid #e0e0e0;text-align:center;background:#f5f5f5}
  .photo-box img{max-width:100%;max-height:360px;object-fit:contain;display:block;margin:0 auto}
  .sec{margin-bottom:16px}
  .sec-ttl{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.9px;color:#888;margin-bottom:8px;padding-bottom:5px;border-bottom:1px solid #e8e8e8}
  .badge-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
  .badge{padding:4px 12px;border-radius:14px;font-size:11px;font-weight:700;border:1px solid}
  .badge-g{color:#008A2E;border-color:#b3e6c4;background:#f0fbf4}
  .badge-b{color:#1565c0;border-color:#b3d4f7;background:#f0f7ff}
  .badge-p{color:#6a1b9a;border-color:#d7b3f5;background:#fdf4ff}
  .badge-n{color:#555;border-color:#ddd;background:#f9f9f9}
  .int-row{display:flex;gap:10px;margin-bottom:8px}
  .int-chip{padding:5px 14px;border-radius:8px;font-size:11px;font-weight:700}
  .int-task{background:#f0f7ff;border:1px solid #b3d4f7;color:#1565c0}
  .warn-item{padding:8px 12px;border-radius:8px;margin-bottom:5px;font-size:11px;line-height:1.55}
  .warn-item.crit{background:#fff4f4;border:1px solid #ffc5c5;color:#b00}
  .warn-item.warn{background:#fffbf0;border:1px solid #ffe0a0;color:#7a4f00}
  .rpt-footer{margin-top:24px;padding-top:12px;border-top:1px solid #e0e0e0;font-size:9.5px;color:#888;display:flex;justify-content:space-between}
  @media print{body{padding:16px 20px}@page{margin:16mm}}
</style></head><body>
<div class="rpt-header">
  <div class="rpt-brand">
    <div class="rpt-logo">SE</div>
    <div><div class="rpt-brand-name">Panel Inspector Pro</div><div class="rpt-brand-sub">EcoStruxure™ AI · Schneider Electric</div></div>
  </div>
  <div class="rpt-date"><div class="rpt-title">Inspection Report</div><div>${dateStr}</div></div>
</div>
<div class="proj-box">
  <div class="proj-grid">
    <div class="pf"><label>Project / Work Order</label><div class="val">${scan.project_name||'—'}</div></div>
    <div class="pf"><label>Site / Location</label><div class="val">${scan.site||'—'}</div></div>
    <div class="pf"><label>Technician</label><div class="val">${scan.inspector||scan.username||'—'}</div></div>
    <div class="pf"><label>Task</label><div class="val">${cap(scan.task)}</div></div>
  </div>
</div>
${imgSrc ? `<div class="photo-box"><img src="${imgSrc}" alt="Panel photo"></div>` : ''}
<div class="sec">
  <div class="sec-ttl">Panel Identification</div>
  <div class="badge-row"><span class="badge badge-g">${scan.panel_type||'Unknown'}</span>
  ${scan.busbar_side && scan.busbar_side !== 'unknown' ? `<span class="badge badge-n">VBB: ${scan.busbar_side}</span>` : ''}
  </div>
  ${scan.result_summary ? `<p style="font-size:11px;color:#555;line-height:1.6;margin-top:6px">${scan.result_summary}</p>` : ''}
  ${scan.notes ? `<p style="font-size:12px;color:#444;line-height:1.7;margin-top:6px">${scan.notes}</p>` : ''}
</div>
${warns.length ? `<div class="sec"><div class="sec-ttl">Safety Warnings</div>${warnRows}</div>` : ''}
<div class="rpt-footer">
  <span>Panel Inspector Pro · Schneider Electric · Internal Use Only</span>
  <span>Generated ${dateStr}</span>
</div>
<script>setTimeout(()=>window.print(),600);<\/script>
</body></html>`;

  const w = window.open('','_blank');
  if (w) { w.document.write(html); w.document.close(); }
  else alert('Please allow pop-ups to generate the PDF.');
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDetail(); });

loadHistory();
