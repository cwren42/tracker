/* workflows.html extracted JS */
/* Note: {{token}} patterns are workflow placeholder strings, not Jinja */

'use strict';
// ══════════════════════════════════════════════════════════
//  WORKFLOW CANVAS  —  mouse-event drag, no HTML5 drag API
// ══════════════════════════════════════════════════════════
let nodes=[], edges=[], selectedNode=null, currentWfId=null, nodeIdSeq=1, edgeIdSeq=1;

const canvasWrap = document.getElementById('wf-canvas-wrap');
const canvas     = document.getElementById('wf-canvas');
const svgEl      = document.getElementById('wf-svg');
const nodesDiv   = document.getElementById('wf-nodes');
const dropHint   = document.getElementById('wf-drop-hint');
const ghost      = document.getElementById('wf-drag-ghost');

// ── PALETTE DRAG (mousedown → ghost → mouseup on canvas) ──────────────
let paletteDrag = null;

document.querySelectorAll('.palette-node').forEach(pn => {
  pn.addEventListener('mousedown', e => {
    if (e.button !== 0) return;
    e.preventDefault();
    paletteDrag = { type: pn.dataset.type, action: pn.dataset.action || '' };
    ghost.innerHTML = (pn.querySelector('.pn-icon')?.outerHTML || '') + pn.textContent.trim();
    ghost.style.display = 'flex';
    ghost.style.left = (e.clientX + 14) + 'px';
    ghost.style.top  = (e.clientY - 16) + 'px';
    pn.classList.add('dragging');
    pn._draggingRef = pn;
  });
});

document.addEventListener('mousemove', e => {
  if (paletteDrag) {
    ghost.style.left = (e.clientX + 14) + 'px';
    ghost.style.top  = (e.clientY - 16) + 'px';
    const cr = canvas.getBoundingClientRect();
    const over = e.clientX > cr.left && e.clientX < cr.right && e.clientY > cr.top && e.clientY < cr.bottom;
    canvasWrap.classList.toggle('drag-over', over);
  }
  if (edgeDragActive && edgeDragLine) {
    const cr = canvas.getBoundingClientRect();
    const from = portPos(edgeDragFrom.node, edgeDragFrom.port);
    edgeDragLine.setAttribute('d', bezier(from.x, from.y, e.clientX - cr.left, e.clientY - cr.top));
  }
  if (nodeMoveNd) {
    const cr=canvas.getBoundingClientRect();
    nodeMoveNd.x = Math.max(0, e.clientX - cr.left - nodeMoveOx);
    nodeMoveNd.y = Math.max(0, e.clientY - cr.top  - nodeMoveOy);
    nodeMoveEl.style.left = Math.round(nodeMoveNd.x) + 'px';
    nodeMoveEl.style.top  = Math.round(nodeMoveNd.y) + 'px';
    refreshEdges();
  }
});

document.addEventListener('mouseup', e => {
  // palette drop
  if (paletteDrag) {
    ghost.style.display = 'none';
    canvasWrap.classList.remove('drag-over');
    document.querySelectorAll('.palette-node.dragging').forEach(p => p.classList.remove('dragging'));
    const cr = canvas.getBoundingClientRect();
    if (e.clientX > cr.left && e.clientX < cr.right && e.clientY > cr.top && e.clientY < cr.bottom) {
      addNode(paletteDrag.type, paletteDrag.action, Math.max(10, e.clientX - cr.left - 80), Math.max(10, e.clientY - cr.top - 30));
    }
    paletteDrag = null;
  }
  // edge drop
  if (edgeDragActive) {
    edgeDragActive = false;
    if (edgeDragLine) { try{svgEl.removeChild(edgeDragLine);}catch(e){} edgeDragLine = null; }
    const cr  = canvas.getBoundingClientRect();
    const tgt = findInputPort(e.clientX - cr.left, e.clientY - cr.top);
    if (tgt && tgt !== edgeDragFrom.node && !edges.find(ed => ed.fromNode===edgeDragFrom.node && ed.fromPort===edgeDragFrom.port && ed.toNode===tgt)) {
      edges.push({ id:'e'+(edgeIdSeq++), fromNode:edgeDragFrom.node, fromPort:edgeDragFrom.port, toNode:tgt, label:edgeDragFrom.port==='out'?'':edgeDragFrom.port });
      refreshEdges();
    }
    edgeDragFrom = null;
  }
  // node move
  if (nodeMoveNd) { nodeMoveEl.style.zIndex=''; nodeMoveEl=null; nodeMoveNd=null; }
});

// ── NODE CREATION ──────────────────────────────────────────────────────
const ICONS={
  trigger:'lightning-fill',
  create_ticket:'ticket-fill', update_ticket:'pencil-fill', close_ticket:'check-circle-fill', assign_ticket:'person-check-fill',
  send_notification:'bell-fill', send_email:'envelope-fill', send_teams:'microsoft-teams', send_slack:'slack',
  create_user:'person-plus-fill', disable_ad_user:'person-slash-fill', enable_ad_user:'person-check-fill',
  reset_password:'key-fill', unlock_account:'unlock-fill',
  add_to_group:'people-fill', remove_from_group:'person-dash-fill', azure_sync:'cloud-arrow-up-fill',
  deploy_software:'box-arrow-in-down-right', uninstall_software:'box-arrow-up-right',
  run_script:'terminal-fill', deploy_patch:'shield-check',
  reboot_device:'arrow-clockwise', shutdown_device:'power', lock_device:'lock-fill', apply_gpo:'shield-shaded',
  http_request:'send-fill', webhook:'globe2', ai_suggest:'stars', wait:'clock-fill', condition:'signpost-split-fill'
};
const ALABELS={
  create_ticket:'Create Ticket', update_ticket:'Update Ticket', close_ticket:'Close Ticket', assign_ticket:'Assign Ticket',
  send_notification:'In-App Alert', send_email:'Send Email', send_teams:'Teams Message', send_slack:'Slack Message',
  create_user:'Create User', disable_ad_user:'Disable User', enable_ad_user:'Enable User',
  reset_password:'Reset Password', unlock_account:'Unlock Account',
  add_to_group:'Add to AD Group', remove_from_group:'Remove from Group', azure_sync:'Azure AD Sync',
  deploy_software:'Deploy Software', uninstall_software:'Uninstall Software',
  run_script:'Run Script', deploy_patch:'Deploy Patch',
  reboot_device:'Reboot Device', shutdown_device:'Shutdown Device', lock_device:'Lock/BitLocker', apply_gpo:'Apply Group Policy',
  http_request:'HTTP Request', webhook:'Webhook', ai_suggest:'AI Suggest', wait:'Wait'
};

function addNode(type, action, x, y, cfg, id, label) {
  cfg = cfg || {};
  const nid = id || ('n'+(nodeIdSeq++));
  const nd  = {id:nid,type,action:action||'',x,y,config:cfg,label:label||(type==='trigger'?'Trigger':type==='condition'?'Condition':ALABELS[action]||action||type)};
  nodes.push(nd); renderNode(nd); updateDropHint(); return nd;
}

function renderNode(nd) {
  const el = document.createElement('div');
  el.className = 'wf-node node-'+nd.type;
  el.id = 'node-'+nd.id;
  el.style.left = Math.round(nd.x)+'px';
  el.style.top  = Math.round(nd.y)+'px';
  const icon = nd.type==='condition' ? ICONS.condition : (ICONS[nd.action]||'gear-fill');
  el.innerHTML = `<button class="node-del" onclick="deleteNode('${nd.id}')"><i class="bi bi-x-lg"></i></button>
    <div class="node-header"><i class="bi bi-${icon}"></i> ${esc(nd.label)}</div>
    <div class="node-body" id="node-body-${nd.id}"></div>`;
  if (nd.type!=='trigger') {
    const pin=document.createElement('div'); pin.className='port port-in'; pin.style.top='28px'; pin.title='Input'; el.appendChild(pin);
  }
  if (nd.type==='condition') {
    el.appendChild(mkPort(nd.id,'true','#5fcf8b',18));
    el.appendChild(mkPort(nd.id,'false','#f87171',42));
  } else {
    el.appendChild(mkPort(nd.id,'out','#6aacf8',28));
  }
  makeNodeDraggable(el, nd);
  el.addEventListener('mousedown', e => { if(!e.target.classList.contains('port')&&!e.target.closest('button')) selectNode(nd); });
  nodesDiv.appendChild(el);
  refreshEdges(); updateNodeBody(nd);
}

function mkPort(nid, pname, color, top) {
  const p=document.createElement('div');
  p.className='port port-out'; p.style.top=top+'px'; p.style.borderColor=color;
  p.dataset.node=nid; p.dataset.port=pname;
  p.title=pname==='true'?'True ✓':pname==='false'?'False ✗':'Output';
  p.addEventListener('mousedown', startEdgeDrag); return p;
}

function updateNodeBody(nd) {
  const b=document.getElementById('node-body-'+nd.id); if(!b) return;
  const c=nd.config||{};
  let t='';
  if(nd.type==='trigger')                  t=c.trigger_type||document.getElementById('wf-trigger-type').value;
  else if(nd.action==='create_ticket')     t=c.title||'<em style="color:var(--text-secondary)">No title</em>';
  else if(nd.action==='update_ticket')     t=c.status||'<em style="color:var(--text-secondary)">No status</em>';
  else if(nd.action==='close_ticket')      t=c.resolution||'Auto-close';
  else if(nd.action==='assign_ticket')     t=c.assigned_to||'<em style="color:var(--text-secondary)">No assignee</em>';
  else if(nd.action==='send_email')        t=c.to||'<em style="color:var(--text-secondary)">No recipient</em>';
  else if(nd.action==='send_notification') t=c.message||'<em style="color:var(--text-secondary)">No message</em>';
  else if(nd.action==='send_teams')        t=c.title||'<em style="color:var(--text-secondary)">No title</em>';
  else if(nd.action==='send_slack')        t=c.channel||'<em style="color:var(--text-secondary)">No channel</em>';
  else if(nd.action==='create_user')       t=c.username||'<em style="color:var(--text-secondary)">No username</em>';
  else if(nd.action==='disable_ad_user')   t=c.username||'{{username}}';
  else if(nd.action==='enable_ad_user')    t=c.username||'{{username}}';
  else if(nd.action==='reset_password')    t=c.username||'{{username}}';
  else if(nd.action==='unlock_account')    t=c.username||'{{username}}';
  else if(nd.action==='add_to_group')      t=(c.username||'user')+' → '+(c.group_name||'group');
  else if(nd.action==='remove_from_group') t=(c.username||'user')+' ← '+(c.group_name||'group');
  else if(nd.action==='azure_sync')        t='Delta sync';
  else if(nd.action==='deploy_software')   t=c.package||'<em style="color:var(--text-secondary)">No package</em>';
  else if(nd.action==='uninstall_software')t=c.package||'<em style="color:var(--text-secondary)">No package</em>';
  else if(nd.action==='run_script')        t=(c.language||'ps1')+': '+(c.script||'').substring(0,28)||'<em style="color:var(--text-secondary)">No script</em>';
  else if(nd.action==='deploy_patch')      t=c.cve_id||'<em style="color:var(--text-secondary)">No CVE</em>';
  else if(nd.action==='reboot_device')     t=c.delay_seconds?'Delay: '+c.delay_seconds+'s':'Immediate';
  else if(nd.action==='shutdown_device')   t='Force shutdown';
  else if(nd.action==='lock_device')       t=c.mode||'lock';
  else if(nd.action==='apply_gpo')         t=c.force?'gpupdate /force':'gpupdate';
  else if(nd.action==='webhook')           t=(c.url||'').substring(0,32)||'<em style="color:var(--text-secondary)">No URL</em>';
  else if(nd.action==='http_request')      t=(c.method||'GET')+' '+(c.url||'').substring(0,24)||'<em style="color:var(--text-secondary)">No URL</em>';
  else if(nd.action==='wait')              t=(c.seconds||'?')+'s';
  else if(nd.type==='condition')           t=(c.field||'?')+' '+(c.operator||'==')+' '+(c.value||'?');
  else if(nd.action==='ai_suggest')        t='AI ticket suggestion';
  b.innerHTML=t;
}

// ── NODE DRAG (move on canvas) ─────────────────────────────────────────
let nodeMoveEl=null, nodeMoveNd=null, nodeMoveOx=0, nodeMoveOy=0;

function makeNodeDraggable(el, nd) {
  el.addEventListener('mousedown', e => {
    if(e.button!==0||e.target.classList.contains('port')||e.target.closest('button')) return;
    e.preventDefault();
    nodeMoveEl=el; nodeMoveNd=nd;
    const cr=canvas.getBoundingClientRect();
    nodeMoveOx=e.clientX-cr.left-nd.x; nodeMoveOy=e.clientY-cr.top-nd.y;
    el.style.zIndex=100;
  });
}

// ── EDGE DRAWING ───────────────────────────────────────────────────────
let edgeDragActive=false, edgeDragFrom=null, edgeDragLine=null;

function startEdgeDrag(e) {
  e.preventDefault(); e.stopPropagation();
  edgeDragActive=true;
  edgeDragFrom={node:e.target.dataset.node, port:e.target.dataset.port};
  edgeDragLine=document.createElementNS('http://www.w3.org/2000/svg','path');
  edgeDragLine.setAttribute('stroke', cssVar('--button-primary')); edgeDragLine.setAttribute('stroke-width','2');
  edgeDragLine.setAttribute('fill','none'); edgeDragLine.setAttribute('stroke-dasharray','6 3');
  svgEl.appendChild(edgeDragLine);
}

function cssVar(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim()||'#888';}

function findInputPort(x, y) {
  for(const nd of nodes) {
    if(nd.type==='trigger') continue;
    const el=document.getElementById('node-'+nd.id); if(!el) continue;
    const pin=el.querySelector('.port-in'); if(!pin) continue;
    const r=pin.getBoundingClientRect(), cr=canvas.getBoundingClientRect();
    const px=r.left-cr.left+r.width/2, py=r.top-cr.top+r.height/2;
    if(Math.abs(x-px)<16&&Math.abs(y-py)<16) return nd.id;
  }
  return null;
}

function portPos(nid, port) {
  const el=document.getElementById('node-'+nid); if(!el) return{x:0,y:0};
  const nd=nodes.find(n=>n.id===nid);
  const cr=canvas.getBoundingClientRect();
  const p=el.querySelector('[data-port="'+port+'"]')||el.querySelector(port==='in'?'.port-in':'.port-out');
  if(!p&&nd) return port==='in'?{x:nd.x,y:nd.y+28}:{x:nd.x+160,y:nd.y+28};
  if(!p) return{x:0,y:0};
  const r=p.getBoundingClientRect();
  return{x:r.left-cr.left+r.width/2, y:r.top-cr.top+r.height/2};
}

function bezier(x1,y1,x2,y2){const cx=(x1+x2)/2;return `M${x1},${y1} C${cx},${y1} ${cx},${y2} ${x2},${y2}`;}

function eCol(port){return port==='true'?'#5fcf8b':port==='false'?'#f87171':cssVar('--button-primary');}

function refreshEdges() {
  const neutralCol=cssVar('--button-primary');
  svgEl.innerHTML=`<defs>
    <marker id="arr"       markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="${neutralCol}"/></marker>
    <marker id="arr-true"  markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#5fcf8b"/></marker>
    <marker id="arr-false" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#f87171"/></marker>
  </defs>`;
  const btnLayer=document.getElementById('wf-edge-btns');
  if(btnLayer) btnLayer.innerHTML='';
  edges.forEach(ed=>{
    const from=portPos(ed.fromNode,ed.fromPort), to=portPos(ed.toNode,'in'), col=eCol(ed.fromPort);
    const mid=[(from.x+to.x)/2,(from.y+to.y)/2];
    const mId=ed.fromPort==='true'?'arr-true':ed.fromPort==='false'?'arr-false':'arr';
    const g=document.createElementNS('http://www.w3.org/2000/svg','g'); g.classList.add('conn-group');
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d',bezier(from.x,from.y,to.x,to.y)); path.setAttribute('stroke',col);
    path.setAttribute('stroke-width','2'); path.setAttribute('fill','none'); path.setAttribute('marker-end','url(#'+mId+')');
    const txt=document.createElementNS('http://www.w3.org/2000/svg','text');
    txt.setAttribute('x',mid[0]); txt.setAttribute('y',mid[1]-12); txt.setAttribute('fill',col);
    txt.setAttribute('font-size','11'); txt.setAttribute('text-anchor','middle'); txt.textContent=ed.label||'';
    g.appendChild(path); g.appendChild(txt);
    svgEl.appendChild(g);
    // HTML delete button — lives outside SVG so pointer-events work regardless
    const btn=document.createElement('button');
    btn.className='edge-del-btn';
    btn.style.left=mid[0]+'px'; btn.style.top=mid[1]+'px';
    btn.style.borderColor=col; btn.style.color=col;
    btn.title='Delete connection';
    btn.innerHTML='&times;';
    btn.addEventListener('click', e=>{ e.stopPropagation(); edges=edges.filter(e2=>e2.id!==ed.id); refreshEdges(); });
    if(btnLayer) btnLayer.appendChild(btn);
  });
}

// ── SELECTION & CONFIG PANEL ───────────────────────────────────────────
function selectNode(nd) {
  if(selectedNode) document.getElementById('node-'+selectedNode.id)?.classList.remove('selected');
  selectedNode=nd;
  document.getElementById('node-'+nd.id)?.classList.add('selected');
  renderConfigPanel(nd);
}

function renderConfigPanel(nd) {
  const panel=document.getElementById('wf-config-inner');
  let html=`<div class="cfg-section"><div class="cfg-label">Node Label</div>
    <input class="cfg-input" value="${esc(nd.label)}" oninput="nd_set('label',this.value)"></div>`;

  if(nd.type==='trigger'){
    html+=`<div class="cfg-section"><div class="cfg-label">Trigger Type</div>
      <select class="cfg-input cfg-select" id="cfg-trigger" onchange="nd_set_trigger(this.value)">
        ${['manual','schedule','ticket_created','ticket_updated','vulnerability_detected','patch_failed','user_offboarded','alert_triggered']
          .map(v=>`<option value="${v}"${(nd.config.trigger_type||'manual')===v?' selected':''}>${v.replace(/_/g,' ')}</option>`).join('')}
      </select>
      <div id="cfg-sched-wrap" class="mt-2" style="display:${nd.config.trigger_type==='schedule'?'block':'none'}">
        <div class="cfg-label">Interval (mins)</div>
        <input class="cfg-input mt-1" type="number" min="1" placeholder="60" value="${nd.config.interval||''}" oninput="nd_set('config.interval',+this.value)">
      </div></div>`;

  // ── Tickets ───────────────────────────────────────────────────────────
  } else if(nd.type==='condition'){
    html+=cfgR('Field','text',nd.config.field||'','nd_set(\'config.field\',this.value)');
    html+=`<div class="cfg-section"><div class="cfg-label">Operator</div>
      <select class="cfg-input cfg-select" onchange="nd_set('config.operator',this.value)">
        ${['==','!=','>','<','contains','not_contains'].map(op=>`<option${(nd.config.operator||'==')==op?' selected':''}>${op}</option>`).join('')}
      </select></div>`;
    html+=cfgR('Value','text',nd.config.value||'','nd_set(\'config.value\',this.value)');

  } else if(nd.action==='create_ticket'){
    html+=cfgR('Title','text',nd.config.title||'','nd_set(\'config.title\',this.value)');
    html+=cfgR('Description','text',nd.config.description||'','nd_set(\'config.description\',this.value)');
    html+=`<div class="cfg-section"><div class="cfg-label">Priority</div>
      <select class="cfg-input cfg-select" onchange="nd_set('config.priority',this.value)">
        ${['Low','Medium','High','Critical'].map(p=>`<option${(nd.config.priority||'Medium')===p?' selected':''}>${p}</option>`).join('')}
      </select></div>`;

  } else if(nd.action==='update_ticket'){
    html+=cfgR('Status','text',nd.config.status||'','nd_set(\'config.status\',this.value)');
    html+=cfgR('Note','text',nd.config.note||'','nd_set(\'config.note\',this.value)');
    html+=cfgR('Assigned To','text',nd.config.assigned_to||'','nd_set(\'config.assigned_to\',this.value)');

  } else if(nd.action==='close_ticket'){
    html+=cfgR('Resolution note','text',nd.config.resolution||'Resolved by workflow','nd_set(\'config.resolution\',this.value)');

  } else if(nd.action==='assign_ticket'){
    html+=cfgR('Assign to (username)','text',nd.config.assigned_to||'','nd_set(\'config.assigned_to\',this.value)');

  // ── Notifications ─────────────────────────────────────────────────────
  } else if(nd.action==='send_email'){
    html+=cfgR('To','email',nd.config.to||'','nd_set(\'config.to\',this.value)');
    html+=cfgR('Subject','text',nd.config.subject||'','nd_set(\'config.subject\',this.value)');
    html+=cfgR('Body','text',nd.config.body||'','nd_set(\'config.body\',this.value)');

  } else if(nd.action==='send_notification'){
    html+=cfgR('Message','text',nd.config.message||'','nd_set(\'config.message\',this.value)');
    html+=cfgR('User (blank=all admins)','text',nd.config.user||'','nd_set(\'config.user\',this.value)');

  } else if(nd.action==='send_teams'){
    html+=cfgR('Teams Webhook URL','url',nd.config.webhook_url||'','nd_set(\'config.webhook_url\',this.value)');
    html+=cfgR('Title','text',nd.config.title||'Workflow Alert','nd_set(\'config.title\',this.value)');
    html+=cfgR('Message','text',nd.config.message||'','nd_set(\'config.message\',this.value)');

  } else if(nd.action==='send_slack'){
    html+=cfgR('Slack Webhook URL','url',nd.config.webhook_url||'','nd_set(\'config.webhook_url\',this.value)');
    html+=cfgR('Channel (#channel)','text',nd.config.channel||'','nd_set(\'config.channel\',this.value)');
    html+=cfgR('Message','text',nd.config.message||'','nd_set(\'config.message\',this.value)');

  // ── Active Directory ──────────────────────────────────────────────────
  } else if(nd.action==='create_user'){
    html+=cfgR('Username','text',nd.config.username||'','nd_set(\'config.username\',this.value)');
    html+=cfgR('First Name','text',nd.config.first_name||'','nd_set(\'config.first_name\',this.value)');
    html+=cfgR('Last Name','text',nd.config.last_name||'','nd_set(\'config.last_name\',this.value)');
    html+=cfgR('Password','password',nd.config.password||'','nd_set(\'config.password\',this.value)');
    html+=cfgR('OU (leave blank for default)','text',nd.config.ou||'','nd_set(\'config.ou\',this.value)');
    html+=`<div class="text-secondary mt-1" style="font-size:.74rem">Supports <code>{{ticket_id}}</code>, <code>{{username}}</code> etc.</div>`;

  } else if(nd.action==='disable_ad_user'||nd.action==='enable_ad_user'||nd.action==='unlock_account'){
    html+=cfgR('Username','text',nd.config.username||'{{username}}','nd_set(\'config.username\',this.value)');

  } else if(nd.action==='reset_password'){
    html+=cfgR('Username','text',nd.config.username||'{{username}}','nd_set(\'config.username\',this.value)');
    html+=cfgR('New Password','password',nd.config.new_password||'','nd_set(\'config.new_password\',this.value)');
    html+=`<div class="text-secondary mt-1" style="font-size:.74rem">Consider using a temp password and requiring a change on first login.</div>`;

  } else if(nd.action==='add_to_group'||nd.action==='remove_from_group'){
    html+=cfgR('Username','text',nd.config.username||'{{username}}','nd_set(\'config.username\',this.value)');
    html+=cfgR('Group Name (CN)','text',nd.config.group_name||'','nd_set(\'config.group_name\',this.value)');

  } else if(nd.action==='azure_sync'){
    html+=`<div class="text-secondary" style="font-size:.78rem">Triggers an Azure AD Connect delta sync via the nearest online RMM agent on the domain controller. No extra config needed.</div>`;

  // ── Device Management ─────────────────────────────────────────────────
  } else if(nd.action==='deploy_software'){
    html+=`<div class="cfg-section"><div class="cfg-label">Method</div>
      <select class="cfg-input cfg-select" onchange="nd_set('config.method',this.value)">
        ${['chocolatey','winget','msi','exe'].map(m=>`<option${(nd.config.method||'chocolatey')===m?' selected':''}>${m}</option>`).join('')}
      </select></div>`;
    html+=cfgR('Package / Path','text',nd.config.package||'','nd_set(\'config.package\',this.value)');
    html+=cfgR('Extra args (optional)','text',nd.config.args||'','nd_set(\'config.args\',this.value)');
    html+=cfgR('Asset ID (blank=ctx)','text',nd.config.asset_id||'','nd_set(\'config.asset_id\',this.value)');
    html+=`<div class="text-secondary mt-1" style="font-size:.74rem">e.g. <code>googlechrome</code> (choco), <code>Google.Chrome</code> (winget), <code>C:\\setup.msi</code> (msi)</div>`;

  } else if(nd.action==='uninstall_software'){
    html+=`<div class="cfg-section"><div class="cfg-label">Method</div>
      <select class="cfg-input cfg-select" onchange="nd_set('config.method',this.value)">
        ${['chocolatey','winget','wmi'].map(m=>`<option${(nd.config.method||'chocolatey')===m?' selected':''}>${m}</option>`).join('')}
      </select></div>`;
    html+=cfgR('Package Name','text',nd.config.package||'','nd_set(\'config.package\',this.value)');
    html+=cfgR('Asset ID (blank=ctx)','text',nd.config.asset_id||'','nd_set(\'config.asset_id\',this.value)');

  } else if(nd.action==='run_script'){
    html+=`<div class="cfg-section"><div class="cfg-label">Language</div>
      <select class="cfg-input cfg-select" onchange="nd_set('config.language',this.value)">
        ${['powershell','bash'].map(m=>`<option${(nd.config.language||'powershell')===m?' selected':''}>${m}</option>`).join('')}
      </select></div>`;
    html+=cfgR('Asset ID (blank=ctx)','text',nd.config.asset_id||'','nd_set(\'config.asset_id\',this.value)');
    html+=`<div class="cfg-section"><div class="cfg-label">Script</div>
      <textarea class="cfg-input" rows="5" oninput="nd_set('config.script',this.value)">${esc(nd.config.script||'')}</textarea></div>`;
    html+=`<div class="text-secondary mt-1" style="font-size:.74rem">Supports <code>{{asset_id}}</code>, <code>{{ticket_id}}</code>, <code>{{username}}</code></div>`;

  } else if(nd.action==='deploy_patch'){
    html+=cfgR('CVE ID','text',nd.config.cve_id||'{{cve_id}}','nd_set(\'config.cve_id\',this.value)');
    html+=cfgR('Asset ID (blank=all)','text',nd.config.asset_id||'','nd_set(\'config.asset_id\',this.value)');

  } else if(nd.action==='reboot_device'){
    html+=cfgR('Asset ID (blank=ctx)','text',nd.config.asset_id||'','nd_set(\'config.asset_id\',this.value)');
    html+=cfgR('Delay before reboot (sec)','number',nd.config.delay_seconds||0,'nd_set(\'config.delay_seconds\',+this.value)');

  } else if(nd.action==='shutdown_device'){
    html+=cfgR('Asset ID (blank=ctx)','text',nd.config.asset_id||'','nd_set(\'config.asset_id\',this.value)');
    html+=`<div class="text-secondary" style="font-size:.78rem">Sends <code>Stop-Computer -Force</code> immediately.</div>`;

  } else if(nd.action==='lock_device'){
    html+=cfgR('Asset ID (blank=ctx)','text',nd.config.asset_id||'','nd_set(\'config.asset_id\',this.value)');
    html+=`<div class="cfg-section"><div class="cfg-label">Mode</div>
      <select class="cfg-input cfg-select" onchange="nd_set('config.mode',this.value)">
        ${[['lock','Lock Workstation'],['bitlocker','Enable BitLocker']].map(([v,l])=>`<option value="${v}"${(nd.config.mode||'lock')===v?' selected':''}>${l}</option>`).join('')}
      </select></div>`;

  } else if(nd.action==='apply_gpo'){
    html+=cfgR('Asset ID (blank=ctx)','text',nd.config.asset_id||'','nd_set(\'config.asset_id\',this.value)');
    html+=`<div class="cfg-section"><div class="cfg-label">Force update?</div>
      <select class="cfg-input cfg-select" onchange="nd_set('config.force',this.value==='true')">
        <option value="true"${nd.config.force!==false?' selected':''}>Yes — gpupdate /force</option>
        <option value="false"${nd.config.force===false?' selected':''}>No — gpupdate</option>
      </select></div>`;

  // ── Integration ───────────────────────────────────────────────────────
  } else if(nd.action==='webhook'){
    html+=cfgR('URL','url',nd.config.url||'','nd_set(\'config.url\',this.value)');
    html+=`<div class="cfg-section"><div class="cfg-label">Method</div>
      <select class="cfg-input cfg-select" onchange="nd_set('config.method',this.value)">
        ${['POST','GET','PUT'].map(m=>`<option${(nd.config.method||'POST')===m?' selected':''}>${m}</option>`).join('')}
      </select></div>`;
    html+=cfgR('Payload (JSON)','text',nd.config.body||'{}','nd_set(\'config.body\',this.value)');

  } else if(nd.action==='http_request'){
    html+=cfgR('URL','url',nd.config.url||'','nd_set(\'config.url\',this.value)');
    html+=`<div class="cfg-section"><div class="cfg-label">Method</div>
      <select class="cfg-input cfg-select" onchange="nd_set('config.method',this.value)">
        ${['GET','POST','PUT','PATCH','DELETE'].map(m=>`<option${(nd.config.method||'GET')===m?' selected':''}>${m}</option>`).join('')}
      </select></div>`;
    html+=cfgR('Headers (JSON, optional)','text',nd.config.headers||'{}','nd_set(\'config.headers\',this.value)');
    html+=cfgR('Body (optional)','text',nd.config.body||'','nd_set(\'config.body\',this.value)');

  } else if(nd.action==='wait'){
    html+=cfgR('Seconds','number',nd.config.seconds||30,'nd_set(\'config.seconds\',+this.value)');

  } else if(nd.action==='ai_suggest'){
    html+=`<div class="text-secondary" style="font-size:.78rem">Analyses the ticket (<code>{{ticket_id}}</code>) and generates an AI-powered remediation suggestion. Requires OpenAI key in Settings.</div>`;
  }

  html+=`<div class="mt-3"><button class="btn btn-sm btn-outline-danger w-100" onclick="deleteNode('${nd.id}')"><i class="bi bi-trash3"></i> Delete Node</button></div>`;
  panel.innerHTML=html;
}

function cfgR(label,type,val,oninput){
  return `<div class="cfg-section"><div class="cfg-label">${label}</div>
    <input class="cfg-input" type="${type}" value="${esc(String(val))}" oninput="${oninput}"></div>`;
}

function nd_set(key,val){
  if(!selectedNode) return;
  const p=key.split('.');
  if(p.length===1) selectedNode[key]=val;
  else selectedNode[p[0]][p[1]]=val;
  updateNodeBody(selectedNode);
}

function nd_set_trigger(val){
  nd_set('config.trigger_type',val);
  document.getElementById('wf-trigger-type').value=val;
  const w=document.getElementById('cfg-sched-wrap');
  if(w) w.style.display=val==='schedule'?'block':'none';
}

// ── DELETE / CLEAR ─────────────────────────────────────────────────────
function deleteNode(nid){
  nodes=nodes.filter(n=>n.id!==nid); edges=edges.filter(e=>e.fromNode!==nid&&e.toNode!==nid);
  document.getElementById('node-'+nid)?.remove();
  if(selectedNode?.id===nid){selectedNode=null;resetCfgPanel();}
  refreshEdges(); updateDropHint();
}

function clearCanvas(){
  if(nodes.length&&!confirm('Clear canvas?')) return;
  nodes=[]; edges=[]; selectedNode=null; nodeIdSeq=1; edgeIdSeq=1;
  nodesDiv.innerHTML=''; svgEl.innerHTML='';
  const bl=document.getElementById('wf-edge-btns'); if(bl) bl.innerHTML='';
  updateDropHint(); resetCfgPanel();
}

function resetCfgPanel(){document.getElementById('wf-config-inner').innerHTML='<div class="text-center text-secondary py-4" style="font-size:.8rem"><i class="bi bi-cursor-fill" style="font-size:1.5rem"></i><br>Click a node to configure</div>';}

function updateDropHint(){dropHint.style.display=nodes.length?'none':'block';}

// ── SAVE / LOAD ────────────────────────────────────────────────────────
async function saveWorkflow(){
  const payload={name:document.getElementById('wf-title').value,trigger_type:document.getElementById('wf-trigger-type').value,enabled:document.getElementById('wf-enabled').checked,
    nodes:nodes.map(n=>({id:n.id,type:n.type,action:n.action,label:n.label,x:n.x,y:n.y,config:n.config})),edges};
  const method=currentWfId?'PUT':'POST', url=currentWfId?`/api/workflows/${currentWfId}`:'/api/workflows';
  const resp=await fetch(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const data=await resp.json();
  if(resp.ok){if(!currentWfId&&data.id){currentWfId=data.id;document.getElementById('btn-run').disabled=false;}showToast('Workflow saved','success');loadWorkflowList();}
  else showToast(data.error||'Save failed','danger');
}

async function loadWorkflow(id){
  const resp=await fetch('/api/workflows/'+id); const wf=await resp.json(); if(!resp.ok) return;
  currentWfId=wf.id; clearCanvas();
  document.getElementById('wf-title').value=wf.name;
  document.getElementById('wf-trigger-type').value=wf.trigger_type;
  document.getElementById('wf-enabled').checked=!!wf.enabled;
  document.getElementById('btn-run').disabled=false;
  (wf.nodes||[]).forEach(n=>{addNode(n.type,n.action||'',n.x,n.y,n.config||{},n.id,n.label);const s=parseInt((n.id||'').replace('n',''));if(!isNaN(s)&&s>=nodeIdSeq)nodeIdSeq=s+1;});
  (wf.edges||[]).forEach(e=>{edges.push(e);const s=parseInt((e.id||'').replace('e',''));if(!isNaN(s)&&s>=edgeIdSeq)edgeIdSeq=s+1;});
  refreshEdges(); loadRunHistory(id);
  document.querySelectorAll('.wf-item').forEach(el=>el.classList.toggle('active',el.dataset.id==id));
}

function applyGeneratedWorkflow(wf){
  clearCanvas();
  if(wf.name) document.getElementById('wf-title').value=wf.name;
  if(wf.trigger_type) document.getElementById('wf-trigger-type').value=wf.trigger_type;
  (wf.nodes||[]).forEach(n=>{addNode(n.type,n.action||'',n.x||80,n.y||200,n.config||{},n.id,n.label);const s=parseInt((n.id||'').replace('n',''));if(!isNaN(s)&&s>=nodeIdSeq)nodeIdSeq=s+1;});
  (wf.edges||[]).forEach(e=>{edges.push(e);const s=parseInt((e.id||'').replace('e',''));if(!isNaN(s)&&s>=edgeIdSeq)edgeIdSeq=s+1;});
  refreshEdges();
}

async function loadWorkflowList(){
  const resp=await fetch('/api/workflows'); const list=await resp.json();
  const el=document.getElementById('wf-list');
  if(!list.length){el.innerHTML='<div class="text-center text-secondary py-4" style="font-size:.78rem">No workflows yet.</div>';return;}
  el.innerHTML=list.map(wf=>`<div class="wf-item${wf.id===currentWfId?' active':''}" data-id="${wf.id}" onclick="loadWorkflow(${wf.id})">
    <span class="wf-badge badge bg-${wf.enabled?'success':'secondary'}">${wf.enabled?'ON':'OFF'}</span>
    <span class="wf-name">${esc(wf.name)}</span></div>`).join('');
}

async function runWorkflow(){
  if(!currentWfId) return;
  const resp=await fetch(`/api/workflows/${currentWfId}/run`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const d=await resp.json();
  if(resp.ok){showToast('Workflow started — Run #'+d.run_id,'success');setTimeout(()=>loadRunHistory(currentWfId),1500);}
  else showToast(d.error||'Failed','danger');
}

function newWorkflow(){
  currentWfId=null; clearCanvas();
  document.getElementById('wf-title').value='New Workflow';
  document.getElementById('wf-trigger-type').value='manual';
  document.getElementById('wf-enabled').checked=true;
  document.getElementById('btn-run').disabled=true;
  document.querySelectorAll('.wf-item').forEach(el=>el.classList.remove('active'));
  document.getElementById('run-history').innerHTML='';
}

async function loadRunHistory(wfId){
  const resp=await fetch(`/api/workflows/${wfId}/runs`); const runs=await resp.json();
  const el=document.getElementById('run-history');
  if(!runs.length){el.innerHTML='<span style="color:var(--text-secondary);font-size:.78rem">No runs yet</span>';return;}
  const C={success:'#5fcf8b',failed:'#f87171',running:'#6aacf8',pending:'#f8a84a'};
  el.innerHTML=runs.map(r=>`<div class="run-row"><span style="color:${C[r.status]||'var(--text-secondary)'};font-size:.78rem;font-weight:600">${r.status.toUpperCase()}</span>
    <span style="color:var(--text-secondary);font-size:.78rem">${r.started_at||'—'}</span>
    ${r.error?`<span style="color:#f87171;font-size:.74rem">${esc(r.error)}</span>`:''}</div>`).join('');
}

// ── AI GENERATION ─────────────────────────────────────────────────────
async function aiGenerateWorkflow(){
  const prompt=document.getElementById('ai-prompt-input').value.trim();
  if(!prompt){document.getElementById('ai-prompt-input').focus();return;}
  const btn=document.getElementById('ai-generate-btn'), spin=document.getElementById('ai-gen-spin'), icon=document.getElementById('ai-gen-icon'), result=document.getElementById('ai-wf-result');
  btn.disabled=true; spin.style.display='inline-block'; icon.style.display='none'; result.style.display='none';
  const resp=await fetch('/api/workflows/ai-generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt})}).catch(()=>null);
  btn.disabled=false; spin.style.display='none'; icon.style.display='';
  if(!resp||!resp.ok){result.innerHTML='<div class="alert alert-danger py-2 mb-0" style="font-size:.8rem">Request failed.</div>';result.style.display='';return;}
  const data=await resp.json();
  if(!data.ok){result.innerHTML=`<div class="alert alert-danger py-2 mb-0" style="font-size:.8rem">${esc(data.error||'Generation failed')}</div>`;result.style.display='';return;}
  window._pendingAiWf=data.workflow;
  const nc=(data.workflow.nodes||[]).length, ec=(data.workflow.edges||[]).length;
  result.innerHTML=`<div class="alert alert-success py-2 mb-0" style="font-size:.8rem">
    <i class="bi bi-check-circle me-1"></i>Generated <strong>${nc} nodes</strong>, <strong>${ec} connections</strong>.
    ${data.workflow.name?`<br><span class="text-muted">${esc(data.workflow.name)}</span>`:''}
    <br><button class="btn btn-sm btn-success mt-2" onclick="applyAndClose()"><i class="bi bi-check-lg me-1"></i>Apply to Canvas</button>
  </div>`;
  result.style.display='';
}

function applyAndClose(){
  if(window._pendingAiWf){applyGeneratedWorkflow(window._pendingAiWf);window._pendingAiWf=null;}
  bootstrap.Modal.getInstance(document.getElementById('aiWfModal'))?.hide();
  document.getElementById('ai-prompt-input').value='';
  document.getElementById('ai-wf-result').style.display='none';
  showToast('AI workflow applied — review and save','success');
}

// ── UTILITY ───────────────────────────────────────────────────────────
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

function showToast(msg,type='info'){
  const t=document.createElement('div');
  t.className=`alert alert-${type} position-fixed`;
  t.style.cssText='bottom:20px;right:20px;z-index:9999;min-width:240px;';
  t.innerHTML=`<i class="bi bi-${type==='success'?'check-circle':'exclamation-circle'}-fill me-2"></i>${msg}`;
  document.body.appendChild(t); setTimeout(()=>t.remove(),3200);
}

loadWorkflowList();
