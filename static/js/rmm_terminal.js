const AGENT_ID    = window.RMMTERMINALCFG.agent_id;
const GATEWAY_URL = window.RMMTERMINALCFG.gateway_url;

const term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: 'Cascadia Code, Consolas, monospace',
    theme: {
        background: '#0d0d0d',
        foreground: '#f2f2f2',
        cursor: '#00ff00',
    },
    convertEol: true,
    scrollback: 5000,
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
    const resp = await fetch('/api/rmm/issue-token', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({agent_id: AGENT_ID}),
    });
    if (!resp.ok) throw new Error('Failed to get session token');
    return await resp.json();
}

async function connect(shellType) {
    shellType = shellType || 'powershell';
    currentShell = shellType;
    setStatus('connecting', 'Connecting…');
    term.write('\r\n\x1b[33m[Tracker RMM] Connecting to ' + AGENT_ID + '…\x1b[0m\r\n');

    let tokenData;
    try {
        tokenData = await issueToken();
    } catch (e) {
        term.write('\x1b[31m[ERROR] ' + e.message + '\x1b[0m\r\n');
        setStatus('offline', 'Auth failed');
        return;
    }

    const wsUrl = GATEWAY_URL.replace(/^wss/, 'wss') + '/ws/tech/' + AGENT_ID + '?session_token=' + tokenData.token;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        setStatus('connecting', 'Authenticating…');
    };

    ws.onmessage = (evt) => {
        let msg;
        try { msg = JSON.parse(evt.data); } catch { return; }

        if (msg.type === 'session') {
            sessionId = msg.session_id;
            setStatus('online', 'Connected (session #' + sessionId + ')');
            term.write('\x1b[32m[Tracker RMM] Session established. Starting ' + shellType + '…\x1b[0m\r\n\r\n');
            startShell(shellType);
            return;
        }

        if (msg.type === 'shell_started') {
            shellActive = true;
            // Trigger initial prompt render for shells that don't emit one until first input.
            ws.send(JSON.stringify({type: 'shell_input', session_id: sessionId, data: '\n'}));
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
        setStatus('offline', 'Disconnected');
        term.write('\r\n\x1b[31m[Tracker RMM] Disconnected (code ' + e.code + ')\x1b[0m\r\n');
        ws = null; sessionId = null;
    };

    ws.onerror = () => {
        setStatus('offline', 'Connection error');
        term.write('\r\n\x1b[31m[ERROR] WebSocket connection failed\x1b[0m\r\n');
    };
}

function startShell(shellType) {
    if (!ws || !sessionId) return;
    currentShell = shellType || 'powershell';
    ws.send(JSON.stringify({type: 'shell_start', session_id: sessionId, shell: shellType}));
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

// Send keystrokes to shell
term.onData(data => {
    if (ws && ws.readyState === WebSocket.OPEN && sessionId && shellActive) {
        // Windows shell line-editing expects BS and CRLF.
        if (currentShell === 'powershell' || currentShell === 'cmd') {
            data = data.replace(/\x7f/g, '\x08');
            data = data.replace(/\r/g, '\r\n');
        }
        ws.send(JSON.stringify({type: 'shell_input', session_id: sessionId, data: data}));
    }
});

// Resize PTY when xterm resizes
term.onResize(({cols, rows}) => {
    if (ws && ws.readyState === WebSocket.OPEN && sessionId) {
        ws.send(JSON.stringify({type: 'shell_resize', session_id: sessionId, cols, rows}));
    }
});

// Auto-connect on page load
connect('powershell');
