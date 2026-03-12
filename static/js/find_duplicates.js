function mergeDuplicates(groupName, assetIds) {
    const keepRadio = document.querySelector(`input[name="keep-${groupName}"]:checked`);
    if (!keepRadio) {
        alert('Please select which asset to keep');
        return;
    }
    
    const keepId = parseInt(keepRadio.value);
    const deleteIds = assetIds.filter(id => id !== keepId);
    
    if (!confirm(`Keep asset #${keepId} and delete ${deleteIds.length} duplicate(s)?`)) {
        return;
    }
    
    fetch('window.FINDDUPLICATESCFG.merge_url', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            keep_id: keepId,
            delete_ids: deleteIds
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert(data.message);
            window.location.reload();
        } else {
            alert('Error: ' + data.message);
        }
    })
    .catch(error => {
        alert('Error: ' + error);
    });
}
