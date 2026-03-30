const _cmpCharts = {};
const APP_COLORS   = ['#4dabf7','#69db7c','#fcc419','#ff8787','#da77f2','#74c0fc','#a9e34b','#ffa94d'];
const AGENT_COLORS = ['#4dabf7','#69db7c','#fcc419','#ff8787','#da77f2','#ff922b','#a9e34b','#74c0fc'];

function fmtDur(s) {
  if (!s||s<0) return '0m';
  if (s < 60)   return s + 's';
  if (s < 3600) return Math.round(s/60) + 'm';
  const h = Math.floor(s/3600), m = Math.round((s%3600)/60);
  return m ? `${h}h ${m}m` : `${h}h`;
}

function prodScore(summary) {
  const PROD   = ['slack','teams','msteams','outlook','code','devenv','winword','excel','powerpnt','zoom','onenote','pycharm','rider','webstorm','idea64'];
  const UNPROD = ['spotify','vlc','discord','reddit','netflix','youtube'];
  const SKIP   = ['lockapp','lockscreenhost','shellhost','shellexperiencehost','startmenuexperiencehost','searchhost','searchapp','textinputhost','applicationframehost','runtimebroker','taskhostw','sihost','ctfmon','fontdrvhost','dwm','winlogon','logonui','conhost','condrv'];
  let p=0,u=0,n=0;
  for (const r of (summary||[])) {
    const proc = (r.process_name||'').toLowerCase().replace('.exe','');
    const sec  = r.total_s||0;
    if (SKIP.some(x=>proc===x))              continue;
    if (PROD.some(x=>proc.startsWith(x)))   p+=sec;
    else if (UNPROD.some(x=>proc.startsWith(x))) u+=sec;
    else n+=sec;
  }
  const tot=p+u+n;
  return tot ? Math.round((p+n*0.4)/tot*100) : 0;
}

function prodColor(score) { return score>=70?'#51cf66':score>=45?'#fcc419':'#ff6b6b'; }
function prodLabel(score) { return score>=70?'High':score>=45?'Medium':'Low'; }

function cmpSelCount() {
  const n = document.querySelectorAll('.cmp-agent-cb:checked').length;
  const badge = document.getElementById('cmp-sel-count');
  if (badge) badge.textContent = n + ' selected';
}

function cmpSelectAll(state) {
  document.querySelectorAll('.cmp-agent-row').forEach(row => {
    if (row.style.display === 'none') return;
    const cb = row.querySelector('.cmp-agent-cb');
    if (cb) cb.checked = state;
  });
  cmpSelCount();
  cmpLoad();
}

function cmpFilterAgents(q) {
  const term = q.toLowerCase();
  document.querySelectorAll('.cmp-agent-row').forEach(row => {
    const match = (row.dataset.hostname || '').includes(term);
    row.style.display = match ? '' : 'none';
  });
}

async function cmpLoad() {
  const selected = [...document.querySelectorAll('.cmp-agent-cb:checked')].map(cb=>cb.value);
  const daysEl = document.getElementById('cmp-days');
  const days = daysEl ? parseInt(daysEl.value) || 7 : 7;
  const grid = document.getElementById('cmp-grid');
  const spin = document.getElementById('cmp-spinner');
  const empty = document.getElementById('cmp-empty');

  if (!selected.length) {
    if (grid)  grid.style.display='none';
    if (spin)  spin.style.display='none';
    if (empty) empty.style.display='';
    return;
  }
  if (grid)  grid.style.display='none';
  if (spin)  spin.style.display='block';
  if (empty) empty.style.display='none';

  for (const id in _cmpCharts) { try { _cmpCharts[id].destroy(); } catch(_){} delete _cmpCharts[id]; }

  let data;
  try {
    const res = await fetch(`/api/rmm/eagle-eyes/compare-data?agents=${encodeURIComponent(selected.join(','))}&days=${days}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch(e) {
    console.error('[compare] fetch failed:', e);
    if (spin) spin.style.display='none';
    if (grid) { grid.innerHTML=`<div class="col-12"><div class="alert alert-danger"><i class="bi bi-exclamation-triangle me-2"></i>Error loading data: ${e.message||e}</div></div>`; grid.style.display=''; }
    return;
  }

  if (spin)  spin.style.display='none';
  if (!data || !data.ok) { if (empty) empty.style.display=''; return; }

  const n = selected.length;
  const colClass = n === 1 ? 'col-12 col-lg-8 col-xl-6 mx-auto'
                 : n === 2 ? 'col-12 col-md-6'
                 : n === 3 ? 'col-12 col-md-6 col-xl-4'
                 : n <= 8  ? 'col-12 col-sm-6 col-xl-3'
                 :            'col-12 col-sm-6 col-md-4';

  grid.innerHTML = selected.map((aid, idx) => {
    const r            = data.results[aid] || {hostname:aid, summary:[], daily:[], total_s:0};
    const score        = prodScore(r.summary);
    const topApps      = (r.summary||[]).slice(0, 6);
    const topApp       = topApps[0] || null;
    const lineId       = `line-${aid.replace(/[^a-z0-9]/gi,'_')}`;
    const barId        = `bar-${aid.replace(/[^a-z0-9]/gi,'_')}`;
    const col          = AGENT_COLORS[idx % AGENT_COLORS.length];
    const active       = (r.daily||[]).filter(d => d.total_s > 0).length;
    const avgDaily     = active ? Math.round(r.total_s / active) : 0;
    const busiestDay   = (r.daily||[]).reduce((best, d) => (!best || d.total_s > best.total_s) ? d : best, null);
    const totalEvents  = (r.summary||[]).reduce((sum, a) => sum + (a.events || 0), 0);
    const avgSession   = totalEvents ? Math.round(r.total_s / totalEvents) : 0;
    const busiestLabel = busiestDay ? (p => `${parseInt(p[1])}/${parseInt(p[2])}`)(busiestDay.day.split('-')) : '';
    const topPct       = r.total_s && topApp ? Math.round(topApp.total_s / r.total_s * 100) : 0;

    return `<div class="${colClass}">
      <div class="card border-0 shadow h-100 cmp-card" style="background:#0f1923;border-radius:12px;overflow:hidden;">
        <div class="cmp-card-header px-3 py-2 d-flex align-items-center justify-content-between" style="border-bottom:1px solid #1a3050;">
          <div class="d-flex align-items-center gap-2 overflow-hidden">
            <div style="width:9px;height:9px;border-radius:50%;background:${col};box-shadow:0 0 6px ${col};flex-shrink:0;"></div>
            <span class="fw-bold text-white text-truncate" style="font-size:.95rem;">${r.hostname}</span>
          </div>
          <a href="/rmm/eagle-eyes/${encodeURIComponent(aid)}" class="btn btn-outline-info py-0 px-2 flex-shrink-0 ms-2" style="font-size:.7rem;border-radius:6px;">
            <i class="bi bi-eye me-1"></i>View
          </a>
        </div>
        <div class="card-body p-3">
          <!-- Stats 2×2 -->
          <div class="row g-2 mb-2">
            <div class="col-6"><div class="stat-pill">
              <div class="sp-label">Total Time</div>
              <div class="sp-value text-info">${fmtDur(r.total_s)}</div>
            </div></div>
            <div class="col-6"><div class="stat-pill">
              <div class="sp-label">Avg / Day</div>
              <div class="sp-value text-white">${avgDaily ? fmtDur(avgDaily) : '—'}</div>
            </div></div>
            <div class="col-6"><div class="stat-pill">
              <div class="sp-label">Productivity</div>
              <div class="sp-value" style="color:${prodColor(score)};">${score}%
                <span style="font-size:.58rem;opacity:.75;margin-left:3px;">${prodLabel(score)}</span>
              </div>
              <div class="prod-bar mt-1"><div style="width:${score}%;background:${prodColor(score)};"></div></div>
            </div></div>
            <div class="col-6"><div class="stat-pill">
              <div class="sp-label">Active Days</div>
              <div class="sp-value text-white">${active}<span style="font-size:.65rem;color:#566779;font-weight:400;">/${days}</span></div>
            </div></div>
          </div>
          <!-- Info strip -->
          <div class="cmp-info-strip mb-2">
            ${busiestDay ? `<span class="cmp-chip" title="Busiest day"><i class="bi bi-bar-chart-fill" style="color:#4dabf7;"></i>${busiestLabel} · ${fmtDur(busiestDay.total_s)}</span>` : ''}
            ${r.summary.length ? `<span class="cmp-chip"><i class="bi bi-grid-3x3-gap" style="color:#8899aa;"></i>${r.summary.length} apps</span>` : ''}
            ${avgSession >= 30 ? `<span class="cmp-chip" title="Avg time per window"><i class="bi bi-stopwatch" style="color:#8899aa;"></i>${fmtDur(avgSession)}/window</span>` : ''}
          </div>
          ${topApp ? `
          <div class="d-flex align-items-center gap-2 mb-3 px-2 py-1" style="background:#0a1a28;border-radius:8px;">
            <i class="bi bi-star-fill text-warning" style="font-size:.75rem;flex-shrink:0;"></i>
            <span class="text-muted" style="font-size:.72rem;white-space:nowrap;">Top app:</span>
            <span class="fw-semibold text-warning text-truncate" style="font-size:.8rem;" title="${topApp.process_name}">${topApp.process_name}</span>
            <span class="ms-auto text-muted flex-shrink-0" style="font-size:.72rem;">${fmtDur(topApp.total_s)}</span>
            ${topPct ? `<span class="cmp-chip ms-1" style="font-size:.62rem;">${topPct}%</span>` : ''}
          </div>` : ''}
          <!-- Daily chart -->
          <div class="mb-1 chart-label">Daily Activity</div>
          <div style="position:relative;height:110px;margin-bottom:14px;"><canvas id="${lineId}"></canvas></div>
          <!-- App breakdown -->
          <div class="mb-1 chart-label">App Breakdown</div>
          <div style="position:relative;height:${Math.max(topApps.length,3)*26}px;"><canvas id="${barId}"></canvas></div>
        </div>
      </div>
    </div>`;
  }).join('');

  if (grid) grid.style.display = '';

  if (typeof Chart === 'undefined') {
    console.error('[compare] Chart.js not loaded — charts skipped');
    return;
  }

  for (let i=0; i<selected.length; i++) {
    const aid    = selected[i];
    const r      = data.results[aid] || {};
    const col    = AGENT_COLORS[i % AGENT_COLORS.length];
    const daily  = r.daily || [];
    const topApps= (r.summary||[]).slice(0,6);

    // Daily bar chart
    const lineId = `line-${aid.replace(/[^a-z0-9]/gi,'_')}`;
    const lineEl = document.getElementById(lineId);
    if (lineEl && daily.length) {
      try {
        _cmpCharts[lineId] = new Chart(lineEl.getContext('2d'), {
          type:'bar',
          data:{
            labels: daily.map(d=>{ const p=d.day.split('-'); return `${parseInt(p[1])}/${parseInt(p[2])}`; }),
            datasets:[{
              data: daily.map(d=>+((d.total_s||0)/3600).toFixed(2)),
              backgroundColor: col+'55', borderColor: col,
              borderWidth:2, borderRadius:4, hoverBackgroundColor:col+'aa',
            }]
          },
          options:{
            responsive:true, maintainAspectRatio:false, animation:{duration:400},
            plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>' '+fmtDur(Math.round(c.parsed.y*3600))}}},
            scales:{
              x:{grid:{display:false}, ticks:{color:'#4a5a6a',font:{size:9},maxTicksLimit:days<=7?7:12}},
              y:{grid:{color:'rgba(255,255,255,0.04)'}, ticks:{color:'#4a5a6a',font:{size:9},callback:v=>v+'h'}, min:0}
            }
          }
        });
      } catch(ce) { console.error('[compare] line chart error:', ce); }
    }

    // Horizontal bar – top apps
    const barId = `bar-${aid.replace(/[^a-z0-9]/gi,'_')}`;
    const barEl = document.getElementById(barId);
    if (barEl && topApps.length) {
      try {
        _cmpCharts[barId] = new Chart(barEl.getContext('2d'), {
          type:'bar',
          data:{
            labels: topApps.map(a=>a.process_name),
            datasets:[{
              data: topApps.map(a=>+((a.total_s||0)/3600).toFixed(2)),
              backgroundColor: APP_COLORS.slice(0,topApps.length).map(c=>c+'cc'),
              borderColor:     APP_COLORS.slice(0,topApps.length),
              borderWidth:1, borderRadius:4,
            }]
          },
          options:{
            indexAxis:'y', responsive:true, maintainAspectRatio:false, animation:{duration:400},
            plugins:{legend:{display:false}, tooltip:{callbacks:{label:c=>' '+fmtDur(Math.round(c.parsed.x*3600))}}},
            scales:{
              x:{grid:{color:'rgba(255,255,255,0.04)'}, ticks:{color:'#4a5a6a',font:{size:9},callback:v=>v+'h'}, min:0},
              y:{grid:{display:false}, ticks:{color:'#8899aa',font:{size:10}}}
            }
          }
        });
      } catch(ce) { console.error('[compare] bar chart error:', ce); }
    }
  }
}

document.addEventListener('DOMContentLoaded', () => { cmpSelCount(); cmpLoad(); });
