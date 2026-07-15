/* ═══════════════ AUTH GUARD ═══════════════ */
if (localStorage.getItem('pi_auth') !== '1') {
  window.location.replace('login.html');
} else {
  document.addEventListener('DOMContentLoaded', function() {
    const username = localStorage.getItem('pi_user') || '';
    const uEl = document.getElementById('navUser');
    const aEl = document.getElementById('navAvatarLetter');
    const tEl = document.getElementById('projTech');
    if (uEl) uEl.textContent = username;
    if (aEl) aEl.textContent = username ? username[0].toUpperCase() : 'T';
    if (tEl && username) tEl.value = username;

    const returnId = new URLSearchParams(window.location.search).get('return');
    if (returnId) applyReturnVisit(returnId);
  });

  async function applyReturnVisit(scanId) {
    try {
      const res = await fetch(`${window.location.origin}/api/scans/${encodeURIComponent(scanId)}`);
      if (!res.ok) return;
      const scan = await res.json();

      // Pre-fill project fields
      const pn = document.getElementById('projName');
      const ps = document.getElementById('projSite');
      const pt = document.getElementById('projTech');
      if (pn && scan.project_name) pn.value = scan.project_name;
      if (ps && scan.site)         ps.value = scan.site;
      if (pt && scan.inspector)    pt.value = scan.inspector;

      // Show reference banner
      const API = window.location.origin;
      const imgSrc = scan.image_path
        ? (scan.image_path.startsWith('http') ? scan.image_path : `${API}/api/scan-image/${scan.image_path}`)
        : null;
      const dateStr = scan.timestamp
        ? new Date(scan.timestamp + (scan.timestamp.endsWith('Z') ? '' : 'Z'))
            .toLocaleString('en-GB', { day:'2-digit', month:'short', year:'numeric' })
        : '—';
      const rvBanner = document.getElementById('rvBanner');
      if (rvBanner) {
        rvBanner.style.display = 'block';
        rvBanner.innerHTML = `<div class="rv-banner">
          ${imgSrc
            ? `<img class="rv-banner-thumb" src="${imgSrc}" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">`
            : ''}
          <div class="rv-banner-thumb-ph" ${imgSrc ? 'style="display:none"' : ''}>
            <i class="fas fa-solar-panel" style="color:#3DCD58;font-size:18px"></i>
          </div>
          <div class="rv-banner-info">
            <div class="rv-banner-label"><i class="fas fa-rotate-right"></i> Return Visit — Based on Previous Inspection</div>
            <div class="rv-banner-title">${(scan.project_name || 'Unnamed Project').replace(/&/g,'&amp;').replace(/</g,'&lt;')}</div>
            <div class="rv-banner-sub">${dateStr} · ${(scan.panel_type || 'Unknown panel').replace(/&/g,'&amp;').replace(/</g,'&lt;')} · ${(scan.task || 'inspection').replace(/&/g,'&amp;').replace(/</g,'&lt;')}</div>
          </div>
        </div>`;
      }

      // Auto-progress wizard: DEAD → Doors Open
      fwGo(1, 'no');
      setTimeout(() => {
        fwGo(2, 'yes');
        // Select previous task
        if (scan.task) {
          const taskBtn = document.querySelector(`.t-btn[data-task="${scan.task}"]`);
          if (taskBtn) taskBtn.click();
        }
        // Scroll to capture card
        const card4 = document.getElementById('card4');
        if (card4) setTimeout(() => card4.scrollIntoView({ behavior: 'smooth', block: 'start' }), 300);
      }, 120);
    } catch(e) {
      console.warn('Return visit pre-fill failed:', e);
    }
  }
}
function doLogout(){
  localStorage.removeItem('pi_auth');
  localStorage.removeItem('pi_user');
  window.location.replace('login.html');
}

/* ═══════════════ STATE ═══════════════ */
const S = {
  img64:null, mime:'image/jpeg', task:'commissioning', isLive:false, sldMime:'image/png',
  zoneOn:false, zone:null, drawing:false, drawStart:null,
  sld64:null, dets:[], hovIdx:-1,
};

/* ═══════════════ DOM ═══════════════ */
const g = id => document.getElementById(id);
const fileInput   = g('fileInput');
const uploadZone  = g('uploadZone');
const upPreview   = g('uploadPreview');
const prevThumb   = g('prevThumb');
const prevName    = g('prevName');
const prevSz      = g('prevSz');
const prevClr     = g('prevClr');
const zoneToggle  = g('zoneToggle');
const zoneHint    = g('zoneHint');
const zoneClear   = g('zoneClear');
const sldBody     = g('sldBody');   /* null — no sldBody in new layout, harmless */
const sldInput    = g('sldInput');
const sldName     = g('sldName');
const canvasWrap  = g('canvasWrap');
const canvasPh    = g('canvasPh');
const imgC        = g('imgCanvas');
const ovlC        = g('overlayCanvas');
const resultsEl   = g('results');
const rInner      = g('rInner');
const ictx        = imgC.getContext('2d');
const octx        = ovlC.getContext('2d');
let loadedImg = null;

const detBtns = [g('detBtnD'), g('detBtnM')];
const spinEls = [g('spinD'),   g('spinM')];
const iconEls = [g('iconD'),   g('iconM')];
const txtEls  = [g('txtD'),    g('txtM')];

function setLoading(on) {
  detBtns.forEach(b => b.disabled = on || !S.img64);
  spinEls.forEach(s => s.style.display = on ? 'block' : 'none');
  iconEls.forEach(i => i.style.display = on ? 'none' : '');
  txtEls.forEach(t => t.textContent = on ? 'Analysing…' : 'Analyse Panel');
}
function setEnabled(v) { detBtns.forEach(b => b.disabled = !v); }

/* ═══════════════ STEPPER ═══════════════ */
function setStepState(n, state) {
  const el = g('step' + n + 'Item');
  if (!el) return;
  el.classList.remove('step-active','step-done','step-pending');
  el.classList.add('step-' + state);
}
function unlockCard(n) {
  const c = g('card' + n);
  if (c) c.classList.remove('locked');
  setStepState(n, 'active');
  if (n > 1) setStepState(n - 1, 'done');
}
function lockCard(n) {
  const c = g('card' + n);
  if (c) c.classList.add('locked');
  setStepState(n, 'pending');
}
function completeStep(n) { setStepState(n, 'done'); }

/* ═══════════════ UPLOAD ═══════════════ */
uploadZone.addEventListener('click', e => {
  if (e.target === fileInput) return;
  fileInput.click();
});
g('camBtn').addEventListener('click', e => {
  e.preventDefault();
  g('camInput').click();
});

uploadZone.addEventListener('dragover',  e => { e.preventDefault(); uploadZone.classList.add('drag'); });
uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('drag'));
uploadZone.addEventListener('drop', e => {
  e.preventDefault(); uploadZone.classList.remove('drag');
  const f = e.dataTransfer.files[0];
  if (f && f.type.startsWith('image/')) loadFile(f);
});
fileInput.addEventListener('change', e => { if (e.target.files[0]) loadFile(e.target.files[0]); });
g('camInput').addEventListener('change', e => { if (e.target.files[0]) loadFile(e.target.files[0]); });

prevClr.addEventListener('click', () => {
  S.img64 = null; loadedImg = null;
  upPreview.style.display = 'none';
  uploadZone.style.display = '';
  canvasPh.style.display = '';
  imgC.style.display = ovlC.style.display = 'none';
  resultsEl.classList.remove('vis');
  setEnabled(false);
  fileInput.value = '';
  S.dets = []; S.zone = null;
  zoneClear.classList.remove('vis');
  canvasWrap.style.height = '';
  redraw();
});

function loadFile(file) {
  S.mime = file.type || 'image/jpeg';
  const reader = new FileReader();
  reader.onload = ev => {
    S.img64 = ev.target.result.split(',')[1];
    prevThumb.src = ev.target.result;
    prevName.textContent = file.name;
    prevSz.textContent = (file.size / 1024).toFixed(0) + ' KB';
    upPreview.style.display = 'flex';
    uploadZone.style.display = 'none';
    setEnabled(true);
    completeStep(4);
    if (S.sld64) g('btnCmp').style.display = 'flex';
    const img = new Image();
    img.onload = () => { loadedImg = img; renderImg(); S.zone = null; redraw(); };
    img.src = ev.target.result;
  };
  reader.readAsDataURL(file);
}

/* ═══════════════ CANVAS SIZING ═══════════════ */
function cSize() {
  if (!loadedImg) return { w:0, h:0 };
  const mW = canvasWrap.getBoundingClientRect().width - 2;
  const maxH = Math.min(window.innerHeight * 0.65, 700);
  const sc = Math.min(mW / loadedImg.width, maxH / loadedImg.height, 1);
  return { w: Math.floor(loadedImg.width * sc), h: Math.floor(loadedImg.height * sc) };
}

function renderImg() {
  if (!loadedImg) return;
  const {w, h} = cSize();
  if (!w || !h) return;
  imgC.width = ovlC.width = w;
  imgC.height = ovlC.height = h;
  imgC.style.width = ovlC.style.width = w + 'px';
  imgC.style.height = ovlC.style.height = h + 'px';
  canvasWrap.style.height = h + 'px';
  ictx.drawImage(loadedImg, 0, 0, w, h);
  canvasPh.style.display = 'none';
  imgC.style.display = ovlC.style.display = 'block';
}

window.addEventListener('resize', () => { renderImg(); redraw(); });

/* ═══════════════ COORDINATE HELPERS ═══════════════ */
const n2p = (n, d) => n / 1000 * d;
const p2n = (p, d) => Math.round(p / d * 1000);
const clamp = v => Math.max(0, Math.min(1000, v));
function safeBuf(z, pad=80) {
  return { ymin:clamp(z.ymin-pad), xmin:clamp(z.xmin-pad), ymax:clamp(z.ymax+pad), xmax:clamp(z.xmax+pad) };
}

/* ═══════════════ ZONE TOGGLE ═══════════════ */
zoneToggle.addEventListener('click', () => {
  S.zoneOn = !S.zoneOn;
  zoneToggle.classList.toggle('on', S.zoneOn);
  zoneHint.classList.toggle('vis', S.zoneOn);
  ovlC.style.cursor = S.zoneOn ? 'crosshair' : 'default';
});
zoneClear.addEventListener('click', () => { S.zone = null; zoneClear.classList.remove('vis'); redraw(); });

/* ═══════════════ ZONE DRAWING ═══════════════ */
function evXY(e) {
  const r = ovlC.getBoundingClientRect();
  const t = e.touches?.[0] || e.changedTouches?.[0];
  return { cx:(t ? t.clientX : e.clientX) - r.left, cy:(t ? t.clientY : e.clientY) - r.top };
}
function startDraw(e) { if (!S.zoneOn || !loadedImg) return; e.preventDefault(); S.drawing = true; const {cx,cy} = evXY(e); S.drawStart = {cx,cy}; }
function moveDraw(e) {
  if (!S.drawing) return; e.preventDefault();
  const {cx,cy} = evXY(e); const {w,h} = cSize();
  const x0=p2n(S.drawStart.cx,w), y0=p2n(S.drawStart.cy,h), x1=p2n(cx,w), y1=p2n(cy,h);
  S.zone = { ymin:clamp(Math.min(y0,y1)), xmin:clamp(Math.min(x0,x1)), ymax:clamp(Math.max(y0,y1)), xmax:clamp(Math.max(x0,x1)) };
  redraw();
}
function endDraw(e) { if (!S.drawing) return; e.preventDefault(); S.drawing = false; if (S.zone) zoneClear.classList.add('vis'); }
ovlC.addEventListener('mousedown',  startDraw);
ovlC.addEventListener('mousemove',  moveDraw);
ovlC.addEventListener('mouseup',    endDraw);
ovlC.addEventListener('mouseleave', () => { if (S.drawing) S.drawing = false; });
ovlC.addEventListener('touchstart', startDraw, {passive:false});
ovlC.addEventListener('touchmove',  moveDraw,  {passive:false});
ovlC.addEventListener('touchend',   endDraw,   {passive:false});

/* ═══════════════ SLD ═══════════════ */
sldInput.addEventListener('change', e => {
  const f = e.target.files[0]; if (!f) return;
  sldName.textContent = f.name;
  S.sldMime = f.type || 'image/png';
  const r = new FileReader();
  r.onload = ev => {
    S.sld64 = ev.target.result.split(',')[1];
    g('btnReadSld').style.display = 'flex';
    g('btnCmp').style.display = S.img64 ? 'flex' : 'none';
    completeStep(3);
  };
  r.readAsDataURL(f);
});

/* ═══════════════ TASK BUTTONS ═══════════════ */
document.querySelectorAll('.t-btn').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.t-btn').forEach(x => x.classList.remove('on'));
  b.classList.add('on'); S.task = b.dataset.task; loadChecklist();
}));

/* ═══════════════ ANALYSE ═══════════════ */
async function analyse() {
  if (!S.img64) return;
  setLoading(true);
  resultsEl.classList.remove('vis');
  try {
    if (S.task === 'aging') {
      const res = await fetch('/api/aging', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ imageBase64: S.img64, mimeType: S.mime })
      });
      if (!res.ok) { const e = await res.json().catch(()=>({})); throw new Error(e.detail || `Server error ${res.status}`); }
      renderAgingResults(await res.json());
      return;
    }
    const body = {
      imageBase64: S.img64, mimeType: S.mime,
      task: S.task || 'others', identifyOnly: false,
      username: localStorage.getItem('pi_user') || '',
      projectName: g('projName').value.trim() || undefined,
      site: g('projSite').value.trim() || undefined,
      inspector: g('projTech').value.trim() || undefined,
    };
    if (S.zone)  { body.workZone = S.zone; body.safetyBuffer = safeBuf(S.zone); }
    if (S.sld64) body.sldBase64 = S.sld64;
    const res = await fetch('/api/analyze', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (err.error === 'not_a_panel') throw new Error('Not a panel: ' + (err.detected_as || 'image does not show an electrical switchboard'));
      throw new Error(err.error || err.detail || `Server error ${res.status}`);
    }
    renderResults(await res.json());
  } catch(e) {
    renderError(e.message);
  } finally {
    setLoading(false);
  }
}
detBtns.forEach(b => b.addEventListener('click', analyse));

/* ═══════════════ AGING RESULTS ═══════════════ */
function renderAgingResults(d) {
  resultsEl.classList.add('vis');
  requestAnimationFrame(() => renderImg());

  const condColour = { Excellent:'#3DCD58', Good:'#22D3EE', Fair:'#FFA502', Poor:'#FF4757', Critical:'#FF0000' };
  const condBg     = { Excellent:'rgba(61,205,88,.1)', Good:'rgba(34,211,238,.1)', Fair:'rgba(255,165,2,.1)', Poor:'rgba(255,71,87,.1)', Critical:'rgba(255,0,0,.12)' };
  const condBorder = { Excellent:'rgba(61,205,88,.35)', Good:'rgba(34,211,238,.35)', Fair:'rgba(255,165,2,.35)', Poor:'rgba(255,71,87,.35)', Critical:'rgba(255,0,0,.4)' };
  const urgColour  = { 'None':'#3DCD58', 'Plan within 5-8 years':'#FFA502', 'Priority within 2-3 years':'#FF4757', 'Immediate':'#FF0000' };

  const cond    = d.condition || 'Unknown';
  const cc      = condColour[cond]  || '#6A9EC0';
  const cbg     = condBg[cond]      || 'rgba(106,158,192,.08)';
  const cborder = condBorder[cond]  || 'rgba(106,158,192,.2)';
  const score   = d.condition_score || 0;
  const urgency = d.replacement_urgency || '—';
  const uc      = urgColour[urgency] || '#6A9EC0';

  const scoreDots = [1,2,3,4,5].map(i =>
    `<div style="width:12px;height:12px;border-radius:50%;background:${i<=score ? cc : 'rgba(255,255,255,.1)'}"></div>`
  ).join('');

  const agingSigns = (d.visual_aging_signs || []);
  const goodSigns  = (d.no_aging_signs || []);
  const products   = (d.detected_products || []);
  const signs      = agingSigns.map(s => s.toLowerCase()).join(' ');

  const statusColour = { Current:'#3DCD58', Legacy:'#FFA502', 'End of Life':'#FF4757', Obsolete:'#FF0000' };

  // ── Generate age-specific safety warnings ──────────────────────────────────
  const safetyWarns = [];
  const condL = cond.toLowerCase();
  const ageNum = parseInt((d.estimated_age_years || '0').split('-')[0]) || 0;

  if (condL === 'critical') {
    safetyWarns.push({ text: 'Panel exceeds 25-year design life — Schneider Electric mandatory full condition assessment required before any intervention.', crit: true });
    safetyWarns.push({ text: 'Aged breakers may have degraded interrupting capacity — do not rely on fault protection without professional testing.', crit: true });
    safetyWarns.push({ text: 'Insulation degradation likely at this age — elevated arc flash and electric shock risk. Treat as higher hazard than rated.', crit: true });
  } else if (condL === 'poor') {
    safetyWarns.push({ text: 'Equipment approaching critical age — breaker contact erosion may reduce interrupting capacity below rated value.', crit: true });
    safetyWarns.push({ text: 'Annual thermographic inspection is mandatory at this age to detect hot spots before failure occurs.', crit: false });
  } else if (condL === 'fair') {
    safetyWarns.push({ text: 'Equipment is mid-life — increase inspection frequency from 3-year to annual schedule per Schneider Electric recommendation.', crit: false });
  }

  if ((d.eol_status || '') === 'Obsolete') {
    safetyWarns.push({ text: 'Product is OBSOLETE — no spare parts available from Schneider Electric. Any component failure requires full replacement of the panel section.', crit: true });
  } else if ((d.eol_status || '') === 'End of Life') {
    safetyWarns.push({ text: 'Product is End of Life — spare part availability is reducing. Order critical spares (trip units, contacts) now before stock runs out.', crit: true });
  } else if ((d.eol_status || '') === 'Legacy') {
    safetyWarns.push({ text: 'Legacy product — spare parts still available but reducing. Plan replacement in next maintenance cycle.', crit: false });
  }

  if (signs.includes('burn') || signs.includes('scorch') || signs.includes('char')) {
    safetyWarns.push({ text: 'Burn marks detected — breaker may have exceeded maximum 50 fault trip operations (Schneider limit). Do not re-energise without full inspection and replacement.', crit: true });
  }
  if (signs.includes('rust') || signs.includes('corrosion')) {
    safetyWarns.push({ text: 'Corrosion detected — busbar or terminal connections may have increased resistance causing overheating. Inspect all connections immediately, arc flash risk elevated.', crit: true });
  }
  if (signs.includes('crack') || signs.includes('broken')) {
    safetyWarns.push({ text: 'Physical damage detected — damaged covers compromise IP rating and expose live parts. Replace before re-energising.', crit: true });
  }
  if (signs.includes('insulation') || signs.includes('cable')) {
    safetyWarns.push({ text: 'Cable insulation degradation visible — risk of insulation failure and short circuit. Full cable inspection required before next energisation.', crit: true });
  }
  if (ageNum >= 25 || condL === 'critical') {
    safetyWarns.push({ text: 'Equipment over 25 years old — Schneider Electric official policy: mandatory condition assessment and active replacement planning required.', crit: true });
  }
  // ──────────────────────────────────────────────────────────────────────────

  let h = '';

  // Condition banner
  h += `<div class="fu" style="padding:18px 20px;border-radius:14px;background:${cbg};border:1px solid ${cborder};display:flex;align-items:center;gap:16px;margin-bottom:4px">
    <div style="font-size:36px">⏱</div>
    <div style="flex:1">
      <div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.9px;color:${cc};margin-bottom:4px">Aging Assessment</div>
      <div style="font-size:22px;font-weight:900;color:${cc};letter-spacing:-.5px">${esc(cond)}</div>
      <div style="font-size:12px;color:var(--dim);margin-top:3px">${esc(d.notes || '')}</div>
    </div>
    <div style="text-align:center;flex-shrink:0">
      <div style="display:flex;gap:4px;margin-bottom:6px">${scoreDots}</div>
      <div style="font-size:10px;color:var(--muted)">Condition Score</div>
    </div>
  </div>`;

  // Age + EOL row
  h += `<div class="fu fu1" style="display:flex;gap:8px;flex-wrap:wrap">
    <div class="st-chip"><i class="fas fa-calendar" style="color:var(--purple)"></i><b>${esc(d.estimated_age_years || '—')}</b> yrs est.</div>
    <div class="st-chip"><i class="fas fa-clock-rotate-left" style="color:var(--dim)"></i>${esc(d.estimated_installation_decade || '—')}</div>
    <div class="st-chip"><i class="fas fa-solar-panel" style="color:var(--green)"></i>${esc(d.panel_type || 'Unknown')}</div>
    <div class="st-chip" style="color:${statusColour[d.eol_status]||'var(--dim)'}"><i class="fas fa-circle-info"></i><b>${esc(d.eol_status || '—')}</b></div>
  </div>`;

  // Age-specific safety warnings
  if (safetyWarns.length) {
    h += `<div class="fu fu2"><div class="sec-ttl"><i class="fas fa-triangle-exclamation" style="color:var(--amber)"></i> Age-Related Safety Warnings</div>
    <div class="warn-list">`;
    safetyWarns.forEach(w => {
      h += `<div class="w-item ${w.crit ? 'crit' : 'warn'}"><i class="fas ${w.crit ? 'fa-circle-radiation' : 'fa-triangle-exclamation'}"></i><span>${esc(w.text)}</span></div>`;
    });
    h += `</div></div>`;
  }

  // Detected products table
  if (products.length) {
    h += `<div class="fu fu2"><div class="sec-ttl"><i class="fas fa-microchip" style="color:var(--blue)"></i> Detected Products</div>
    <div style="display:flex;flex-direction:column;gap:6px">`;
    products.forEach(p => {
      const sc  = statusColour[p.status] || 'var(--dim)';
      const pd  = getSeDoc(p.product);
      h += `<div style="padding:9px 13px;background:rgba(7,20,34,.8);border:1px solid rgba(255,255,255,.07);border-radius:10px">
        <div style="display:flex;align-items:center;justify-content:space-between">
          <div>
            <div style="font-size:12px;font-weight:700;color:var(--text)">${esc(p.product)}</div>
            <div style="font-size:10px;color:var(--muted);margin-top:2px">Introduced ${esc(p.introduced)} · ${esc(p.generation)}</div>
          </div>
          <div style="font-size:10px;font-weight:700;color:${sc};padding:3px 9px;border-radius:8px;border:1px solid ${sc}22;background:${sc}11;flex-shrink:0">${esc(p.status)}</div>
        </div>
        ${pd ? `<div style="display:flex;gap:6px;margin-top:8px">
          <a href="${pd.page}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:7px;background:rgba(61,205,88,.08);border:1px solid rgba(61,205,88,.25);color:#3DCD58;font-size:10px;font-weight:700;text-decoration:none"><i class="fas fa-arrow-up-right-from-square"></i> Product Page</a>
          ${pd.catalogue ? `<a href="${pd.catalogue}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:7px;background:rgba(74,158,255,.08);border:1px solid rgba(74,158,255,.25);color:#4A9EFF;font-size:10px;font-weight:700;text-decoration:none"><i class="fas fa-file-pdf"></i> Catalogue</a>` : ''}
        </div>` : ''}
      </div>`;
    });
    h += `</div></div>`;
  }

  // Visual signs found
  if (agingSigns.length) {
    h += `<div class="fu fu3"><div class="sec-ttl"><i class="fas fa-triangle-exclamation" style="color:var(--amber)"></i> Aging Signs Detected</div>
    <div class="warn-list">`;
    agingSigns.forEach(w => {
      h += `<div class="w-item warn"><i class="fas fa-circle-dot"></i><span>${esc(w)}</span></div>`;
    });
    h += `</div></div>`;
  }

  // Positive findings
  if (goodSigns.length) {
    h += `<div class="fu fu4"><div class="sec-ttl"><i class="fas fa-circle-check" style="color:var(--green)"></i> Good Condition</div>
    <div class="warn-list">`;
    goodSigns.forEach(w => {
      h += `<div class="w-item" style="background:rgba(61,205,88,.06);border:1px solid rgba(61,205,88,.18);color:#6EE7B7"><i class="fas fa-check"></i><span>${esc(w)}</span></div>`;
    });
    h += `</div></div>`;
  }

  // EOL note
  if (d.eol_note) {
    h += `<div class="fu" style="padding:10px 13px;border-radius:10px;background:rgba(74,158,255,.06);border:1px solid rgba(74,158,255,.18);font-size:11px;color:#93C5FD;display:flex;gap:8px;align-items:flex-start">
      <i class="fas fa-circle-info" style="color:var(--blue);margin-top:2px;flex-shrink:0"></i><span>${esc(d.eol_note)}</span></div>`;
  }

  // Recommendation
  h += `<div class="fu" style="padding:14px 16px;border-radius:12px;background:rgba(168,85,247,.07);border:1px solid rgba(168,85,247,.2)">
    <div style="font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.9px;color:#A855F7;margin-bottom:6px"><i class="fas fa-screwdriver-wrench"></i> Maintenance Recommendation</div>
    <div style="font-size:12px;color:var(--dim);line-height:1.65">${esc(d.maintenance_recommendation || '—')}</div>
    <div style="margin-top:10px;display:flex;align-items:center;gap:7px">
      <div style="font-size:9.5px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--muted)">Replacement urgency:</div>
      <div style="font-size:11px;font-weight:800;color:${uc}">${esc(urgency)}</div>
    </div>
  </div>`;

  rInner.innerHTML = h;
}

/* ═══════════════ OVERLAY DRAW ═══════════════ */
const COL = { acb:'#A855F7', mccb:'#FFA502', mcb:'#22D3EE', contactor:'#EC4899', relay:'#6366F1', plc:'#F97316', structure:'#4A9EFF', component:'#3DCD58' };
function lCol(d) {
  const t = (d.label||d.type||'').toLowerCase();
  if (t.includes('acb')||t.includes('masterpact'))          return COL.acb;
  if (t.includes('mccb')||t.includes('nsx')||t.includes('ns')) return COL.mccb;
  if (t.includes('mcb')||t.includes('acti')||t.includes('ic60')) return COL.mcb;
  if (t.includes('contactor')) return COL.contactor;
  if (t.includes('relay'))     return COL.relay;
  if (t.includes('plc'))       return COL.plc;
  if ((d.category||'')==='structure') return COL.structure;
  return COL.component;
}
function redraw() {
  const {w,h} = cSize(); octx.clearRect(0,0,w,h); if (!loadedImg) return;
  if (S.zone) {
    const sb = safeBuf(S.zone);
    octx.strokeStyle='rgba(255,71,87,.7)'; octx.lineWidth=1.5; octx.setLineDash([5,4]);
    octx.strokeRect(n2p(sb.xmin,w),n2p(sb.ymin,h),n2p(sb.xmax-sb.xmin,w),n2p(sb.ymax-sb.ymin,h));
    octx.setLineDash([]);
    octx.strokeStyle='rgba(255,165,2,.9)'; octx.lineWidth=2; octx.setLineDash([6,4]);
    octx.strokeRect(n2p(S.zone.xmin,w),n2p(S.zone.ymin,h),n2p(S.zone.xmax-S.zone.xmin,w),n2p(S.zone.ymax-S.zone.ymin,h));
    octx.setLineDash([]);
    octx.font='bold 10px Inter,sans-serif';
    octx.fillStyle='rgba(255,165,2,.95)'; octx.fillText('WORK ZONE',n2p(S.zone.xmin,w)+4,n2p(S.zone.ymin,h)-4);
    octx.fillStyle='rgba(255,71,87,.85)'; octx.fillText('SAFETY BUFFER',n2p(sb.xmin,w)+4,n2p(sb.ymin,h)-4);
  }
  S.dets.forEach((d,i) => {
    const box=d.box||d.box_2d; if (!box||box.length<4) return;
    const [ymin,xmin,ymax,xmax]=box;
    const bx=n2p(xmin,w),by=n2p(ymin,h),bw=n2p(xmax-xmin,w),bh=n2p(ymax-ymin,h);
    const active=i===S.hovIdx, inZ=S.zone&&!(xmax<S.zone.xmin||xmin>S.zone.xmax||ymax<S.zone.ymin||ymin>S.zone.ymax);
    const t=(d.label||d.type||'').toLowerCase(), isACB=t.includes('acb')||t.includes('masterpact');
    const col=inZ&&isACB?'#FF4757':inZ?'#FFA502':lCol(d);
    octx.strokeStyle=col; octx.lineWidth=inZ&&isACB?3:active?2.5:1.5;
    octx.globalAlpha=active?1:inZ?.95:.75;
    octx.strokeRect(bx,by,bw,bh);
    if (inZ&&isACB){octx.fillStyle='rgba(255,71,87,.12)';octx.globalAlpha=1;octx.fillRect(bx,by,bw,bh);}
    else if(active){octx.fillStyle='rgba(255,255,255,.05)';octx.globalAlpha=.12;octx.fillRect(bx,by,bw,bh);}
    octx.globalAlpha=1;
    const chip=(d.label||d.type||'Component')+(d.rating?` · ${d.rating}`:'')+((inZ&&isACB)?' ⚠ DANGER':'');
    octx.font='bold 10px Inter,sans-serif';
    const tw=octx.measureText(chip).width;
    octx.fillStyle=col; octx.globalAlpha=.92;
    octx.beginPath();
    if(octx.roundRect) octx.roundRect(bx,by-15,tw+10,15,3); else octx.rect(bx,by-15,tw+10,15);
    octx.fill(); octx.globalAlpha=1;
    octx.fillStyle='#07101F'; octx.fillText(chip,bx+5,by-4);
  });
}

/* ═══════════════ RENDER RESULTS ═══════════════ */
function renderResults(data) {
  S.dets = data.breakers||[]; resultsEl.classList.add('vis');
  requestAnimationFrame(() => { renderImg(); redraw(); });
  // cache for printReport
  window._lastPanelType = data.panel_type||'Unknown';
  window._lastSummary   = data.summary||'';
  window._lastWarns     = data.safety_warnings||[];
  window._lastRecs      = data.task_recommendations||[];
  window._lastComps     = S.dets.filter(b=>(b.category||'component')!=='structure');
  g('btnPrint').classList.add('vis');
  const comps  = S.dets.filter(b=>(b.category||'component')!=='structure');
  const structs = S.dets.filter(b=>(b.category||'component')==='structure');
  const acbs   = comps.filter(b=>{ const t=(b.type||'').toLowerCase(); return t.includes('acb')||t.includes('masterpact'); });
  const mccbs  = comps.filter(b=>{ const t=(b.type||'').toLowerCase(); return t.includes('mccb')||t.includes('nsx')||t.includes('ns'); });
  const mcbs   = comps.filter(b=>{ const t=(b.type||'').toLowerCase(); return t.includes('mcb')||t.includes('acti')||t.includes('ic60'); });
  const ptLbl  = data.panel_type||'Unknown';
  const pt     = ptLbl.toLowerCase().replace(/\s+/g,'-');
  const cls    = pt.includes('prismaset-g')?'pg':pt.includes('prismaset-p')?'pp':pt.includes('okken')?'ok':pt.includes('abb')?'ab':'un';
  const icon   = ptLbl.includes('Okken')?'fa-industry':ptLbl.includes('PrismaSeT')?'fa-bolt':ptLbl.includes('ABB')?'fa-a':'fa-circle-question';

  const projName = g('projName').value.trim();
  const projSite = g('projSite').value.trim();
  const projPermit = g('projPermit').value.trim();
  const projTech = g('projTech').value.trim();

  let h = '';

  if (projName || projSite) {
    h += `<div class="proj-banner fu">
      <i class="fas fa-folder-open"></i>
      <div>
        <div class="proj-banner-name">${esc(projName || 'Unnamed Project')}</div>
        <div class="proj-banner-meta">${[projSite, projPermit, projTech ? 'Tech: '+projTech : ''].filter(Boolean).join(' · ')}</div>
      </div>
    </div>`;
  }

  h += `<div class="fu fu1">
    <div class="p-badge-row">
      <span class="p-badge ${cls}"><i class="fas ${icon}"></i> ${ptLbl}</span>
      ${data.busbar_side&&data.busbar_side!=='unknown'?`<span class="bb-note"><i class="fas fa-arrows-left-right"></i> Busbar: <b style="color:var(--text)">${data.busbar_side}</b></span>`:''}
    </div>
    ${data.summary?`<p class="p-sum">${esc(data.summary)}</p>`:''}
    ${docLinks(ptLbl)}
  </div>`;

  h += `<div class="stats-row fu fu2">
    ${acbs.length  ?`<div class="st-chip"><i class="fas fa-bolt" style="color:#A855F7"></i><b>${acbs.length}</b> ACB</div>`:''}
    ${mccbs.length ?`<div class="st-chip"><i class="fas fa-bolt" style="color:#FFA502"></i><b>${mccbs.length}</b> MCCB</div>`:''}
    ${mcbs.length  ?`<div class="st-chip"><i class="fas fa-bolt" style="color:#22D3EE"></i><b>${mcbs.length}</b> MCB</div>`:''}
    ${structs.length?`<div class="st-chip"><i class="fas fa-table-columns" style="color:#4A9EFF"></i><b>${structs.length}</b> Column</div>`:''}
    ${comps.length?`<div class="st-chip"><i class="fas fa-microchip" style="color:var(--green)"></i><b>${comps.length}</b> total</div>`:'<div class="st-chip" style="color:var(--muted)">No components detected</div>'}
  </div>`;

  const warns = data.safety_warnings||[];
  if (warns.length) {
    h += `<div class="warn-list fu fu3"><div class="sec-ttl"><i class="fas fa-triangle-exclamation" style="color:var(--amber)"></i> Safety Warnings</div>`;
    warns.forEach(w => {
      const c=w.toLowerCase().includes('arc')||w.toLowerCase().includes('energi')||w.toLowerCase().includes('live')||w.includes('🔥');
      h+=`<div class="w-item ${c?'crit':'warn'}"><i class="fas ${c?'fa-circle-radiation':'fa-triangle-exclamation'}"></i><span>${esc(w)}</span></div>`;
    });
    h += `</div>`;
  }

  const recs = data.task_recommendations||[];
  if (recs.length) {
    h += `<div class="fu fu4"><div class="sec-ttl"><i class="fas fa-shield-halved" style="color:var(--green)"></i> ERMS Operations — ${cap(S.task)}</div>
    <div style="overflow-x:auto"><table class="erms-tbl"><thead><tr><th>Operation</th><th>Position</th><th>Hazards</th><th>ERMS</th><th>Alternative</th></tr></thead><tbody>`;
    recs.forEach(r => {
      const eClass=`erms-${r.erms}`;
      const eLbl=r.erms==='ON'?'ERMS ON':r.erms==='recommended'?'Recommended':r.erms==='OFF'?'ERMS OFF':'—';
      const pos=r.position==='inside'?'Inside (doors open)':r.position==='outside'?'<0.3 m (closed)':r.position==='near'?'0.3–1 m':'>1 m';
      const haz=(r.hazards||[]).map(x=>`<span class="haz ${x==='Arc Flash'?'haz-arc':'haz-sh'}">${x==='Arc Flash'?'🔥':'⚡'} ${x}</span>`).join('');
      h+=`<tr><td style="color:var(--text);font-weight:500">${esc(r.operation)}</td><td>${esc(pos)}</td><td>${haz||'<span style="color:var(--muted)">None</span>'}</td><td><span class="erms-pill ${eClass}">${eLbl}</span></td><td>${r.alternative?esc(r.alternative):'<span style="color:var(--muted)">—</span>'}</td></tr>`;
    });
    h += `</tbody></table></div></div>`;
  }

  if (comps.length) {
    h += `<div class="fu"><div class="sec-ttl"><i class="fas fa-list" style="color:var(--dim)"></i> Detected Components</div><div class="cards-row">`;
    comps.forEach((d,i) => {
      const c=lCol(d);
      const _doc = getSeDoc(d.label||d.type||'');
      h+=`<div class="d-card" data-idx="${i}" style="border-top:2px solid ${c}22">
        <div class="d-lbl" title="${esc(d.type||'')}">${esc(d.label||d.type||'Component')}</div>
        <div class="d-meta">${esc(d.brand||'')}${d.type_detail?' · '+esc(d.type_detail):''}</div>
        ${d.circuit_label?`<div class="d-meta" style="color:var(--dim)">${esc(d.circuit_label)}</div>`:''}
        ${d.rating?`<span class="d-rat">${esc(d.rating)}</span>`:''}
        ${_doc?`<div style="display:flex;gap:4px;margin-top:7px;flex-wrap:wrap">
          <a href="${_doc.page}" target="_blank" rel="noopener" onclick="event.stopPropagation()" style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:6px;background:rgba(61,205,88,.08);border:1px solid rgba(61,205,88,.2);color:#3DCD58;font-size:9px;font-weight:700;text-decoration:none"><i class="fas fa-arrow-up-right-from-square"></i> Docs</a>
          ${_doc.catalogue?`<a href="${_doc.catalogue}" target="_blank" rel="noopener" onclick="event.stopPropagation()" style="display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:6px;background:rgba(74,158,255,.08);border:1px solid rgba(74,158,255,.2);color:#4A9EFF;font-size:9px;font-weight:700;text-decoration:none"><i class="fas fa-file-pdf"></i> PDF</a>`:''}
        </div>`:''}
      </div>`;
    });
    h += `</div></div>`;
  }

  rInner.innerHTML = h;
  rInner.querySelectorAll('.d-card').forEach(c => {
    c.addEventListener('mouseenter', () => { S.hovIdx = +c.dataset.idx; redraw(); });
    c.addEventListener('mouseleave', () => { S.hovIdx = -1; redraw(); });
  });
}

function renderError(msg) {
  resultsEl.classList.add('vis');
  requestAnimationFrame(() => renderImg());
  rInner.innerHTML = `<div class="w-item crit fu"><i class="fas fa-circle-xmark"></i><span><b>Detection failed:</b> ${esc(msg)}</span></div>`;
}

async function runSldRead() {
  if (!S.sld64) return;
  const btn=g('btnReadSld'),spin=g('spinReadSld'),icon=g('iconReadSld'),txt=g('txtReadSld');
  btn.disabled=true; spin.style.display='block'; icon.style.display='none'; txt.textContent='Reading…';
  try {
    const res = await fetch('/api/read-sld', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ sldBase64:S.sld64, sldMime:S.sldMime||'image/png' })
    });
    const d = await res.json();
    g('sldReadSummary').textContent = d.summary || '';
    let html = '';
    if (d.incoming_supply) {
      html += `<div class="sld-cmp-section"><div class="sld-cmp-ttl">Incoming Supply</div>
        <div class="sld-cmp-item sld-match">Voltage: ${d.incoming_supply.voltage||'—'} · Phases: ${d.incoming_supply.phases||'—'} · Main: ${d.incoming_supply.main_rating||'—'}</div></div>`;
    }
    if (d.circuits && d.circuits.length) {
      html += `<div class="sld-cmp-section"><div class="sld-cmp-ttl">Circuits (${d.circuits.length})</div>`;
      d.circuits.forEach(c => {
        html += `<div class="sld-cmp-item sld-match"><b>${c.id||''} ${c.name||'—'}</b> — ${c.rating||'—'} ${c.breaker_type||''} ${c.load?'· '+c.load:''}</div>`;
      });
      html += '</div>';
    }
    if (d.notes && d.notes.length) {
      html += `<div class="sld-cmp-section"><div class="sld-cmp-ttl">Notes</div>`;
      d.notes.forEach(n => { html += `<div class="sld-cmp-item sld-disc">${n}</div>`; });
      html += '</div>';
    }
    g('sldReadBody').innerHTML = html;
    g('sldReadResult').style.display = 'block';
  } catch(e) {
    alert('SLD read failed: ' + e.message);
  } finally {
    btn.disabled=false; spin.style.display='none'; icon.style.display=''; txt.textContent='Read SLD';
  }
}

async function runSldCompare() {
  if (!S.img64 || !S.sld64) return;
  const btn=g('btnCmp'),spin=g('spinCmp'),icon=g('iconCmp'),txt=g('txtCmp');
  btn.disabled=true; spin.style.display='block'; icon.style.display='none'; txt.textContent='Comparing…';
  try {
    const res = await fetch('/api/compare-sld', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ imageBase64:S.img64, imageMime:S.mime, sldBase64:S.sld64, sldMime:S.sldMime||'image/png' })
    });
    const d = await res.json();
    g('sldCmpSummary').textContent = d.summary||'';
    const matches=(d.matches||[]).map(i=>`<div class="sld-cmp-item sld-match">✅ <b>${esc(i.item)}</b> — ${esc(i.note)}</div>`).join('');
    const discs  =(d.discrepancies||[]).map(i=>`<div class="sld-cmp-item sld-disc">⚠️ <b>${esc(i.item)}</b> — SLD: ${esc(i.sld_says)} / Photo: ${esc(i.photo_shows)}</div>`).join('');
    const missing=(d.missing||[]).map(i=>`<div class="sld-cmp-item sld-miss">❌ <b>${esc(i.item)}</b> — ${esc(i.note)}</div>`).join('');
    g('sldCmpBody').innerHTML=
      (matches?`<div class="sld-cmp-section"><div class="sld-cmp-ttl">✅ Matches</div>${matches}</div>`:'') +
      (discs  ?`<div class="sld-cmp-section"><div class="sld-cmp-ttl">⚠️ Discrepancies</div>${discs}</div>`:'') +
      (missing?`<div class="sld-cmp-section"><div class="sld-cmp-ttl">❌ Missing from photo</div>${missing}</div>`:'');
    g('sldCmp').style.display='block';
  } catch(e) { alert('Comparison failed: '+e.message); }
  finally { btn.disabled=false; spin.style.display='none'; icon.style.display='inline'; txt.textContent='Compare with Photo'; }
}

function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function cap(s){ return s?s[0].toUpperCase()+s.slice(1):''; }

/* ═══════════════ SCHNEIDER DOCUMENTATION LINKS (verified on se.com) ═══════════════ */
const SE_DOCS = {
  'prismaset p':   { name:'PrismaSeT P',    page:'https://www.se.com/uk/en/product-range/22928838-prismaset-p/',              catalogue:'https://www.se.com/us/en/download/document/DESW024EN/' },
  'prismaset g':   { name:'PrismaSeT G',    page:'https://www.se.com/uk/en/product-range/22926020-prismaset-g/',              catalogue:'https://www.se.com/us/en/download/document/DESW025EN/' },
  'okken':         { name:'Okken',           page:'https://www.se.com/uk/en/product-range/1478-okken/',                        catalogue:'https://www.se.com/uk/en/download/document/998-1211343_GMA-GB/' },
  'masterpact mtz':{ name:'MasterPact MTZ', page:'https://www.se.com/uk/en/product-range/63545-masterpact-mtz/',              catalogue:'https://www.se.com/us/en/download/document/LVPED216026EN_WEB/' },
  'masterpact nw': { name:'MasterPact NW',  page:'https://www.se.com/uk/en/product-range/1007-masterpact-nw/',                catalogue:'https://www.se.com/us/en/download/document/0613CT0001/' },
  'masterpact nt': { name:'MasterPact NT',  page:'https://www.se.com/uk/en/product-range/1006-masterpact-nt/',                catalogue:'https://www.se.com/us/en/download/document/0613CT0001/' },
  'masterpact':    { name:'MasterPact',     page:'https://www.se.com/uk/en/product-range/63545-masterpact-mtz/',              catalogue:'https://www.se.com/us/en/download/document/LVPED216026EN_WEB/' },
  'compact nsxm':  { name:'Compact NSXm',   page:'https://www.se.com/uk/en/product-range/39910433-compact-nsxm-new-generation/', catalogue:'https://www.se.com/us/en/download/document/LVPED221001EN/' },
  'compact nsx':   { name:'Compact NSX',    page:'https://www.se.com/uk/en/product-range/39910531-compact-nsx-new-generation/', catalogue:'https://www.se.com/us/en/download/document/LVPED221001EN/' },
  'compact ns':    { name:'Compact NS',     page:'https://www.se.com/uk/en/product-range/1002-compact-ns/',                   catalogue:'https://www.se.com/uk/en/download/document/LVPED211021EN/' },
  'acti9':         { name:'Acti9',          page:'https://www.se.com/uk/en/product-range/7556-miniature-circuit-breaker-acti9-ic60/', catalogue:'https://www.se.com/in/en/download/document/Acti9_Product_Catalouge/' },
  'ic60':          { name:'Acti9 iC60',     page:'https://www.se.com/uk/en/product-range/7556-miniature-circuit-breaker-acti9-ic60/', catalogue:'https://www.se.com/in/en/download/document/Acti9_Product_Catalouge/' },
  'multi9':        { name:'Multi9',         page:'https://www.se.com/uk/en/product-range/1104-multi9/',                       catalogue:null },
  'c60':           { name:'Multi9 C60',     page:'https://www.se.com/uk/en/product-range/1104-multi9/',                       catalogue:null },
};
// Match product name to doc entry (most specific first)
const _SE_KEYS = Object.keys(SE_DOCS);
function getSeDoc(name) {
  const lower = (name || '').toLowerCase();
  const noSpace = lower.replace(/\s+/g, '');
  for (const k of _SE_KEYS) {
    if (lower.includes(k) || noSpace.includes(k.replace(/\s+/g, ''))) return SE_DOCS[k];
  }
  // Fallback: keyword matching for shortened names returned by Gemini
  if (/nsxm/.test(noSpace))                              return SE_DOCS['compact nsxm'];
  if (/nsx/.test(noSpace))                               return SE_DOCS['compact nsx'];
  if (/\bns\b/.test(lower) && !/nsx/.test(noSpace))     return SE_DOCS['compact ns'];
  if (/mtz/.test(noSpace))                               return SE_DOCS['masterpact mtz'];
  if (/\bnw\b/.test(lower))                              return SE_DOCS['masterpact nw'];
  if (/\bnt\b/.test(lower) && !/nsx/.test(noSpace))     return SE_DOCS['masterpact nt'];
  if (/ic60|acti/.test(noSpace))                         return SE_DOCS['acti9'];
  if (/multi9|c60/.test(noSpace))                        return SE_DOCS['multi9'];
  return null;
}
function docLinks(name) {
  const d = getSeDoc(name);
  if (!d) return '';
  return `<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">
    <a href="${d.page}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:7px;background:rgba(61,205,88,.08);border:1px solid rgba(61,205,88,.25);color:#3DCD58;font-size:10px;font-weight:700;text-decoration:none" onmouseover="this.style.background='rgba(61,205,88,.16)'" onmouseout="this.style.background='rgba(61,205,88,.08)'"><i class="fas fa-arrow-up-right-from-square"></i> Product Page</a>
    ${d.catalogue ? `<a href="${d.catalogue}" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:7px;background:rgba(74,158,255,.08);border:1px solid rgba(74,158,255,.25);color:#4A9EFF;font-size:10px;font-weight:700;text-decoration:none" onmouseover="this.style.background='rgba(74,158,255,.16)'" onmouseout="this.style.background='rgba(74,158,255,.08)'"><i class="fas fa-file-pdf"></i> Catalogue</a>` : ''}
  </div>`;
}

/* ═══════════════ CHECKLIST ═══════════════ */
async function loadChecklist() {
  if (!S.task) { g('clBlk').style.display='none'; return; }
  const hasSld = !!sldInput.files.length;
  try {
    const res = await fetch('/api/checklist', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ task_type:S.task, is_live:S.isLive, panel_type:'', has_sld:hasSld, vbb_side:null, cubicle_count:0 })
    });
    const data = await res.json();
    const src      = data.task_items&&data.task_items.length ? data.task_items : data.items;
    const critical = src.filter(i=>i.critical);
    const rest     = src.filter(i=>!i.critical);
    const items    = [...critical,...rest].slice(0,6);
    const list     = g('clList');
    list.innerHTML = items.map(item=>`
      <div class="cl-item${item.critical?' cl-crit':''}" id="cli_${item.id}">
        <input class="cl-cb" type="checkbox" id="cb_${item.id}" onchange="clToggle('${item.id}')">
        <label class="cl-txt" for="cb_${item.id}">${esc(item.text)}${item.critical?'<span class="cl-badge">CRITICAL</span>':''}</label>
      </div>`).join('');
    g('clCount').textContent = `(${items.length})`;
    g('clBlk').style.display = 'block';
    const body=g('clBody'), arr=g('clArr');
    body.classList.add('open'); arr.classList.add('open');
  } catch(e){ console.warn('Checklist fetch failed',e); }
}
function clToggleOpen(){
  g('clBody').classList.toggle('open');
  g('clArr').classList.toggle('open');
}
function clToggle(id){
  const row=g('cli_'+id), cb=g('cb_'+id);
  if(row) row.classList.toggle('cl-done',cb.checked);
}

/* ═══════════════ FLOWCHART WIZARD ═══════════════ */
function fwLockStep(yesId,noId,chosen){
  const yBtn=g(yesId), nBtn=g(noId);
  if(!yBtn||!nBtn) return;
  [yBtn,nBtn].forEach(b=>{ b.disabled=true; b.classList.add('fw-inactive'); b.onclick=null; });
  const picked=chosen==='yes'?yBtn:nBtn;
  picked.classList.remove('fw-inactive');
  picked.classList.add('fw-active');
}
function fwGo(step,answer){
  if(step===1){
    fwLockStep('fwS1yes','fwS1no',answer);
    g('resetLink1').style.display='block';
    unlockCard(2);
    if(answer==='no'){
      S.isLive=false;
      document.querySelectorAll('.t-btn').forEach(b=>{ b.classList.remove('on'); b.disabled=true; });
      S.task=null;
      g('fwS3').style.display='block';
      document.querySelectorAll('.t-btn').forEach(b=>b.disabled=false);
      loadChecklist();
    } else {
      g('lwOverlay').classList.add('lw-open');
    }
  } else if(step===2){
    fwLockStep('fwS2yes','fwS2no',answer);
    unlockCard(3); unlockCard(4);
    if(answer==='no'){
      document.querySelectorAll('.t-btn').forEach(b=>{ b.classList.toggle('on',b.dataset.task==='operation'); b.disabled=true; });
      S.task='operation';
      g('fwS3').style.display='block';
      loadChecklist();
    } else {
      g('fwS3').style.display='block';
    }
  }
}
function lwCancel(){
  g('lwOverlay').classList.remove('lw-open');
  ['fwS1yes','fwS1no'].forEach(id=>{
    const b=g(id); b.disabled=false; b.classList.remove('fw-inactive','fw-active');
  });
  g('fwS1yes').onclick=()=>fwGo(1,'yes');
  g('fwS1no').onclick=()=>fwGo(1,'no');
  g('resetLink1').style.display='none';
  lockCard(2);
}
function lwProceed(){
  g('lwOverlay').classList.remove('lw-open');
  S.isLive=true;
}
function fwSetTask(task){
  document.querySelectorAll('.t-btn').forEach(b=>b.classList.toggle('on',b.dataset.task===task));
  S.task=task; loadChecklist();
}
/* ═══════════════ PRINT REPORT ═══════════════ */
function printReport() {
  const photoDataUrl = imgC.toDataURL('image/jpeg', 0.92);
  const projName   = g('projName').value.trim()   || '—';
  const projSite   = g('projSite').value.trim()   || '—';
  const projPermit = g('projPermit').value.trim() || '—';
  const projTech   = g('projTech').value.trim()   || '—';
  const dateStr    = new Date().toLocaleString('en-GB',{dateStyle:'full',timeStyle:'short'});
  const task       = cap(S.task || 'N/A');
  const isLiveLbl  = S.isLive ? '⚡ LIVE Intervention' : '✓ Dead (De-energised)';
  const ptLbl      = (window._lastPanelType || '—');
  const summary    = (window._lastSummary   || '');
  const warns      = (window._lastWarns     || []);
  const recs       = (window._lastRecs      || []);
  const comps      = (window._lastComps     || []);

  const warnRows = warns.map(w => {
    const crit = w.toLowerCase().includes('arc')||w.toLowerCase().includes('energi')||w.toLowerCase().includes('live')||w.includes('🔥');
    return `<div class="warn-item ${crit?'crit':'warn'}">${esc(w)}</div>`;
  }).join('');

  const recRows = recs.map(r => {
    const pos = r.position==='inside'?'Inside (doors open)':r.position==='outside'?'< 0.3 m':r.position==='near'?'0.3–1 m':'> 1 m';
    const haz = (r.hazards||[]).join(', ') || 'None';
    const ermsLbl = r.erms==='ON'?'ERMS ON':r.erms==='recommended'?'Recommended':r.erms==='OFF'?'ERMS OFF':'—';
    return `<tr><td>${esc(r.operation)}</td><td>${esc(pos)}</td><td>${esc(haz)}</td><td><b>${ermsLbl}</b></td><td>${esc(r.alternative||'—')}</td></tr>`;
  }).join('');

  const compRows = comps.map(d =>
    `<tr><td>${esc(d.label||d.type||'Component')}</td><td>${esc(d.brand||'—')}</td><td>${esc(d.rating||'—')}</td><td>${esc(d.circuit_label||'—')}</td></tr>`
  ).join('');

  const html = `<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>Panel Inspection Report — ${esc(projName)}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap');
  *{margin:0;padding:0;box-sizing:border-box}
  body{font-family:'Inter',system-ui,sans-serif;color:#0f1923;background:#fff;padding:28px 32px;font-size:12px;line-height:1.5}
  /* header */
  .rpt-header{display:flex;align-items:center;justify-content:space-between;padding-bottom:14px;border-bottom:2.5px solid #3DCD58;margin-bottom:20px}
  .rpt-brand{display:flex;align-items:center;gap:12px}
  .rpt-logo{width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,#3DCD58,#2BAE47);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:900;color:#010912;flex-shrink:0}
  .rpt-brand-name{font-size:15px;font-weight:800;color:#0f1923;letter-spacing:-.2px}
  .rpt-brand-sub{font-size:9px;color:#6A9EC0;text-transform:uppercase;letter-spacing:.6px;margin-top:1px}
  .rpt-date{font-size:10px;color:#555;text-align:right}
  .rpt-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#3DCD58;margin-bottom:2px}
  /* proj info */
  .proj-box{background:#f7fdf9;border:1px solid #d4f0dc;border-radius:12px;padding:16px 20px;margin-bottom:16px}
  .proj-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
  .pf label{font-size:8.5px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:#888;display:block;margin-bottom:3px}
  .pf .val{font-size:12px;font-weight:600;color:#0f1923}
  /* photo */
  .photo-box{margin-bottom:16px;border-radius:12px;overflow:hidden;border:1px solid #e0e0e0;text-align:center;background:#f5f5f5}
  .photo-box img{max-width:100%;max-height:360px;object-fit:contain;display:block;margin:0 auto}
  /* section */
  .sec{margin-bottom:16px}
  .sec-ttl{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.9px;color:#888;margin-bottom:8px;display:flex;align-items:center;gap:5px;padding-bottom:5px;border-bottom:1px solid #e8e8e8}
  /* badges */
  .badge-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
  .badge{padding:4px 12px;border-radius:14px;font-size:11px;font-weight:700;border:1px solid}
  .badge-g{color:#008A2E;border-color:#b3e6c4;background:#f0fbf4}
  .badge-b{color:#1565c0;border-color:#b3d4f7;background:#f0f7ff}
  .badge-p{color:#6a1b9a;border-color:#d7b3f5;background:#fdf4ff}
  .badge-n{color:#555;border-color:#ddd;background:#f9f9f9}
  .p-sum{font-size:12px;color:#444;line-height:1.7}
  /* intervention */
  .int-row{display:flex;gap:10px;margin-bottom:8px}
  .int-chip{padding:5px 14px;border-radius:8px;font-size:11px;font-weight:700}
  .int-live{background:#fff0f1;border:1px solid #ffb3ba;color:#c00}
  .int-dead{background:#f0fbf4;border:1px solid #b3e6c4;color:#007a20}
  .int-task{background:#f0f7ff;border:1px solid #b3d4f7;color:#1565c0}
  /* stats */
  .stats-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
  .stat{padding:5px 12px;background:#f5f5f5;border:1px solid #e0e0e0;border-radius:7px;font-size:11px;color:#444}
  .stat b{color:#0f1923;font-weight:700}
  /* warnings */
  .warn-item{padding:8px 12px;border-radius:8px;margin-bottom:5px;font-size:11px;line-height:1.55}
  .warn-item.crit{background:#fff4f4;border:1px solid #ffc5c5;color:#b00}
  .warn-item.warn{background:#fffbf0;border:1px solid #ffe0a0;color:#7a4f00}
  /* table */
  table{width:100%;border-collapse:collapse;font-size:10.5px}
  th{background:#f5f5f5;color:#555;font-weight:700;text-transform:uppercase;letter-spacing:.5px;font-size:8.5px;padding:7px 9px;text-align:left;border-bottom:1.5px solid #ddd}
  td{padding:7px 9px;border-bottom:1px solid #eee;color:#333;vertical-align:top}
  tr:last-child td{border-bottom:none}
  /* footer */
  .rpt-footer{margin-top:24px;padding-top:12px;border-top:1px solid #e0e0e0;font-size:9.5px;color:#888;display:flex;justify-content:space-between}
  @media print{body{padding:16px 20px}@page{margin:16mm}}
</style></head><body>

<div class="rpt-header">
  <div class="rpt-brand">
    <div class="rpt-logo">SE</div>
    <div>
      <div class="rpt-brand-name">Panel Inspector Pro</div>
      <div class="rpt-brand-sub">EcoStruxure™ AI · Schneider Electric</div>
    </div>
  </div>
  <div class="rpt-date">
    <div class="rpt-title">Inspection Report</div>
    <div>${esc(dateStr)}</div>
  </div>
</div>

<div class="proj-box">
  <div class="proj-grid">
    <div class="pf"><label>Project / Work Order</label><div class="val">${esc(projName)}</div></div>
    <div class="pf"><label>Site / Location</label><div class="val">${esc(projSite)}</div></div>
    <div class="pf"><label>Work Permit / Ref No.</label><div class="val">${esc(projPermit)}</div></div>
    <div class="pf"><label>Technician</label><div class="val">${esc(projTech)}</div></div>
  </div>
</div>

<div class="sec">
  <div class="sec-ttl">Intervention Setup</div>
  <div class="int-row">
    <span class="int-chip ${S.isLive?'int-live':'int-dead'}">${esc(isLiveLbl)}</span>
    <span class="int-chip int-task">Task: ${esc(task)}</span>
  </div>
</div>

<div class="photo-box">
  <img src="${photoDataUrl}" alt="Panel photo">
</div>

<div class="sec">
  <div class="sec-ttl">Panel Identification</div>
  <div class="badge-row">
    <span class="badge badge-g">${esc(ptLbl)}</span>
  </div>
  ${summary ? `<p class="p-sum">${esc(summary)}</p>` : ''}
</div>

${comps.length ? `
<div class="sec">
  <div class="sec-ttl">Detected Components (${comps.length})</div>
  <table>
    <thead><tr><th>Label</th><th>Brand</th><th>Rating</th><th>Circuit</th></tr></thead>
    <tbody>${compRows}</tbody>
  </table>
</div>` : ''}

${warns.length ? `
<div class="sec">
  <div class="sec-ttl">Safety Warnings</div>
  ${warnRows}
</div>` : ''}

${recs.length ? `
<div class="sec">
  <div class="sec-ttl">ERMS Operations — ${esc(task)}</div>
  <table>
    <thead><tr><th>Operation</th><th>Position</th><th>Hazards</th><th>ERMS</th><th>Alternative</th></tr></thead>
    <tbody>${recRows}</tbody>
  </table>
</div>` : ''}

<div class="rpt-footer">
  <span>Panel Inspector Pro · Schneider Electric · Internal Use Only</span>
  <span>Generated ${esc(dateStr)}</span>
</div>

<script>setTimeout(()=>window.print(),400);<\/script>
</body></html>`;

  const w = window.open('', '_blank');
  if (w) { w.document.write(html); w.document.close(); }
  else alert('Please allow pop-ups to generate the PDF report.');
}

function fwReset(){
  ['fwS1yes','fwS1no'].forEach(id=>{
    const b=g(id); b.disabled=false; b.classList.remove('fw-inactive','fw-active');
  });
  g('fwS1yes').onclick=()=>fwGo(1,'yes');
  g('fwS1no').onclick=()=>fwGo(1,'no');
  ['fwS2yes','fwS2no'].forEach(id=>{
    const b=g(id); if(!b) return;
    b.disabled=false; b.classList.remove('fw-inactive','fw-active');
  });
  g('fwS2yes').onclick=()=>fwGo(2,'yes');
  g('fwS2no').onclick=()=>fwGo(2,'no');
  g('fwS3').style.display='none';
  g('resetLink1').style.display='none';
  g('clBlk').style.display='none';
  document.querySelectorAll('.t-btn').forEach(b=>{ b.classList.remove('on'); b.disabled=false; });
  lockCard(2); lockCard(3); lockCard(4);
  setStepState(1,'active');
  S.task='commissioning'; S.isLive=false;
  document.querySelector('.t-btn[data-task="commissioning"]').classList.add('on');
}
