function viewSnapshotDetails(snapshotId) {
    const modal = new bootstrap.Modal(document.getElementById('snapshotModal'));
    modal.show();
    
    fetch(`/api/soc2/snapshot/${snapshotId}`)
        .then(response => response.json())
        .then(data => {
            const detailsDiv = document.getElementById('snapshotDetails');
            if (data.success) {
                const snapshot = data.snapshot;
                const evidenceData = JSON.parse(snapshot.evidence_data);
                
                detailsDiv.innerHTML = `
                    <h6>Snapshot Information</h6>
                    <table class="table table-sm">
                        <tr><td><strong>Date:</strong></td><td>${snapshot.snapshot_date}</td></tr>
                        <tr><td><strong>Type:</strong></td><td>${snapshot.evidence_type}</td></tr>
                        <tr><td><strong>Records:</strong></td><td>${snapshot.record_count}</td></tr>
                        <tr><td><strong>Status:</strong></td><td>${snapshot.status}</td></tr>
                        <tr><td><strong>Collected By:</strong></td><td>${snapshot.collected_by}</td></tr>
                    </table>
                    <h6 class="mt-3">Evidence Data</h6>
                    <pre class="bg-light p-3 rounded">${JSON.stringify(evidenceData, null, 2)}</pre>
                `;
            } else {
                detailsDiv.innerHTML = '<div class="alert alert-danger">Failed to load snapshot details</div>';
            }
        })
        .catch(error => {
            document.getElementById('snapshotDetails').innerHTML = 
                '<div class="alert alert-danger">Error loading details: ' + error + '</div>';
        });
}
