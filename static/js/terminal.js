const assetId = window.TERMINALCFG.asset_id;
let sessionId = null;
let outputIndex = 0;
let pollInterval = null;
let isConnected = false;

const $connectionStatus = $('#connection-status');
const $connectionPanel = $('#connection-panel');
const $terminalOutput = $('#terminal-output');
const $terminalInput = $('#terminal-input');
const $connectionForm = $('#connection-form');
const $clearBtn = $('#clear-btn');
const $disconnectBtn = $('#disconnect-btn');

// Update status UI
function updateStatus(status) {
    $connectionStatus.removeClass('status-connected status-disconnected status-connecting');
    
    if (status === 'connected') {
        $connectionStatus.addClass('status-connected').text('Connected');
        $terminalInput.prop('disabled', false);
        $clearBtn.prop('disabled', false);
        $disconnectBtn.prop('disabled', false);
        $connectionPanel.hide();
        isConnected = true;
    } else if (status === 'connecting') {
        $connectionStatus.addClass('status-connecting').text('Connecting...');
        isConnected = false;
    } else {
        $connectionStatus.addClass('status-disconnected').text('Disconnected');
        $terminalInput.prop('disabled', true);
        $clearBtn.prop('disabled', true);
        $disconnectBtn.prop('disabled', true);
        $connectionPanel.show();
        isConnected = false;
    }
}

// Connect to SSH
$connectionForm.on('submit', function(e) {
    e.preventDefault();
    
    const username = $('#ssh-username').val();
    const password = $('#ssh-password').val();
    
    if (!username || !password) {
        alert('Username and password are required');
        return;
    }
    
    updateStatus('connecting');
    
    $.ajax({
        url: '/api/terminal/connect',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({
            asset_id: assetId,
            username: username,
            password: password
        }),
        success: function(response) {
            if (response.success) {
                sessionId = response.session_id;
                outputIndex = 0;
                updateStatus('connected');
                $terminalInput.focus();
                
                // Start polling for output
                startPolling();
            } else {
                updateStatus('disconnected');
                alert('Connection failed: ' + (response.error || 'Unknown error'));
            }
        },
        error: function(xhr) {
            updateStatus('disconnected');
            alert('Connection failed: ' + (xhr.responseJSON?.error || 'Network error'));
        }
    });
});

// Send input to terminal
$terminalInput.on('keypress', function(e) {
    if (e.which === 13) {  // Enter key
        const input = $(this).val();
        sendInput(input + '\n');
        $(this).val('');
    }
});

function sendInput(data) {
    if (!sessionId || !isConnected) return;
    
    $.ajax({
        url: '/api/terminal/input',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({
            session_id: sessionId,
            data: data
        }),
        error: function(xhr) {
            console.error('Failed to send input:', xhr.responseJSON?.error);
        }
    });
}

// Poll for output
function startPolling() {
    pollInterval = setInterval(pollOutput, 500);  // Poll every 500ms
}

function stopPolling() {
    if (pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
    }
}

function pollOutput() {
    if (!sessionId || !isConnected) {
        stopPolling();
        return;
    }
    
    $.ajax({
        url: '/api/terminal/output',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({
            session_id: sessionId,
            since_index: outputIndex
        }),
        success: function(response) {
            if (response.success && response.output.length > 0) {
                response.output.forEach(function(item) {
                    appendOutput(item.data);
                });
                outputIndex = response.index;
            }
        },
        error: function(xhr) {
            if (xhr.status === 404) {
                // Session not found, disconnect
                disconnect();
            }
        }
    });
}

function appendOutput(text) {
    $terminalOutput.append(text);
    
    // Auto-scroll to bottom
    const container = document.getElementById('terminal-container');
    container.scrollTop = container.scrollHeight;
}

// Clear terminal
$clearBtn.on('click', function() {
    $terminalOutput.empty();
});

// Disconnect
$disconnectBtn.on('click', function() {
    disconnect();
});

function disconnect() {
    if (!sessionId) return;
    
    stopPolling();
    
    $.ajax({
        url: '/api/terminal/disconnect',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({
            session_id: sessionId
        }),
        complete: function() {
            sessionId = null;
            outputIndex = 0;
            updateStatus('disconnected');
            $terminalOutput.empty();
        }
    });
}

// Cleanup on page unload
$(window).on('beforeunload', function() {
    if (isConnected) {
        disconnect();
    }
});

// Initialize
updateStatus('disconnected');
