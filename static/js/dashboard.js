/* index.html (dashboard) extracted JS — merged from 3 script blocks */
/* Expects window.DASH_CFG set by template config block */


/* ─── Block 1 ─────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {
    // Add widget functionality from modal
    document.querySelectorAll('.widget-option').forEach(option => {
        option.addEventListener('click', function() {
            const widgetId = this.dataset.widgetId;
            const widgetType = this.dataset.widgetType;
            
            // Get selected size from radio buttons, or use widget's default size
            const sizeRadio = document.querySelector('input[name="widgetSize"]:checked');
            const selectedSize = sizeRadio ? sizeRadio.value : (this.dataset.widgetSize || 'col-md-4');
            
            // Send request to add widget to dashboard
            fetch(window.DASH_CFG.addWidgetUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    widget_id: widgetId,
                    widget_type: widgetType,
                    title: this.querySelector('strong').textContent,
                    size: selectedSize + ' widget-1-row'
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // Show success message and reload
                    alert(data.message || 'Widget added successfully! Reloading dashboard...');
                    window.location.reload();
                } else {
                    // Show error
                    alert(data.message || 'Failed to add widget');
                }
            })
            .catch(error => {
                console.error('Error:', error);
                alert('Failed to add widget to dashboard');
            });
            
            // Close modal
            const modal = bootstrap.Modal.getInstance(document.getElementById('addWidgetModal'));
            if (modal) {
                modal.hide();
            }
        });
    });
});

/* ─── Block 2 ─────────────────────────────────── */

document.addEventListener('DOMContentLoaded', function() {
    const widgetsContainer = document.getElementById('dashboardWidgets');
    if (!widgetsContainer || typeof Sortable === 'undefined') return;
    
    // Initialize Sortable for drag-and-drop
    const sortable = new Sortable(widgetsContainer, {
        animation: 200,
        easing: 'cubic-bezier(0.25, 0.8, 0.25, 1)',
        handle: '.widget-drag-handle',
        draggable: '.widget-container',
        filter: '.add-widget-card',
        preventOnFilter: false,
        ghostClass: 'sortable-ghost',
        chosenClass: 'sortable-chosen',
        dragClass: 'sortable-drag',
        swapThreshold: 0.5,
        emptyInsertThreshold: 15,
        invertSwap: true,
        invertedSwapThreshold: 0.5,
        direction: function(evt, target, dragEl) {
            // Auto-detect direction based on widget layout
            return 'vertical';
        },
        forceFallback: false,
        fallbackClass: 'sortable-fallback',
        fallbackOnBody: false,
        fallbackTolerance: 3,
        scroll: true,
        scrollSensitivity: 50,
        scrollSpeed: 10,
        bubbleScroll: true,
        onStart: function(evt) {
            evt.item.classList.add('dragging');
            document.body.style.cursor = 'grabbing';
        },
        onEnd: function(evt) {
            evt.item.classList.remove('dragging');
            document.body.style.cursor = '';
            console.log('Widget moved from position ' + evt.oldIndex + ' to ' + evt.newIndex);
        },
        onMove: function(evt) {
            // Prevent moving into the add-widget card
            return evt.related.classList.contains('add-widget-card') ? false : true;
        }
    });
    
    // Remove widget functionality
    document.addEventListener('click', function(e) {
        if (e.target.closest('.widget-remove-btn')) {
            e.preventDefault();
            const widgetContainer = e.target.closest('.widget-container');
            if (confirm('Remove this widget from your dashboard?')) {
                widgetContainer.remove();
            }
        }
    });
    
    // Resize widget functionality
    document.addEventListener('click', function(e) {
        if (e.target.closest('.resize-option')) {
            e.preventDefault();
            const resizeOption = e.target.closest('.resize-option');
            const newSize = resizeOption.dataset.size;
            const resizeType = resizeOption.dataset.type;
            const widgetContainer = e.target.closest('.widget-container');
            
            if (resizeType === 'width') {
                // Remove all col-md-* classes
                widgetContainer.className = widgetContainer.className
                    .split(' ')
                    .filter(cls => !cls.startsWith('col-md-'))
                    .join(' ');
                
                // Add new width class
                widgetContainer.classList.add(newSize);
            } else if (resizeType === 'height') {
                // Remove all widget-*-row* classes
                widgetContainer.className = widgetContainer.className
                    .split(' ')
                    .filter(cls => !cls.includes('widget-') || !cls.includes('row'))
                    .join(' ');
                
                // Add new height class
                widgetContainer.classList.add(newSize);
            }
        }
    });
    
    // Save dashboard configuration
    document.getElementById('saveDashboard').addEventListener('click', function() {
        const widgets = [];
        document.querySelectorAll('.widget-container').forEach((widget, index) => {
            // Get both width and height classes
            const classes = widget.className.split(' ');
            const widthClass = classes.find(c => c.startsWith('col-md-')) || 'col-md-4';
            const heightClass = classes.find(c => c.startsWith('widget-') && c.includes('row')) || 'widget-1-row';
            const sizeString = `${widthClass} ${heightClass}`;
            
            widgets.push({
                id: widget.dataset.widgetId,
                type: widget.dataset.widgetType,
                title: widget.querySelector('.card-title, p')?.textContent.trim() || '',
                position: index,
                size: sizeString,
                enabled: true,
                config: {}
            });
        });
        
        // Send to server
        fetch(window.DASH_CFG.configureUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(widgets)
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                window.location.href = window.DASH_CFG.indexUrl;
            } else {
                alert('Error saving dashboard: ' + data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            alert('Error saving dashboard configuration');
        });
    });
});

function addWidgetToDashboard(widgetId, widgetType, widgetSize, widgetHeight) {
    // Create widget element
    const widgetHtml = `
        <div class="widget-container ${widgetSize} ${widgetHeight}" data-widget-id="${widgetId}" data-widget-type="${widgetType}">
            <div class="card">
                <div class="card-body text-center">
                    <p class="mb-0">Widget: ${widgetId}</p>
                    <small class="text-muted">Save to load data</small>
                </div>
            </div>
            <div class="widget-controls">
                <button class="btn btn-sm btn-danger widget-remove-btn" title="Remove widget">
                    <i class="bi bi-x"></i>
                </button>
                <div class="btn-group widget-resize-btn" role="group">
                    <button type="button" class="btn btn-sm btn-primary dropdown-toggle" data-bs-toggle="dropdown" aria-expanded="false" title="Resize widget">
                        <i class="bi bi-arrows-angle-expand"></i>
                    </button>
                    <ul class="dropdown-menu dropdown-menu-end">
                        <li><h6 class="dropdown-header">Width</h6></li>
                        <li><a class="dropdown-item resize-option" href="#" data-size="col-md-2" data-type="width">Small (1 col)</a></li>
                        <li><a class="dropdown-item resize-option" href="#" data-size="col-md-3" data-type="width">Medium (2 cols)</a></li>
                        <li><a class="dropdown-item resize-option" href="#" data-size="col-md-4" data-type="width">Large (3 cols)</a></li>
                        <li><a class="dropdown-item resize-option" href="#" data-size="col-md-6" data-type="width">X-Large (Half)</a></li>
                        <li><a class="dropdown-item resize-option" href="#" data-size="col-md-12" data-type="width">Full Width</a></li>
                        <li><hr class="dropdown-divider"></li>
                        <li><h6 class="dropdown-header">Height</h6></li>
                        <li><a class="dropdown-item resize-option" href="#" data-size="widget-1-row" data-type="height">1 Row</a></li>
                        <li><a class="dropdown-item resize-option" href="#" data-size="widget-2-rows" data-type="height">2 Rows</a></li>
                    </ul>
                </div>
            </div>
        </div>
    `;
    
    // Insert before the "Add Widget" card
    const addCard = document.querySelector('.add-widget-card').closest('.col-md-3');
    addCard.insertAdjacentHTML('beforebegin', widgetHtml);
}

/* ─── Block 3 ─────────────────────────────────── */

// AI Ask Panel
(function () {
    const input = document.getElementById('aiAskInput');
    const btn = document.getElementById('aiAskBtn');
    const result = document.getElementById('aiAskResult');
    const answer = document.getElementById('aiAskAnswer');
    const sources = document.getElementById('aiAskSources');
    const errorDiv = document.getElementById('aiAskError');

    if (!btn) return;

    function ask() {
        const q = input.value.trim();
        if (!q) return;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> Thinking…';
        result.style.display = 'none';
        errorDiv.style.display = 'none';

        fetch('/api/ai/ask', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({question: q})
        })
        .then(r => r.json())
        .then(data => {
            if (data.error) {
                errorDiv.textContent = data.error;
                errorDiv.style.display = 'block';
            } else {
                answer.textContent = data.answer;
                if (data.sources && data.sources.length) {
                    sources.innerHTML = data.sources.map(s =>
                        `<span class="badge bg-secondary me-1">${s}</span>`
                    ).join('');
                }
                result.style.display = 'block';
            }
        })
        .catch(() => {
            errorDiv.textContent = 'Request failed. Check your AI API key in Settings.';
            errorDiv.style.display = 'block';
        })
        .finally(() => {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-send"></i> Ask AI';
        });
    }

    btn.addEventListener('click', ask);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') ask(); });
})();

  // ── Live status polling ───────────────────────────────────────────────────
  (function() {
    const fields = {
      online:       '[data-live="online"]',
      offline:      '[data-live="offline"]',
      open_tickets: '[data-live="open_tickets"]',
      open_alerts:  '[data-live="open_alerts"]',
      crit_cves:    '[data-live="crit_cves"]',
    };
    function poll() {
      fetch('/api/dashboard/live-status')
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (!d) return;
          for (const [key, sel] of Object.entries(fields)) {
            const el = document.querySelector(sel);
            if (el && d[key] !== undefined) el.textContent = d[key];
          }
        })
        .catch(() => {});
    }
    poll();
    setInterval(poll, 15000);
  })();
