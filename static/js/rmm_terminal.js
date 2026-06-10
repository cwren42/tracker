const AGENT_ID     = window.RMMTERMINAL_CFG.agent_id;
// Ordered list of gateway URLs to try: LAN-preferred first, public fallback second.
// The browser terminal fails over LAN->public the same way the agents do, so a dead
// LAN gateway host (which doesn't serve /ws/tech) doesn't break the shell.
const GATEWAY_URLS = [...new Set(
    [window.RMMTERMINAL_CFG.gateway_url, window.RMMTERMINAL_CFG.gateway_url_fallback].filter(Boolean)
)];

const term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: 'Cascadia Code, Consolas, monospace',
    theme: {
        background: '#0d0d0d',
        foreground: '#f2f2f2',
        cursor: '#00ff00',
    },
    convertEol: false,      // ConPTY sends its own CRLF — don't double-convert
    scrollback: 5000,
    allowProposedApi: true, // needed for proposeDimensions()
});
const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);
term.open(document.getElementById('terminal'));
fitAddon.fit();
window.addEventListener('resize', () => fitAddon.fit());

let ws = null;
let sessionId = null;
let shellActive = false;
let currentShell = 'powershell';

function setStatus(state, label) {
    const dot = document.getElementById('status-dot');
    const lbl = document.getElementById('status-label');
    dot.className = 'status-dot dot-' + state;
    lbl.textContent = label;
}

async function issueToken() {
    // Attach the CSRF token explicitly. This page auto-connects on script load
    // (see connect('powershell') at the bottom), which runs BEFORE base.html's
    // deferred fetch() CSRF monkey-patch installs — so we cannot rely on that
    // patch to inject X-CSRFToken here, or the POST gets a 400 "CSRF token is
    // missing" and the shell shows "Failed to get session token".
    const csrf = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
    const resp = await fetch('/api/rmm/issue-token', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf},
        body: JSON.stringify({agent_id: AGENT_ID}),
    });
    if (!resp.ok) throw new Error('Failed to get session token');
    return await resp.json();
}

async function connect(shellType, urlIdx) {
    urlIdx = urlIdx || 0;
    shellType = shellType || 'powershell';
    currentShell = shellType;
    setStatus('connecting', 'Connecting…');
    if (urlIdx === 0) term.write('\r\n\x1b[33m[Tracker RMM] Connecting to ' + AGENT_ID + '…\x1b[0m\r\n');

    let tokenData;
    try {
        tokenData = await issueToken();
    } catch (e) {
        term.write('\x1b[31m[ERROR] ' + e.message + '\x1b[0m\r\n');
        setStatus('offline', 'Auth failed');
        return;
    }

    const base = GATEWAY_URLS[urlIdx];
    const hasFallback = (urlIdx + 1) < GATEWAY_URLS.length;
    const wsUrl = base + '/ws/tech/' + AGENT_ID + '?session_token=' + tokenData.token;
    ws = new WebSocket(wsUrl);
    let established = false;   // true once the gateway sends the 'session' message

    ws.onopen = () => {
        setStatus('connecting', 'Authenticating…');
    };

    ws.onmessage = (evt) => {
        let msg;
        try { msg = JSON.parse(evt.data); } catch { return; }

        if (msg.type === 'session') {
            established = true;
            sessionId = msg.session_id;
            setStatus('online', 'Connected (session #' + sessionId + ')');
            term.write('\x1b[32m[Tracker RMM] Session established. Starting ' + shellType + '…\x1b[0m\r\n\r\n');
            startShell(shellType);
            return;
        }

        if (msg.type === 'shell_started') {
            shellActive = true;
            // ConPTY/PSReadLine shows the prompt automatically — no need to send \n.
            // For raw-pipe fallback the user presses Enter once to get the first prompt.
            return;
        }

        if (msg.type === 'shell_output') {
            term.write(msg.data);
            return;
        }

        if (msg.type === 'shell_exited') {
            shellActive = false;
            term.write('\r\n\x1b[33m[Shell exited]\x1b[0m\r\n');
            return;
        }

        if (msg.type === 'error') {
            term.write('\r\n\x1b[31m[ERROR] ' + msg.error + '\x1b[0m\r\n');
            setStatus('offline', 'Error');
        }
    };

    ws.onclose = (e) => {
        shellActive = false;
        ws = null;
        // Never established a session AND another gateway URL is available -> fail over.
        if (!established && hasFallback) {
            term.write('\x1b[33m[Tracker RMM] ' + base + ' unreachable — trying fallback gateway…\x1b[0m\r\n');
            sessionId = null;
            connect(shellType, urlIdx + 1);
            return;
        }
        setStatus('offline', 'Disconnected');
        term.write('\r\n\x1b[31m[Tracker RMM] Disconnected (code ' + e.code + ')\x1b[0m\r\n');
        sessionId = null;
    };

    ws.onerror = () => {
        // A failed connection fires onerror then onclose; let onclose own failover/
        // reporting so we don't double-message or report before trying the fallback.
        if (!established && hasFallback) return;
        setStatus('offline', 'Connection error');
        term.write('\r\n\x1b[31m[ERROR] WebSocket connection failed\x1b[0m\r\n');
    };
}

function startShell(shellType) {
    if (!ws || !sessionId) return;
    currentShell = shellType || 'powershell';
    const dims = fitAddon.proposeDimensions() || {cols: term.cols, rows: term.rows};
    ws.send(JSON.stringify({
        type: 'shell_start',
        session_id: sessionId,
        shell: shellType,
        cols: dims.cols,
        rows: dims.rows,
    }));
}

function restartShell(shellType) {
    if (!ws || !sessionId) { connect(shellType); return; }
    ws.send(JSON.stringify({type: 'shell_stop', session_id: sessionId}));
    shellActive = false;
    setTimeout(() => startShell(shellType), 400);
}

function sendCtrlC() {
    if (ws && sessionId && shellActive) {
        ws.send(JSON.stringify({type: 'shell_input', session_id: sessionId, data: '\x03'}));
    }
}

function disconnectAndClose() {
    if (ws) {
        if (sessionId) ws.send(JSON.stringify({type: 'shell_stop', session_id: sessionId}));
        ws.close();
    }
}

// Send keystrokes to shell.
// xterm.js sends DEL (0x7f) for Backspace; Windows console/ConPTY expects BS (0x08).
term.onData(data => {
    if (ws && ws.readyState === WebSocket.OPEN && sessionId && shellActive) {
        data = data.replace(/\x7f/g, '\x08');
        ws.send(JSON.stringify({type: 'shell_input', session_id: sessionId, data: data}));
    }
});

// Resize ConPTY when xterm resizes
term.onResize(({cols, rows}) => {
    if (ws && ws.readyState === WebSocket.OPEN && sessionId) {
        ws.send(JSON.stringify({type: 'shell_resize', session_id: sessionId, cols, rows}));
    }
});

// Auto-connect on page load
connect('powershell');
