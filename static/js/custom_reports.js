/* custom_reports.html extracted JS */
/* Expects window.CR_CONFIG set by template */

let currentReportData = null;

// Report Templates
const reportTemplates = {
    assets_by_department: {
        name: "Assets by Department",
        fields: ["asset_tag", "name", "category", "status", "assigned_to", "purchase_cost"],
        groupBy: "department",
        sortBy: "asset_tag",
        filterCategory: "",
        filterStatus: "",
        filterLifecycle: ""
    },
    replacement_needed: {
        name: "Replacement Needed",
        fields: ["asset_tag", "name", "category", "purchase_date", "age_years", "lifecycle_status", "condition"],
        groupBy: "",
        sortBy: "purchase_date",
        filterCategory: "",
        filterStatus: "",
        filterLifecycle: "Replace Soon"
    },
    warranty_expiring: {
        name: "Warranty Expiring",
        fields: ["asset_tag", "name", "category", "manufacturer", "warranty_expiry", "purchase_cost"],
        groupBy: "category",
        sortBy: "warranty_expiry",
        filterCategory: "",
        filterStatus: "",
        filterLifecycle: ""
    },
    cost_analysis: {
        name: "Cost Analysis",
        fields: ["asset_tag", "name", "category", "department", "purchase_date", "purchase_cost", "status"],
        groupBy: "category",
        sortBy: "purchase_cost",
        filterCategory: "",
        filterStatus: "",
        filterLifecycle: ""
    },
    asset_utilization: {
        name: "Asset Utilization",
        fields: ["asset_tag", "name", "category", "status", "assigned_to", "department"],
        groupBy: "status",
        sortBy: "category",
        filterCategory: "",
        filterStatus: "",
        filterLifecycle: ""
    },
    complete_inventory: {
        name: "Complete Inventory",
        fields: ["asset_tag", "name", "category", "manufacturer", "model", "serial_number", "status", 
                 "purchase_date", "purchase_cost", "warranty_expiry", "assigned_to", "department", "location"],
        groupBy: "",
        sortBy: "asset_tag",
        filterCategory: "",
        filterStatus: "",
        filterLifecycle: ""
    }
};

// Load template
function loadTemplate(templateId) {
    const template = reportTemplates[templateId];
    if (!template) return;
    
    // Set report name
    document.getElementById('reportName').value = template.name;
    
    // Clear all checkboxes
    document.querySelectorAll('.field-checkbox').forEach(cb => cb.checked = false);
    
    // Check template fields
    template.fields.forEach(field => {
        const checkbox = document.getElementById('field_' + field.replace('_', ''));
        if (checkbox) checkbox.checked = true;
    });
    
    // Set filters
    document.getElementById('groupBy').value = template.groupBy;
    document.getElementById('sortBy').value = template.sortBy;
    document.getElementById('filterCategory').value = template.filterCategory;
    document.getElementById('filterStatus').value = template.filterStatus;
    document.getElementById('filterLifecycle').value = template.filterLifecycle;
    
    // Close modal
    bootstrap.Modal.getInstance(document.getElementById('templatesModal')).hide();
    
    // Generate report automatically
    setTimeout(() => generateReport(), 300);
}

// Generate report
function generateReport() {
    const fields = Array.from(document.querySelectorAll('.field-checkbox:checked')).map(cb => cb.value);
    
    if (fields.length === 0) {
        alert('Please select at least one field to include in the report');
        return;
    }
    
    const config = {
        fields: fields,
        filterCategory: document.getElementById('filterCategory').value,
        filterStatus: document.getElementById('filterStatus').value,
        filterLifecycle: document.getElementById('filterLifecycle').value,
        groupBy: document.getElementById('groupBy').value,
        sortBy: document.getElementById('sortBy').value
    };
    
    // Show loading
    document.getElementById('reportPreview').innerHTML = `
        <div class="text-center py-5">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Loading...</span>
            </div>
            <p class="mt-3">Generating report...</p>
        </div>
    `;
    
    // Fetch report data
    fetch('/reports/custom/generate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(config)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            currentReportData = data;
            displayReport(data);
        } else {
            alert('Error generating report: ' + data.message);
        }
    })
    .catch(error => {
        alert('Error: ' + error);
        document.getElementById('reportPreview').innerHTML = `
            <div class="alert alert-danger">Error generating report. Please try again.</div>
        `;
    });
}

// Display report
function displayReport(data) {
    const preview = document.getElementById('reportPreview');
    const groupBy = document.getElementById('groupBy').value;
    
    let html = `<div class="alert alert-info alert-dismissible fade show" role="alert">
        <i class="bi bi-info-circle"></i> <strong>Tip:</strong> Drag column headers to reorder them
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    </div>`;
    
    html += `<div class="table-responsive"><table class="table table-sm table-hover report-table" id="reportTable">`;
    
    // Table header with draggable columns
    html += '<thead><tr>';
    data.fields.forEach((field, index) => {
        html += `<th draggable="true" 
                     data-field="${field}" 
                     data-index="${index}"
                     ondragstart="handleDragStart(event)" 
                     ondragover="handleDragOver(event)" 
                     ondrop="handleDrop(event)" 
                     ondragend="handleDragEnd(event)"
                     style="cursor: move; user-select: none;">
                     <i class="bi bi-grip-vertical text-muted"></i> ${formatFieldName(field)}
                 </th>`;
    });
    html += '</tr></thead><tbody id="reportTableBody">';
    
    // Table body
    if (groupBy && data.grouped) {
        // Grouped display
        for (const [group, assets] of Object.entries(data.grouped)) {
            html += `<tr class="group-header"><td colspan="${data.fields.length}">${formatFieldName(groupBy)}: ${group || 'Not Set'} (${assets.length})</td></tr>`;
            assets.forEach(asset => {
                html += '<tr>';
                data.fields.forEach(field => {
                    html += `<td data-field="${field}">${formatValue(asset[field], field)}</td>`;
                });
                html += '</tr>';
            });
        }
    } else {
        // Regular display
        data.assets.forEach(asset => {
            html += '<tr>';
            data.fields.forEach(field => {
                html += `<td data-field="${field}">${formatValue(asset[field], field)}</td>`;
            });
            html += '</tr>';
        });
    }
    
    html += '</tbody></table></div>';
    preview.innerHTML = html;
    
    // Show actions
    document.getElementById('reportActions').style.display = 'block';
    
    // Build dynamic summary based on available fields
    const summaryRow = document.getElementById('summaryRow');
    let summaryHtml = '';
    
    // Always show total count
    summaryHtml += `
        <div class="col-md-3">
            <h4 class="text-primary">${data.summary.count}</h4>
            <small class="text-muted">Total Assets</small>
        </div>
    `;
    
    // Show total value only if purchase_cost field is included
    if (data.fields.includes('purchase_cost')) {
        summaryHtml += `
            <div class="col-md-3">
                <h4 class="text-success">$${data.summary.total_value.toFixed(2)}</h4>
                <small class="text-muted">Total Value</small>
            </div>
        `;
    }
    
    // Show avg age only if age_years field is included or purchase_date is available
    if (data.fields.includes('age_years') || data.fields.includes('purchase_date')) {
        if (data.summary.avg_age > 0) {
            summaryHtml += `
                <div class="col-md-3">
                    <h4 class="text-info">${data.summary.avg_age.toFixed(1)}</h4>
                    <small class="text-muted">Avg Age (Years)</small>
                </div>
            `;
        }
    }
    
    // Show groups if grouping is enabled
    if (groupBy) {
        const groupCount = Object.keys(data.grouped || {}).length;
        summaryHtml += `
            <div class="col-md-3">
                <h4 class="text-warning">${groupCount}</h4>
                <small class="text-muted">Groups</small>
            </div>
        `;
    }
    
    summaryRow.innerHTML = summaryHtml;
    document.getElementById('reportSummary').style.display = 'block';
}

// Format field names
function formatFieldName(field) {
    return field.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

// Format values
function formatValue(value, field) {
    if (value === null || value === undefined || value === '') return '-';
    if (field === 'purchase_cost') return '$' + parseFloat(value).toFixed(2);
    if (field.includes('date')) return value;
    if (field === 'age_years') return parseFloat(value).toFixed(1);
    return value;
}

// Export report
function exportReport(format) {
    if (!currentReportData) {
        alert('Please generate a report first');
        return;
    }
    
    const config = {
        fields: currentReportData.fields,
        filterCategory: document.getElementById('filterCategory').value,
        filterStatus: document.getElementById('filterStatus').value,
        filterLifecycle: document.getElementById('filterLifecycle').value,
        groupBy: document.getElementById('groupBy').value,
        sortBy: document.getElementById('sortBy').value,
        format: format
    };
    
    // Create form and submit
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = '/reports/custom/export';
    
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'config';
    input.value = JSON.stringify(config);
    
    form.appendChild(input);
    document.body.appendChild(form);
    form.submit();
    document.body.removeChild(form);
}

// Print report
function printReport() {
    window.print();
}

// Update report type fields visibility
function updateReportTypeFields() {
    const reportType = document.getElementById('reportType').value;
    const aggregationField = document.getElementById('aggregationField');
    const valueField = document.getElementById('valueField');
    const chartTypeField = document.getElementById('chartTypeField');
    const fieldsSection = document.querySelector('.field-checkbox').closest('.mb-3');
    const groupBySection = document.getElementById('groupBy').closest('.mb-3');
    
    // Hide all conditional fields
    aggregationField.style.display = 'none';
    valueField.style.display = 'none';
    chartTypeField.style.display = 'none';
    
    if (reportType === 'stat') {
        aggregationField.style.display = 'block';
        valueField.style.display = 'block';
        fieldsSection.style.display = 'none';
    } else if (reportType === 'chart') {
        chartTypeField.style.display = 'block';
        groupBySection.querySelector('select').required = true;
        fieldsSection.style.display = 'none';
    } else {
        // list type
        fieldsSection.style.display = 'block';
        groupBySection.querySelector('select').required = false;
    }
}

// Save report
function saveReport() {
    const name = document.getElementById('reportName').value;
    if (!name) {
        alert('Please enter a report name');
        return;
    }
    
    const reportType = document.getElementById('reportType').value;
    const fields = Array.from(document.querySelectorAll('.field-checkbox:checked')).map(cb => cb.value);
    
    const config = {
        fields: fields,
        filterCategory: document.getElementById('filterCategory').value,
        filterStatus: document.getElementById('filterStatus').value,
        filterLifecycle: document.getElementById('filterLifecycle').value,
        groupBy: document.getElementById('groupBy').value,
        sortBy: document.getElementById('sortBy').value,
        reportType: reportType,
        aggregationType: document.getElementById('aggregationType')?.value || 'count',
        statValueField: document.getElementById('statValueField')?.value || 'id',
        chartType: document.getElementById('chartType')?.value || 'pie'
    };
    
    const description = reportType === 'stat' ? 'Custom stat calculation' :
                       reportType === 'chart' ? 'Custom chart visualization' :
                       'Custom list/table report';
    
    // Save to database via API
    fetch('window.CR_CONFIG.saveUrl', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            name: name,
            description: description,
            report_type: reportType,
            config: config,
            is_public: false
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('Report saved successfully! View it on the Reports & Analytics page.');
            window.location.href = 'window.CR_CONFIG.reportsUrl';
        } else {
            alert('Error saving report: ' + data.message);
        }
    })
    .catch(error => {
        console.error('Error:', error);
        alert('Failed to save report');
    });
}

// Load saved reports
function loadSavedReports() {
    const savedReports = JSON.parse(localStorage.getItem('customReports') || '[]');
    const listDiv = document.getElementById('savedReportsList');
    
    if (savedReports.length === 0) {
        listDiv.innerHTML = '<p class="text-muted">No saved reports yet.</p>';
        return;
    }
    
    let html = '<div class="list-group">';
    savedReports.forEach(report => {
        const date = new Date(report.created).toLocaleDateString();
        html += `
            <div class="list-group-item">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <strong>${report.name}</strong>
                        <br><small class="text-muted">${report.fields.length} fields • ${date}</small>
                    </div>
                    <div>
                        <button class="btn btn-sm btn-outline-primary" onclick="applySavedReport(${report.id})">
                            <i class="bi bi-play"></i> Run
                        </button>
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteSavedReport(${report.id})">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;
    });
    html += '</div>';
    
    listDiv.innerHTML = html;
}

// Apply saved report
function applySavedReport(reportId) {
    const savedReports = JSON.parse(localStorage.getItem('customReports') || '[]');
    const report = savedReports.find(r => r.id === reportId);
    
    if (!report) return;
    
    // Set form values
    document.getElementById('reportName').value = report.name;
    
    document.querySelectorAll('.field-checkbox').forEach(cb => cb.checked = false);
    report.fields.forEach(field => {
        const checkbox = document.getElementById('field_' + field.replace('_', ''));
        if (checkbox) checkbox.checked = true;
    });
    
    document.getElementById('filterCategory').value = report.filterCategory;
    document.getElementById('filterStatus').value = report.filterStatus;
    document.getElementById('filterLifecycle').value = report.filterLifecycle;
    document.getElementById('groupBy').value = report.groupBy;
    document.getElementById('sortBy').value = report.sortBy;
    
    // Close modal
    bootstrap.Modal.getInstance(document.getElementById('savedReportsModal')).hide();
    
    // Generate
    setTimeout(() => generateReport(), 300);
}

// Delete saved report
function deleteSavedReport(reportId) {
    if (!confirm('Delete this saved report?')) return;
    
    let savedReports = JSON.parse(localStorage.getItem('customReports') || '[]');
    savedReports = savedReports.filter(r => r.id !== reportId);
    localStorage.setItem('customReports', JSON.stringify(savedReports));
    
    loadSavedReports();
}

// Clear form
function clearForm() {
    document.getElementById('reportBuilderForm').reset();
    document.querySelectorAll('.field-checkbox').forEach(cb => cb.checked = false);
    document.getElementById('field_asset_tag').checked = true;
    document.getElementById('field_name').checked = true;
    document.getElementById('field_category').checked = true;
    document.getElementById('field_status').checked = true;
    
    document.getElementById('reportPreview').innerHTML = `
        <div class="text-center text-muted py-5">
            <i class="bi bi-bar-chart-line" style="font-size: 3rem;"></i>
            <p class="mt-3">Configure your report and click "Generate Report" to preview</p>
        </div>
    `;
    document.getElementById('reportActions').style.display = 'none';
    document.getElementById('reportSummary').style.display = 'none';
}

// ==================== DRAG AND DROP COLUMN REORDERING ====================

let draggedColumn = null;
let draggedIndex = -1;

function handleDragStart(e) {
    draggedColumn = e.target;
    draggedIndex = parseInt(e.target.dataset.index);
    e.target.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/html', e.target.innerHTML);
}

function handleDragOver(e) {
    if (e.preventDefault) {
        e.preventDefault();
    }
    e.dataTransfer.dropEffect = 'move';
    
    // Add visual indicator
    const targetTh = e.target.closest('th');
    if (targetTh && targetTh !== draggedColumn) {
        // Remove previous indicators
        document.querySelectorAll('th.drag-over').forEach(th => th.classList.remove('drag-over'));
        targetTh.classList.add('drag-over');
    }
    
    return false;
}

function handleDrop(e) {
    if (e.stopPropagation) {
        e.stopPropagation();
    }
    
    e.preventDefault();
    
    const targetColumn = e.target.closest('th');
    if (!targetColumn || targetColumn === draggedColumn) return false;
    
    const targetIndex = parseInt(targetColumn.dataset.index);
    
    // Reorder the fields in currentReportData
    if (currentReportData && currentReportData.fields) {
        const fields = [...currentReportData.fields];
        const [movedField] = fields.splice(draggedIndex, 1);
        fields.splice(targetIndex, 0, movedField);
        
        // Update the data
        currentReportData.fields = fields;
        
        // Re-render the table with reordered columns
        displayReport(currentReportData);
        
        // Show success message
        const alert = document.createElement('div');
        alert.className = 'alert alert-success alert-dismissible fade show position-fixed top-0 start-50 translate-middle-x mt-3';
        alert.style.zIndex = '9999';
        alert.innerHTML = `
            <i class="bi bi-check-circle"></i> Column order updated! Export will use this order.
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        document.body.appendChild(alert);
        
        // Auto-dismiss after 3 seconds
        setTimeout(() => {
            alert.remove();
        }, 3000);
    }
    
    return false;
}

function handleDragEnd(e) {
    e.target.classList.remove('dragging');
    
    // Remove all drag indicators
    document.querySelectorAll('th.drag-over').forEach(th => {
        th.classList.remove('drag-over');
    });
}

// Initialize
document.addEventListener('DOMContentLoaded', function() {
    loadSavedReports();
});

// Print styles
window.addEventListener('beforeprint', function() {
    document.querySelectorAll('.card-header, .btn, .modal').forEach(el => el.style.display = 'none');
});

window.addEventListener('afterprint', function() {
    document.querySelectorAll('.card-header, .btn').forEach(el => el.style.display = '');
});
