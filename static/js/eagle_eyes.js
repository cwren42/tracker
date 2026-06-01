/* eagle_eyes.html extracted JS */
/* agentId and agentTzOffsetH injected by template config block */

let lastSummary = [], lastEvents = [], lastSites = [];
let _arTimer = null;

function dateParams() {
  const sel = document.getElementById('ee-days').value;
  if (sel === 'custom') {
    const f = document.getElementById('ee-from').value;
    const t = document.getElementById('ee-to').value;
    if (f && t) return `from_date=${f}&to_date=${t}`;
  }
  return `days=${parseInt(sel)||7}`;
}
function onPeriodChange() {
  const sel = document.getElementById('ee-days').value;
  const cr  = document.getElementById('ee-custom-range');
  if (sel === 'custom') {
    cr.classList.remove('d-none');
    const today = new Date();
    const from  = new Date(today.getTime() - 7 * 86400000);
    document.getElementById('ee-to').value   = today.toLocaleDateString('en-CA');
    document.getElementById('ee-from').value = from.toLocaleDateString('en-CA');
  } else {
    cr.classList.add('d-none');
    eeLoad();
  }
}
function workHoursFilter(events) {
  if (!document.getElementById('ee-work-hours').checked) return events;
  return events.filter(e => {
    try {
      const h = new Date(e.captured_at).getHours();
      return h >= 8 && h < 18;
    } catch(_) { return true; }
  });
}
function rebuildSummary(events) {
  const m = {};
  for (const e of events) {
    const p = e.process_name || '';
    if (!m[p]) m[p] = {process_name:p, events:0, total_s:0};
    m[p].events++; m[p].total_s += e.duration_s||0;
  }
  return Object.values(m).sort((a,b)=>b.total_s-a.total_s);
}
function toggleAutoRefresh() {
  const btn = document.getElementById('ee-ar-btn');
  if (_arTimer) {
    clearInterval(_arTimer); _arTimer = null;
    btn.innerHTML = '<i class="bi bi-play-circle me-1"></i>Auto';
    btn.className = btn.className.replace('btn-success','btn-outline-secondary');
  } else {
    _arTimer = setInterval(eeLoad, 60000);
    btn.innerHTML = '<i class="bi bi-stop-circle me-1"></i>Auto';
    btn.className = btn.className.replace('btn-outline-secondary','btn-success');
  }
}
function exportCSV(type) {
  let rows, fname, hdr;
  if (type === 'events') {
    hdr  = ['Time','Process','Window Title','Duration (s)'];
    rows = lastEvents.map(e=>[fmtTs(e.captured_at),e.process_name||'',(e.window_title||'').replace(/"/g,'""'),e.duration_s||0]);
    fname = `eagle-events-${agentId}.csv`;
  } else if (type === 'summary') {
    hdr  = ['Application','Events','Total Seconds','Total Time'];
    rows = lastSummary.map(r=>[r.process_name||'',r.events||0,r.total_s||0,fmtDuration(r.total_s)]);
    fname = `eagle-summary-${agentId}.csv`;
  } else {
    hdr  = ['Site','Total Seconds','Total Time'];
    rows = lastSites.map(s=>[s.site||'',s.total_s||0,fmtDuration(s.total_s)]);
    fname = `eagle-sites-${agentId}.csv`;
  }
  const csv = [hdr,...rows].map(r=>r.map(v=>`"${v}"`).join(',')).join('\r\n');
  const a = Object.assign(document.createElement('a'),{href:'data:text/csv;charset=utf-8,'+encodeURIComponent(csv),download:fname});
  a.click();
}
async function takeScreenshot() {
  const btn = document.getElementById('ee-ss-take-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
  try { await fetch(`/api/rmm/screenshot/${encodeURIComponent(agentId)}`,{method:'POST'}); } catch(_){}
  setTimeout(()=>loadScreenshots(dateParams()), 8000);
  setTimeout(()=>{ btn.disabled=false; btn.innerHTML='<i class="bi bi-camera me-1"></i>Take Now'; }, 5000);
}
async function setScreenshotInterval() {
  const iv = parseInt(document.getElementById('ee-ss-interval').value);
  try {
    await fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}`,{
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({enabled:true, screenshot_interval_min:iv})
    });
  } catch(_){}
}

/* ── App category map ─────────────────────────────────────────────────── */
const CAT = {
  browser: { label:'Browser',       color:'#4dabf7', procs:['msedge','chrome','firefox','brave','opera','iexplore','safari','vivaldi','arc'] },
  comms:   { label:'Communication', color:'#69db7c', procs:['teams','slack','zoom','outlook','thunderbird','discord','lync','skype','mail','msteams'] },
  dev:     { label:'Development',   color:'#f783ac', procs:['code','devenv','rider','idea','pycharm','webstorm','androidstudio','gitbash','windowsterminal','cmd','powershell','git','notepad++','vim','nvim','sublime_text','atom','fleet'] },
  office:  { label:'Productivity',  color:'#ffa94d', procs:['winword','excel','powerpnt','onenote','access','msaccess','word','acrobat','acrord32','foxit','libreoffice','soffice','calc','impress','writer'] },
  remote:  { label:'Remote / IT',   color:'#cc5de8', procs:['mstsc','anydesk','vnc','putty','winscp','filezilla','ssh'] },
  media:   { label:'Media',         color:'#20c997', procs:['vlc','spotify','wmplayer','groove','itunes','mpc-hc','mpv'] },
  system:  { label:'System',        color:'#868e96', procs:['explorer','taskmgr','regedit','mmc','msiexec','svchost','control'] },
};

function categorise(procs) {
  const b = {};
  for (const c in CAT) b[c] = 0;
  b.other = 0;
  for (const { process_name, total_s } of procs) {
    const p = (process_name || '').toLowerCase();
    let hit = false;
    for (const c in CAT) {
      if (CAT[c].procs.some(x => p === x || p.startsWith(x))) { b[c] += total_s || 0; hit = true; break; }
    }
    if (!hit) b.other += total_s || 0;
  }
  return b;
}

/* ── Helpers ──────────────────────────────────────────────────────────── */
function fmtDuration(s) {
  if (!s) return '0s';
  if (s < 60) return s + 's';
  if (s < 3600) return Math.round(s/60) + 'm';
  const h = Math.floor(s/3600), m = Math.round((s%3600)/60);
  return m ? `${h}h ${m}m` : `${h}h`;
}
function fmtTs(t) {
  if (!t) return '';
  try {
    const d = new Date(t);
    if (isNaN(d)) return t;
    return d.toLocaleString('en-US', {month:'numeric', day:'numeric', year:'numeric', hour:'numeric', minute:'2-digit', hour12:true});
  } catch(_) { return t; }
}
function fmtHour(h) {
  if (h === 0) return '12 AM'; if (h < 12) return h + ' AM';
  if (h === 12) return '12 PM'; return (h-12) + ' PM';
}
const gridColor = 'rgba(255,255,255,0.07)', textColor = '#adb5bd';
const _ch = {};
function mkChart(id, cfg) { if (_ch[id]) _ch[id].destroy(); _ch[id] = new Chart(document.getElementById(id).getContext('2d'), cfg); }
function vis(id, show) { const el = document.getElementById(id); if (el) el.style.display = show ? '' : 'none'; }
function spinSet(prefix, state) {
  vis(prefix+'-spin', state === 'spin');
  vis(prefix+'-chart', state === 'chart');
  vis(prefix+'-wrap',  state === 'wrap');
  vis(prefix+'-empty', state === 'empty');
}
/* ── Productivity score ──────────────────────────────────────────── */
function renderProductivity(summary) {
  const el = document.getElementById('stat-prod-score');
  const sub = document.getElementById('stat-prod-sub');
  if (!summary.length) { el.textContent='—'; sub.textContent='No data'; return; }
  let productiveS=0, unproductiveS=0, neutralS=0;
  for (const r of summary) {
    const s = r.total_s||0;
    if (r.productivity === 'productive') productiveS += s;
    else if (r.productivity === 'unproductive') unproductiveS += s;
    else neutralS += s;
  }
  const total = productiveS + unproductiveS + neutralS;
  if (total === 0) { el.textContent='—'; sub.textContent='No data'; return; }
  if (productiveS + unproductiveS === 0) {
    el.textContent='—';
    el.style.color='#adb5bd';
    sub.textContent='No apps classified yet';
    return;
  }
  // Score = productive / total tracked time (neutral counts against score)
  const score = Math.round(productiveS / total * 100);
  el.textContent = score+'%';
  el.style.color = score>=75?'#51cf66':score>=50?'#fcc419':'#ff6b6b';
  sub.textContent = `${fmtDuration(productiveS)} productive · ${fmtDuration(unproductiveS)} unproductive · ${fmtDuration(neutralS)} neutral`;
}
/* ── Stat cards ───────────────────────────────────────────────────────── */
function renderStats(summary, daily, hourly) {
  const totalS = summary.reduce((a,r) => a+(r.total_s||0), 0);
  document.getElementById('stat-total-time').textContent = fmtDuration(totalS);
  document.getElementById('stat-total-sub').textContent  = summary.length + ' apps tracked';

  if (summary.length) {
    document.getElementById('stat-top-app').textContent      = summary[0].process_name || '—';
    document.getElementById('stat-top-app-time').textContent = fmtDuration(summary[0].total_s);
  }

  const activeDays = daily.filter(d => d.total_s > 0).length;
  document.getElementById('stat-active-days').textContent    = activeDays;
  // Use actual period length: daily is now a full series, so daily.length = period days
  const periodDays = daily.length || (parseInt(document.getElementById('ee-days').value) || 7);
  document.getElementById('stat-active-days-sub').textContent = `of ${periodDays} days in period`;

  const peak = hourly.length ? hourly.reduce((a,b) => b.total_s>a.total_s?b:a, hourly[0]) : null;
  if (peak && peak.total_s > 0) {
    document.getElementById('stat-peak-hour').textContent     = fmtHour(peak.hour);
    document.getElementById('stat-peak-hour-sub').textContent = fmtDuration(peak.total_s) + ' total activity';
  } else {
    document.getElementById('stat-peak-hour').textContent = '—';
    document.getElementById('stat-peak-hour-sub').textContent = '';
  }
}

/* ── App Usage bar ────────────────────────────────────────────────────── */
function renderUsage(summary) {
  if (!summary.length) { spinSet('ee-usage','empty'); return; }
  const top = summary.slice(0,15);
  document.getElementById('ee-usage-range').textContent = top.length + ' apps';
  spinSet('ee-usage','wrap');
  // Size the relative wrapper (not the canvas): with maintainAspectRatio:false
  // Chart.js sizes the canvas to its parent box, so the fixed height must live
  // on the container or the canvas grows unbounded on every resize tick.
  document.getElementById('ee-usage-wrap').style.height = Math.max(200, top.length*28+50) + 'px';
  mkChart('ee-usage-canvas', {
    type:'bar',
    data:{ labels: top.map(r=>r.process_name||'unknown'),
           datasets:[{ label:'Minutes', data: top.map(r=>Math.round((r.total_s||0)/60)),
                       backgroundColor: top.map((_,i)=>`hsl(${45+i*16},80%,52%)`),
                       borderRadius:4, borderSkipped:false }] },
    options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false},
                tooltip:{callbacks:{label:c=>' '+fmtDuration(top[c.dataIndex].total_s)}} },
      scales:{ x:{grid:{color:gridColor},ticks:{color:textColor},title:{display:true,text:'minutes',color:textColor}},
               y:{grid:{color:'transparent'},ticks:{color:textColor}} } }
  });
}

/* ── Category donut ───────────────────────────────────────────────────── */
function renderCategories(summary) {
  const buckets = categorise(summary);
  const entries = [...Object.keys(buckets).map(k=>({
    label: k==='other'?'Other':CAT[k].label,
    s: buckets[k],
    color: k==='other'?'#495057':CAT[k].color
  }))].filter(e=>e.s>0).sort((a,b)=>b.s-a.s);
  const totalS = entries.reduce((a,e)=>a+e.s,0);
  document.getElementById('ee-cat-total').textContent = fmtDuration(totalS);
  if (!entries.length) {
    vis('ee-cat-spin',false); vis('ee-cat-wrap',false); vis('ee-cat-empty',true);
    return;
  }
  vis('ee-cat-spin',false); vis('ee-cat-wrap',true); vis('ee-cat-empty',false);
  mkChart('ee-cat-chart', {
    type:'doughnut',
    data:{ labels:entries.map(e=>e.label),
           datasets:[{data:entries.map(e=>Math.round(e.s/60)),
                      backgroundColor:entries.map(e=>e.color),
                      borderColor:'#121212',borderWidth:2,hoverOffset:6}] },
    options:{ responsive:true, maintainAspectRatio:true, cutout:'62%',
      plugins:{ legend:{position:'right',labels:{color:textColor,boxWidth:12,font:{size:11},padding:10}},
                tooltip:{callbacks:{label:c=>{const pct=totalS?Math.round(c.parsed/(totalS/60)*100):0;return ` ${fmtDuration(entries[c.dataIndex].s)} (${pct}%)`;}}}} }
  });
}

/* ── Daily activity ───────────────────────────────────────────────────── */
function renderDaily(daily) {
  const active = daily.filter(d=>d.total_s>0);
  if (active.length < 2) { spinSet('ee-daily','empty'); return; }
  document.getElementById('ee-daily-range').textContent = active.length + ' active days';
  spinSet('ee-daily','chart');
  mkChart('ee-daily-chart', {
    type:'line',
    data:{ labels: daily.map(d=>{ const parts=d.day.split('-'); return `${parseInt(parts[1])}/${parseInt(parts[2])}`; }),
           datasets:[{ label:'Hours active', data: daily.map(d=>+((d.total_s||0)/3600).toFixed(2)),
                       fill:true, tension:0.35, borderColor:'#51cf66',
                       backgroundColor:'rgba(81,207,102,0.12)',
                       pointRadius: daily.length>14?2:4, pointBackgroundColor:'#51cf66' }] },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false},
                tooltip:{callbacks:{label:c=>' '+fmtDuration(Math.round(c.parsed.y*3600))}} },
      scales:{ x:{grid:{color:gridColor},ticks:{color:textColor,maxTicksLimit:12}},
               y:{grid:{color:gridColor},ticks:{color:textColor,callback:v=>v+'h'},min:0,
                  title:{display:true,text:'hours',color:textColor}} } }
  });
}

/* ── Hourly heatmap ───────────────────────────────────────────────────── */
function renderHourly(hourly) {
  if (!hourly.some(h=>h.total_s>0)) { spinSet('ee-hourly','empty'); return; }
  const values = hourly.map(h=>Math.round((h.total_s||0)/60));
  const maxV = Math.max(...values,1);
  document.getElementById('ee-hourly-range').textContent = 'by hour';
  spinSet('ee-hourly','chart');
  mkChart('ee-hourly-chart', {
    type:'bar',
    data:{ labels: hourly.map(h=>fmtHour(h.hour)),
           datasets:[{ label:'Minutes', data:values,
                       backgroundColor: values.map(v=>`rgba(255,193,7,${0.2+0.8*(v/maxV)})`),
                       borderColor:     values.map(v=>`rgba(255,193,7,${0.5+0.5*(v/maxV)})`),
                       borderWidth:1, borderRadius:3 }] },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false},
                tooltip:{callbacks:{label:c=>' '+fmtDuration(c.parsed.y*60)}} },
      scales:{ x:{grid:{color:'transparent'},
                  ticks:{color:textColor,maxRotation:60,callback:function(v,i){return i%3===0?this.getLabelForValue(v):'';}}},
               y:{grid:{color:gridColor},ticks:{color:textColor,callback:v=>v+'m'},min:0,
                  title:{display:true,text:'minutes',color:textColor}} } }
  });
}

/* ── Event timeline ───────────────────────────────────────────────────── */
function renderEvents(events) {
  if (!events.length) { spinSet('ee-evt','empty'); return; }
  document.getElementById('ee-evt-range').textContent = events.length + ' events';
  const ICONS = {msedge:'browser-edge',chrome:'browser-chrome',firefox:'browser-firefox',teams:'chat-square-text',slack:'chat-left-dots',zoom:'camera-video',code:'code-slash',devenv:'code-slash',outlook:'envelope',excel:'file-earmark-spreadsheet',winword:'file-earmark-word',powerpnt:'file-earmark-slides',mstsc:'display',explorer:'folder2-open'};
  function icon(p){ const k=(p||'').toLowerCase(); for(const[n,ic]of Object.entries(ICONS))if(k.startsWith(n))return `<i class="bi bi-${ic} me-1 text-muted"></i>`; return '<i class="bi bi-window me-1 text-muted"></i>'; }
  document.getElementById('ee-evt-body').innerHTML = events.map(e=>{
    const dur=e.duration_s||0;
    const bd=dur>=1800?'bg-danger':dur>=600?'bg-warning text-dark':'bg-secondary';
    return `<tr>
      <td class="text-muted" style="white-space:nowrap;font-size:.75rem;">${fmtTs(e.captured_at)}</td>
      <td style="max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${icon(e.process_name)}<span title="${e.process_name||''}">${e.process_name||'—'}</span></td>
      <td style="max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.78rem;" title="${(e.window_title||'').replace(/"/g,'&quot;')}">${e.window_title||'—'}</td>
      <td class="text-end"><span class="badge ${bd}" style="font-size:.7rem;">${fmtDuration(dur)}</span></td>
    </tr>`;
  }).join('');
  vis('ee-evt-spin',false); vis('ee-evt-table',true); vis('ee-evt-empty',false);
}

/* ── Top Browser Sites ──────────────────────────────────────────── */
function renderTopSites(sites) {
  if (!sites.length) { spinSet('ee-sites','empty'); return; }
  document.getElementById('ee-sites-range').textContent = sites.length + ' sites';
  spinSet('ee-sites','wrap');
  // Fixed height on the relative wrapper, not the responsive canvas (see renderUsage).
  document.getElementById('ee-sites-wrap').style.height = Math.max(120, sites.length*22+40)+'px';
  mkChart('ee-sites-canvas',{
    type:'bar',
    data:{ labels:sites.map(s=>s.site),
           datasets:[{label:'Minutes', data:sites.map(s=>Math.round((s.total_s||0)/60)),
                      backgroundColor:sites.map((_,i)=>`hsl(${200+i*11},65%,55%)`),
                      borderRadius:3, borderSkipped:false}] },
    options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false},
                tooltip:{callbacks:{label:c=>' '+fmtDuration(sites[c.dataIndex].total_s)}} },
      scales:{ x:{grid:{color:gridColor},ticks:{color:textColor},title:{display:true,text:'minutes',color:textColor}},
               y:{grid:{color:'transparent'},ticks:{color:textColor,font:{size:11}}} } }
  });
}

/* ── Screenshots ───────────────────────────────────────────────────── */
async function loadScreenshots(dp) {
  const spin=document.getElementById('ee-ss-spin'), grid=document.getElementById('ee-ss-grid'), empty=document.getElementById('ee-ss-empty'), count=document.getElementById('ee-ss-count');
  spin.style.display=''; grid.style.display='none'; empty.style.display='none';
  const res=await fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}/screenshots?${dp}&limit=200`);
  const data=await res.json();
  spin.style.display='none';
  if (!data.ok||!data.screenshots.length){ empty.style.display=''; count.textContent=''; grid.innerHTML=''; return; }
  count.textContent=`${data.screenshots.length} screenshots`;
  grid.innerHTML = data.screenshots.map(s=>`
    <div class="ee-thumb" data-id="${s.id}" onclick="eeOpenShot(${s.id},'${s.time||s.captured_at||''}')"
         style="cursor:pointer;width:168px;flex-shrink:0;border-radius:8px;overflow:hidden;border:1px solid rgba(128,128,128,.2);background:#1a1a2e;">
      <div class="ee-thumb-inner" style="height:106px;display:flex;align-items:center;justify-content:center;">
        <span class="spinner-border spinner-border-sm text-secondary"></span>
      </div>
      <div class="px-2 pb-1 text-muted" style="font-size:.66rem;">${fmtTs(s.time||s.captured_at||'')}</div>
    </div>`).join('');
  grid.style.display='flex';
  for (const s of data.screenshots) {
    const el=grid.querySelector(`[data-id="${s.id}"] .ee-thumb-inner`);
    if (!el) continue;
    try {
      const r=await fetch(`/api/rmm/eagle-eyes/screenshot/${s.id}`);
      const d=await r.json();
      const b64=d.image_b64||d.screenshot?.data;
      const fmt=d.format||d.screenshot?.format||'jpeg';
      if (d.ok&&b64) el.innerHTML=`<img src="data:image/${fmt};base64,${b64}" style="max-width:100%;max-height:106px;object-fit:cover;">`;
      else el.innerHTML='<i class="bi bi-image text-muted fs-3"></i>';
    } catch(_){ el.innerHTML='<i class="bi bi-image text-muted fs-3"></i>'; }
  }
}

window.eeOpenShot = async function(id,ts) {
  const title=document.getElementById('ee-modal-title'), body=document.getElementById('ee-modal-body');
  title.textContent=fmtTs(ts); body.innerHTML='<span class="spinner-border"></span>';
  document.getElementById('ee-modal-download').href=`/api/rmm/eagle-eyes/screenshot/${id}/download`;
  new bootstrap.Modal(document.getElementById('ee-modal')).show();
  try {
    const res=await fetch(`/api/rmm/eagle-eyes/screenshot/${id}`);
    const d=await res.json();
    const b64=d.image_b64||d.screenshot?.data, fmt=d.format||d.screenshot?.format||'jpeg';
    body.innerHTML=d.ok&&b64?`<img src="data:image/${fmt};base64,${b64}" style="max-width:100%;border-radius:4px;">`:'<p class="text-muted">Image not available.</p>';
  } catch(_){ body.innerHTML='<p class="text-danger">Failed to load.</p>'; }
};

/* ── Set section into loading state ───────────────────────────────────── */
function setAllLoading() {
  ['ee-usage','ee-sites','ee-daily','ee-hourly'].forEach(p=>spinSet(p,'spin'));
  vis('ee-cat-spin',true); vis('ee-cat-wrap',false); vis('ee-cat-empty',false);
  vis('ee-evt-spin',true); vis('ee-evt-table',false); vis('ee-evt-empty',false);
}

/* ── Main ─────────────────────────────────────────────────────────────── */
window.eeLoad = async function() {
  const dp = dateParams();
  const wh = document.getElementById('ee-work-hours').checked;
  const whParam = wh ? '&work_hours=1' : '';
  setAllLoading();
  const [sumRes,dailyRes,hourlyRes,evtRes,sitesRes] = await Promise.all([
    fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}/app-summary?${dp}${whParam}`),
    fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}/daily?${dp}${whParam}`),
    fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}/hourly?${dp}${whParam}`),
    fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}/events?${dp}&limit=500`),
    fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}/top-sites?${dp}${whParam}`),
  ]);
  const [sumData,dailyData,hourlyData,evtData,sitesData] = await Promise.all(
    [sumRes.json(),dailyRes.json(),hourlyRes.json(),evtRes.json(),sitesRes.json()]
  );
  lastSummary = sumData.ok  ? sumData.summary  : [];
  lastEvents  = evtData.ok  ? evtData.events   : [];
  lastSites   = sitesData.ok? sitesData.sites   : [];
  const daily  = dailyData.ok  ? dailyData.daily  : [];
  const hourly = hourlyData.ok ? hourlyData.hourly : [];

  // Server already applied work_hours filter to lastSummary when wh=true
  // workHoursFilter still used for the events table display (limited to 500 events)
  const filteredEvt = workHoursFilter(lastEvents);

  renderStats(lastSummary, daily, hourly);
  renderProductivity(lastSummary);
  renderUsage(lastSummary);
  renderCategories(lastSummary);
  renderTopSites(lastSites);
  renderDaily(daily);
  renderHourly(hourly);
  renderEvents(filteredEvt);
  loadScreenshots(dp);

  const now = new Date();
  const t = now.toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit', hour12:true});
  const upd = document.getElementById('ee-last-updated');
  if (upd) upd.textContent = `Updated ${t}`;
  loadFocusSessions();
  loadGantt();
};

/* ── Right Now ─────────────────────────────────────────────────────── */
async function eeLoadCurrent() {
  try {
    const res  = await fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}/current`);
    const data = await res.json();
    const dot  = document.getElementById('ee-now-dot');
    const app  = document.getElementById('ee-now-app');
    const ttl  = document.getElementById('ee-now-title');
    const ib   = document.getElementById('ee-now-idle-badge');
    const idr  = document.getElementById('ee-now-idle-dur');
    const age  = document.getElementById('ee-now-age');
    if (!data.ok || !data.current) {
      dot.style.background = '#495057';
      app.textContent = '— offline —'; ttl.textContent = ''; ib.classList.add('d-none');
      return;
    }
    const c = data.current;
    // Keep agentTzOffsetH updated for gantt day calculation (server provides correct DST-aware value)
    if (typeof c.tz_offset_h === 'number') {
      agentTzOffsetH = c.tz_offset_h;
    }
    // Update the "Updated" label with browser local time
    const _updEl = document.getElementById('ee-last-updated');
    if (_updEl) _updEl.textContent = `Updated ${new Date().toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit', hour12:true})}`;
    // Dot = green if fresh (< 90s), yellow if stale-ish, grey if very old
    let _cTs = c.captured_at || '';
    const ageS = _cTs ? (Date.now() - new Date(_cTs).getTime()) / 1000 : 9999;
    dot.style.background = ageS < 90 ? '#51cf66' : ageS < 300 ? '#fcc419' : '#495057';
    app.textContent  = c.process_name || '—';
    ttl.textContent  = c.window_title || '';
    if (ageS <= 300 && c.is_idle) {
      // Only show idle badge if agent is actively reporting (not stale)
      ib.classList.remove('d-none');
      const idleSec = Math.max(0, Math.round(ageS));
      idr.textContent = idleSec > 0 ? fmtDuration(idleSec) + ' idle' : '';
    } else {
      ib.classList.add('d-none');
      idr.textContent = ageS > 300 ? `Last seen: ${fmtTs(c.captured_at)}` : '';
    }
    age.textContent = ageS <= 300 && c.captured_at ? fmtTs(c.captured_at) : '';
  } catch(_){}
}

/* ── Gantt Day Timeline ────────────────────────────────────────────── */
const GANTT_COLORS = ['#4dabf7','#69db7c','#f783ac','#ffa94d','#cc5de8','#20c997','#a9e34b','#ff8787','#74c0fc','#e599f7'];
const _gc = {};
function ganttColor(proc) {
  if (!_gc[proc]) _gc[proc] = GANTT_COLORS[Object.keys(_gc).length % GANTT_COLORS.length];
  return _gc[proc];
}
async function loadGantt() {
  const day = document.getElementById('ee-gantt-day').value;
  if (!day) return;
  document.getElementById('ee-gantt-spin').style.display = '';
  document.getElementById('ee-gantt-chart').style.display = 'none';
  document.getElementById('ee-gantt-empty').style.display = 'none';
  try {
    const res  = await fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}/gantt?day=${day}`);
    const data = await res.json();
    if (typeof data.tz_offset_h === 'number') agentTzOffsetH = data.tz_offset_h;  // keep day picker in sync
    document.getElementById('ee-gantt-spin').style.display = 'none';
    if (!data.ok || !data.events.length) { document.getElementById('ee-gantt-empty').style.display=''; return; }
    renderGantt(data.events, day);
  } catch(_){ document.getElementById('ee-gantt-spin').style.display='none'; document.getElementById('ee-gantt-empty').style.display=''; }
}
function renderGantt(events, day) {
  const el = document.getElementById('ee-gantt-chart');
  el.style.display = '';
  // Build hour buckets 0-23
  const hourBuckets = Array.from({length:24}, (_,h) => ({hour:h, segs:[]}));
  let dayStart = null, dayEnd = null;
  for (const ev of events) {
    if (!ev.captured_at) continue;
    let _evTs = ev.captured_at;
    if (!/Z$|[+-]\d\d/.test(_evTs)) _evTs += 'Z';
    const tsUtc = new Date(_evTs);
    if (isNaN(tsUtc.getTime())) continue;
    // Use agent's local hour - extracted from the ISO offset so gantt shows agent's work day
    const _offM = _evTs.match(/([+-])(\d{2}):(\d{2})$/);
    const _offMs = _offM ? ((_offM[1]==='+' ? 1 : -1) * (parseInt(_offM[2])*60 + parseInt(_offM[3]))) * 60000 : agentTzOffsetH * 3600000;
    const tsLocal = new Date(tsUtc.getTime() + _offMs);
    const h   = tsLocal.getUTCHours();
    const dur = ev.duration_s || 0;
    hourBuckets[h].segs.push({proc: ev.process_name||'unknown', dur, ts: tsUtc});
    if (!dayStart || tsUtc < dayStart) dayStart = tsUtc;
    if (!dayEnd || tsUtc > dayEnd) dayEnd = tsUtc;
  }
  const activeHours = hourBuckets.filter(b=>b.segs.length);
  if (!activeHours.length) { el.style.display='none'; document.getElementById('ee-gantt-empty').style.display=''; return; }
  const minH = activeHours[0].hour, maxH = activeHours[activeHours.length-1].hour + 1;
  let html = `<div style="overflow-x:auto;"><div style="min-width:600px;">`;
  // Hour labels + bars
  for (let h = minH; h <= maxH; h++) {
    const bucket = hourBuckets[h] || {segs:[]};
    const totalH = bucket.segs.reduce((a,s)=>a+s.dur, 0) || 3600;
    const label  = fmtHour(h);
    html += `<div class="d-flex align-items-center mb-1" style="gap:6px;">`;
    html += `<span style="width:46px;font-size:.68rem;color:#868e96;flex-shrink:0;text-align:right;">${label}</span>`;
    html += `<div class="flex-grow-1 d-flex" style="height:20px;border-radius:4px;overflow:hidden;background:#1a1a2e;">`;
    for (const seg of bucket.segs) {
      const pct = Math.max(1, Math.round(seg.dur / totalH * 100));
      const col = ganttColor(seg.proc);
      html += `<div title="${seg.proc} · ${fmtDuration(seg.dur)}" style="width:${pct}%;background:${col};height:100%;"></div>`;
    }
    html += `</div></div>`;
  }
  // Legend
  const seen = {};
  for (const b of hourBuckets) for (const s of b.segs) if (!seen[s.proc]) seen[s.proc] = true;
  html += `<div class="d-flex flex-wrap gap-2 mt-2">`;
  for (const proc of Object.keys(seen)) {
    html += `<span style="font-size:.68rem;color:${ganttColor(proc)}"><i class="bi bi-square-fill me-1"></i>${proc}</span>`;
  }
  html += `</div></div></div>`;
  el.innerHTML = html;
}
function ganttPrevDay() {
  const inp = document.getElementById('ee-gantt-day');
  const d = new Date(inp.value + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() - 1);
  inp.value = d.toISOString().slice(0, 10);
  loadGantt();
}
function ganttNextDay() {
  const inp = document.getElementById('ee-gantt-day');
  const d = new Date(inp.value + 'T00:00:00Z');
  d.setUTCDate(d.getUTCDate() + 1);
  inp.value = d.toISOString().slice(0, 10);
  loadGantt();
}

/* ── Focus Sessions ────────────────────────────────────────────────── */
async function loadFocusSessions() {
  const dp = dateParams();
  document.getElementById('ee-focus-spin').style.display = '';
  document.getElementById('ee-focus-table').style.display = 'none';
  document.getElementById('ee-focus-empty').style.display = 'none';
  try {
    const res  = await fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}/focus-sessions?${dp}`);
    const data = await res.json();
    document.getElementById('ee-focus-spin').style.display = 'none';
    if (!data.ok || !data.sessions.length) { document.getElementById('ee-focus-empty').style.display=''; return; }
    document.getElementById('ee-focus-count').textContent = data.sessions.length + ' sessions';
    document.getElementById('ee-focus-body').innerHTML = data.sessions.map(s => {
      const dur = s.duration_s;
      const lvl = dur >= 7200 ? ['bg-success','Deep'] : dur >= 3600 ? ['bg-info text-dark','Flow'] : ['bg-secondary','Focus'];
      return `<tr>
        <td style="max-width:130px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"><i class="bi bi-window me-1 text-muted"></i>${s.process_name||'—'}</td>
        <td class="text-muted" style="font-size:.75rem;">${fmtTs(s.started_at)}</td>
        <td class="text-end fw-bold">${fmtDuration(dur)}</td>
        <td class="text-center"><span class="badge ${lvl[0]}" style="font-size:.68rem;">${lvl[1]}</span></td>
      </tr>`;
    }).join('');
    document.getElementById('ee-focus-table').style.display = '';
  } catch(_){ document.getElementById('ee-focus-spin').style.display='none'; document.getElementById('ee-focus-empty').style.display=''; }
}

/* ── App Classifications ───────────────────────────────────────────── */
const PROD_BADGE = {
  productive:   '<span class="badge bg-success" style="font-size:.72rem;">✅ Productive</span>',
  unproductive: '<span class="badge bg-danger"  style="font-size:.72rem;">❌ Unproductive</span>',
  neutral:      '<span class="badge bg-secondary" style="font-size:.72rem;">⚪ Neutral</span>',
};

let _clsData = [];           // all loaded classifications (global app + this agent's sites)
let _clsEditId = null;       // id of app rule being edited (null = adding new)
let _clsSiteEditId = null;   // id of site rule being edited

function clsTab(tab) {
  const isApp = tab === 'app';
  document.getElementById('cls-panel-app').style.display  = isApp ? '' : 'none';
  document.getElementById('cls-panel-site').style.display = isApp ? 'none' : '';
  document.getElementById('cls-tab-app').classList.toggle('active', isApp);
  document.getElementById('cls-tab-site').classList.toggle('active', !isApp);
  if (!isApp) clsLoadTopSites();
}

async function loadClassifications() {
  const res  = await fetch(`/api/rmm/eagle-eyes/app-classifications?agent_id=${encodeURIComponent(agentId)}`);
  const data = await res.json();
  if (!data.ok) return;
  _clsData = data.classifications;
  _clsRender();
  // Also refresh suggestions in case a new app rule was added
  loadFleetSuggestions();
}

function _clsRender() {
  const search  = (document.getElementById('cls-search')?.value || '').toLowerCase();
  const appRows = _clsData.filter(c => !c.window_title_pattern);
  const siteRows = _clsData.filter(c => c.window_title_pattern && (!c.agent_id || c.agent_id === agentId));

  // Update app count badge
  const countEl = document.getElementById('cls-app-count');
  if (countEl) countEl.textContent = appRows.length || '';

  // App rules table
  const filtered = search
    ? appRows.filter(c => (c.process_pattern||'').includes(search) || (c.label||'').toLowerCase().includes(search))
    : appRows;
  document.getElementById('cls-tbody').innerHTML = filtered.map(c =>
    `<tr>
      <td><code>${escHtml(c.process_pattern||'—')}</code></td>
      <td>${escHtml(c.label||'—')}</td>
      <td>${PROD_BADGE[c.productivity]||escHtml(c.productivity)}</td>
      <td class="text-end">
        <button class="btn btn-xs btn-outline-secondary py-0 px-1 me-1" style="font-size:.7rem;" onclick="clsEdit(${c.id})"><i class="bi bi-pencil"></i></button>
        <button class="btn btn-xs btn-outline-danger py-0 px-1" style="font-size:.7rem;" onclick="clsDelete(${c.id})"><i class="bi bi-trash"></i></button>
      </td>
    </tr>`
  ).join('') || '<tr><td colspan="4" class="text-muted text-center small py-3">No app rules yet — add one above or click a fleet suggestion</td></tr>';

  // Site rules table (this agent's rules only)
  const agentSiteRows = siteRows.filter(c => c.agent_id === agentId);
  const globalSiteRows = siteRows.filter(c => !c.agent_id);
  const allSiteRows = [...agentSiteRows, ...globalSiteRows];
  document.getElementById('cls-site-tbody').innerHTML = allSiteRows.map(c => {
    const isGlobal = !c.agent_id;
    return `<tr>
      <td><code>${escHtml(c.window_title_pattern)}</code>${isGlobal ? ' <span class="badge bg-light text-secondary border" style="font-size:.65rem;">global</span>' : ''}</td>
      <td>${escHtml(c.label||'—')}</td>
      <td>${PROD_BADGE[c.productivity]||escHtml(c.productivity)}</td>
      <td class="text-end">
        ${!isGlobal ? `<button class="btn btn-xs btn-outline-secondary py-0 px-1 me-1" style="font-size:.7rem;" onclick="clsSiteEdit(${c.id})"><i class="bi bi-pencil"></i></button>` : ''}
        <button class="btn btn-xs btn-outline-danger py-0 px-1" style="font-size:.7rem;" onclick="clsDelete(${c.id})"><i class="bi bi-trash"></i></button>
      </td>
    </tr>`;
  }).join('') || '<tr><td colspan="4" class="text-muted text-center small py-3">No site rules for this asset yet — add one above or click a top-site chip</td></tr>';
}

function clsFilterTable() {
  _clsRender();
}

/* ── App rule edit / cancel ─────────────────────────────────── */
function clsEdit(id) {
  const c = _clsData.find(x => x.id === id);
  if (!c) return;
  _clsEditId = id;
  document.getElementById('cls-pattern').value = c.process_pattern || '';
  document.getElementById('cls-label').value   = c.label || '';
  document.getElementById('cls-prod').value    = c.productivity || 'neutral';
  document.getElementById('cls-form-title').textContent = 'Edit App Rule';
  document.getElementById('cls-edit-badge').style.display = '';
  document.getElementById('cls-cancel-btn').style.display = '';
  document.getElementById('cls-pattern').focus();
}
function clsCancelEdit() {
  _clsEditId = null;
  document.getElementById('cls-pattern').value = '';
  document.getElementById('cls-label').value   = '';
  document.getElementById('cls-prod').value    = 'neutral';
  document.getElementById('cls-form-title').textContent = 'Add App Rule';
  document.getElementById('cls-edit-badge').style.display = 'none';
  document.getElementById('cls-cancel-btn').style.display = 'none';
}

/* ── Site rule edit / cancel ────────────────────────────────── */
function clsSiteEdit(id) {
  const c = _clsData.find(x => x.id === id);
  if (!c) return;
  _clsSiteEditId = id;
  document.getElementById('cls-site-pat').value   = c.window_title_pattern || '';
  document.getElementById('cls-site-label').value = c.label || '';
  document.getElementById('cls-site-prod').value  = c.productivity || 'unproductive';
  document.getElementById('cls-site-form-title').textContent = 'Edit Site Rule';
  document.getElementById('cls-site-edit-badge').style.display = '';
  document.getElementById('cls-site-cancel-btn').style.display = '';
  document.getElementById('cls-site-pat').focus();
}
function clsSiteCancelEdit() {
  _clsSiteEditId = null;
  document.getElementById('cls-site-pat').value   = '';
  document.getElementById('cls-site-label').value = '';
  document.getElementById('cls-site-prod').value  = 'unproductive';
  document.getElementById('cls-site-form-title').textContent = 'Add Site Rule';
  document.getElementById('cls-site-edit-badge').style.display = 'none';
  document.getElementById('cls-site-cancel-btn').style.display = 'none';
}

/* ── Save handlers ──────────────────────────────────────────── */
async function clsSave() {
  const pattern = document.getElementById('cls-pattern').value.trim().toLowerCase();
  const label   = document.getElementById('cls-label').value.trim();
  const prod    = document.getElementById('cls-prod').value;
  if (!pattern) return;
  // If editing, delete old entry first (process_pattern may have changed)
  if (_clsEditId !== null) {
    await fetch(`/api/rmm/eagle-eyes/app-classifications/${_clsEditId}`, {method:'DELETE'});
  }
  await fetch('/api/rmm/eagle-eyes/app-classifications', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({process_pattern: pattern, label, productivity: prod}),
  });
  clsCancelEdit();
  loadClassifications();
}

async function clsSiteSave() {
  const sitePat = document.getElementById('cls-site-pat').value.trim().toLowerCase();
  const label   = document.getElementById('cls-site-label').value.trim();
  const prod    = document.getElementById('cls-site-prod').value;
  if (!sitePat) return;
  // Per-agent site rule — always pass agent_id
  await fetch('/api/rmm/eagle-eyes/app-classifications', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({window_title_pattern: sitePat, label: label || sitePat, productivity: prod, agent_id: agentId}),
  });
  clsSiteCancelEdit();
  loadClassifications();
}

/* ── Fleet suggestions panel ────────────────────────────────── */
async function loadFleetSuggestions() {
  const listEl    = document.getElementById('cls-suggestions-list');
  const loadingEl = document.getElementById('cls-suggestions-loading');
  if (!listEl) return;
  try {
    const res  = await fetch('/api/rmm/eagle-eyes/fleet-app-suggestions');
    const data = await res.json();
    if (loadingEl) loadingEl.style.display = 'none';
    if (!data.ok || !data.suggestions.length) {
      listEl.innerHTML = '<span class="text-muted small">All top apps are classified!</span>';
      return;
    }
    listEl.innerHTML = data.suggestions.map(s => {
      const agents = s.agent_count === 1 ? '1 agent' : `${s.agent_count} agents`;
      const mins   = Math.round(s.total_s / 60);
      const name   = escHtml(s.process_name);
      return `<button class="btn btn-xs btn-outline-secondary py-0 px-2" style="font-size:.73rem;"
        onclick="clsApplySuggestion('${s.process_name.replace(/'/g,"\\'")}')">
        ${name} <span class="text-muted">${agents}${mins ? ` · ${mins}m` : ''}</span>
      </button>`;
    }).join('');
  } catch(_) {
    if (loadingEl) loadingEl.style.display = 'none';
  }
}

function clsApplySuggestion(name) {
  document.getElementById('cls-pattern').value = name;
  // Auto-fill label as title-cased name
  document.getElementById('cls-label').value   = name.charAt(0).toUpperCase() + name.slice(1);
  document.getElementById('cls-prod').value    = 'neutral';
  document.getElementById('cls-pattern').focus();
}

/* ── Top sites chips (per-asset) ────────────────────────────── */
async function clsLoadTopSites() {
  try {
    const [sitesRes, clsRes] = await Promise.all([
      fetch(`/api/rmm/eagle-eyes/${encodeURIComponent(agentId)}/top-sites?days=30`),
      fetch(`/api/rmm/eagle-eyes/app-classifications?agent_id=${encodeURIComponent(agentId)}`),
    ]);
    const [sitesData, clsData] = await Promise.all([sitesRes.json(), clsRes.json()]);
    if (!sitesData.ok || !sitesData.sites.length) return;
    const classified = new Set(
      (clsData.classifications||[])
        .filter(c => c.window_title_pattern)
        .map(c => c.window_title_pattern.toLowerCase())
    );
    const chips = sitesData.sites.map(s => {
      const key      = s.site.toLowerCase();
      const done     = classified.has(key);
      const safeSite = s.site.replace(/'/g, "\\'");
      const mins     = Math.round(s.total_s / 60);
      return `<button class="btn btn-xs ${done?'btn-outline-success':'btn-outline-secondary'} py-0 px-2" style="font-size:.75rem;"
        onclick="clsSiteChip('${safeSite}')" ${done?'disabled title="Already classified"':''}>
        ${done?'✓ ':''}${escHtml(s.site)} <span class="text-muted">${mins}m</span></button>`;
    }).join('');
    document.getElementById('cls-top-sites-chips').innerHTML = chips;
    document.getElementById('cls-top-sites-wrap').style.display = '';
  } catch(_) {}
}

function clsSiteChip(site) {
  document.getElementById('cls-site-pat').value   = site.toLowerCase();
  document.getElementById('cls-site-label').value = site;
  document.getElementById('cls-site-prod').value  = 'unproductive';
  document.getElementById('cls-site-pat').focus();
}

async function clsDelete(id) {
  if (!confirm('Delete this rule?')) return;
  await fetch(`/api/rmm/eagle-eyes/app-classifications/${id}`, {method:'DELETE'});
  loadClassifications();
}

/* ── Escape helper (if not already defined) ─────────────────── */
function escHtml(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* ── Alert Rules ───────────────────────────────────────────────────── */
function alrTypeChange() {
  const t = document.getElementById('alr-type').value;
  document.getElementById('alr-thresh-col').style.display = (t === 'app_used') ? 'none' : '';
  document.getElementById('alr-proc-col').style.display   = (t === 'app_used') ? '' : 'none';
}
async function loadAlerts() {
  const res  = await fetch('/api/rmm/eagle-eyes/alerts');
  const data = await res.json();
  if (!data.ok) return;
  document.getElementById('alr-tbody').innerHTML = data.rules.map(r =>
    `<tr><td>${r.alert_type}</td><td>${r.threshold??'—'}</td><td>${r.process_pattern??'—'}</td>
     <td>${r.email_notify?'Yes':'No'}</td><td class="text-muted small">${r.last_fired_at??'Never'}</td>
     <td><button class="btn btn-xs btn-outline-danger py-0 px-1" style="font-size:.7rem;" onclick="alrDelete(${r.id})">✕</button></td></tr>`
  ).join('') || '<tr><td colspan="6" class="text-muted text-center">No rules</td></tr>';
  const log = data.log || [];
  document.getElementById('alr-log').innerHTML = log.length
    ? log.map(l=>`<div>${fmtTs(l.fired_at)} — ${l.alert_type}: ${l.message}</div>`).join('')
    : 'No alerts fired yet.';
}
async function alrSave() {
  const t = document.getElementById('alr-type').value;
  await fetch('/api/rmm/eagle-eyes/alerts', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
    alert_type: t,
    threshold:  t!=='app_used' ? parseInt(document.getElementById('alr-thresh').value)||null : null,
    process_pattern: t==='app_used' ? document.getElementById('alr-proc').value.trim().toLowerCase() : null,
    email_notify: document.getElementById('alr-email').checked,
    agent_id: agentId,
  })});
  loadAlerts();
}
async function alrDelete(id) {
  await fetch(`/api/rmm/eagle-eyes/alerts/${id}`, {method:'DELETE'});
  loadAlerts();
}

/* ── Scheduled Reports ─────────────────────────────────────────────── */
function schFreqChange() {
  const freq = document.getElementById('sch-freq').value;
  document.getElementById('sch-dow-row').style.display = freq==='weekly' ? '' : 'none';
}
async function loadSchedules() {
  const res  = await fetch('/api/rmm/eagle-eyes/report-schedules');
  const data = await res.json();
  if (!data.ok) return;
  const DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  document.getElementById('sch-list').innerHTML = data.schedules.length
    ? data.schedules.map(s => `<div class="d-flex justify-content-between align-items-center py-1 border-bottom">
        <span>${s.frequency==='weekly'?DAYS[s.day_of_week]+' ':''}${s.send_time} → ${s.email_to||'—'}</span>
        <button class="btn btn-xs btn-outline-danger py-0 px-1" style="font-size:.7rem;" onclick="schDelete(${s.id})">✕</button>
      </div>`).join('')
    : '<p class="text-muted small">No schedules.</p>';
}
async function schSave() {
  const email = document.getElementById('sch-email').value.trim();
  if (!email) return;
  await fetch('/api/rmm/eagle-eyes/report-schedules', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({
    agent_id: agentId,
    frequency: document.getElementById('sch-freq').value,
    day_of_week: parseInt(document.getElementById('sch-dow').value),
    send_time: document.getElementById('sch-time').value,
    email_to: email,
  })});
  document.getElementById('sch-email').value='';
  loadSchedules();
}
async function schDelete(id) {
  await fetch(`/api/rmm/eagle-eyes/report-schedules?id=${id}`, {method:'DELETE'});
  loadSchedules();
}

document.addEventListener('DOMContentLoaded', () => {
  // Set gantt to today in browser local time
  document.getElementById('ee-gantt-day').value = new Date().toLocaleDateString('en-CA');

  eeLoad();
  eeLoadCurrent();
  setInterval(eeLoadCurrent, 30000);  // live poll every 30s

  // Lazy-load modals on first open
  document.getElementById('ee-classify-modal').addEventListener('show.bs.modal', () => {
    loadClassifications();
    loadFleetSuggestions();
  }, {once:false});
  document.getElementById('ee-alerts-modal').addEventListener('show.bs.modal', () => loadAlerts(), {once:false});
  document.getElementById('ee-schedule-modal').addEventListener('show.bs.modal', () => loadSchedules(), {once:false});
});
