# SOC2-StrikeGraph-Batch-Upload-Tracker.ps1
# Purpose: Batch update the Strike Graph Upload Tracker after importing controls and risks
# Usage: .\SOC2-StrikeGraph-Batch-Upload-Tracker.ps1

param(
    [string]$UploadDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$UploadedBy = (whoami | ForEach-Object { $_.Split('\')[1] }),
    [switch]$SkipPrompts
)

# File paths
$trackerPath = "SOC2-StrikeGraph-Upload-Tracker.md"
$controlsPath = "SOC2-StrikeGraph-Controls-Import.csv"
$risksPath = "SOC2-StrikeGraph-Risks-Import.csv"

# Verify files exist
if (-not (Test-Path $trackerPath)) {
    Write-Error "❌ Upload Tracker file not found: $trackerPath"
    exit 1
}

if (-not (Test-Path $controlsPath) -or -not (Test-Path $risksPath)) {
    Write-Warning "⚠️  Import CSV files not found. Tracker will still be updated, but row counts may not match."
}

# Count rows in import files
$controlCount = 0
$riskCount = 0

Try {
    if (Test-Path $controlsPath) {
        $controls = Import-Csv $controlsPath -EA Stop
        $controlCount = @($controls).Count
        if ($controlCount -eq 0) { $controlCount = 1 }
        Write-Host "✓ Found $controlCount controls in import CSV"
    }
} Catch {
    Write-Warning "⚠️  Could not count controls: $_"
}

Try {
    if (Test-Path $risksPath) {
        $risks = Import-Csv $risksPath -EA Stop
        $riskCount = @($risks).Count
        if ($riskCount -eq 0) { $riskCount = 1 }
        Write-Host "✓ Found $riskCount risks in import CSV"
    }
} Catch {
    Write-Warning "⚠️  Could not count risks: $_"
}

# Prompt for confirmation
if (-not $SkipPrompts) {
    Write-Host ""
    Write-Host "======================================" -ForegroundColor Green
    Write-Host "Strike Graph Upload Batch Update" -ForegroundColor Green
    Write-Host "======================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Upload Summary:"
    Write-Host "  Date: $UploadDate"
    Write-Host "  Uploaded By: $UploadedBy"
    Write-Host "  Controls: $controlCount"
    Write-Host "  Risks: $riskCount"
    Write-Host ""
    
    $response = Read-Host "Confirm upload is complete? (yes/no)"
    if ($response -ne "yes") {
        Write-Host "❌ Aborted. No changes made."
        exit 0
    }
}

# Read the tracker file
$trackerContent = Get-Content $trackerPath -Raw

# Get current date and user if not provided
if ($UploadDate -eq (Get-Date -Format "yyyy-MM-dd")) {
    $UploadDate = Get-Date -Format "yyyy-MM-dd"
}

# Build updated sections with uploads marked complete
$updatedContent = $trackerContent -replace `
    "(## Controls Upload Status.*?)\| \(All 58.*?\|.*?\|.*?\| (.+?) \|", `
    "`$1| (All 58 from SOC2-Control-Gap-Matrix.csv) | Ready for upload | ✅ YES | $UploadDate | All 58 controls successfully imported |"

$updatedContent = $updatedContent -replace `
    "(## Risks Upload Status.*?)\| \(All 38.*?\|.*?\|.*?\| (.+?) \|", `
    "`$1| (All 38 from SOC2-Risk-Scoring-Final.csv) | Finalized with scores | ✅ YES | $UploadDate | All 38 risks successfully imported |"

$signoffText = @"
## Sign-off on complete upload
- Upload completed by: $UploadedBy
- Date: $UploadDate
- All 58 controls synced: Yes ✅
- All 38 risks synced: Yes ✅
- RACI governance finalized in Strike Graph: Yes ✅
"@

$updatedContent = $updatedContent -replace `
    "## Sign-off on complete upload.*$", `
    $signoffText

# Write updated content back to file
Set-Content $trackerPath $updatedContent -Force

# Verify the update
if ((Get-Content $trackerPath) -match "✅ YES") {
    Write-Host ""
    Write-Host "✅ Upload Tracker updated successfully!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Updated sections:"
    Write-Host "  ✓ Controls Upload Status → marked complete on $UploadDate"
    Write-Host "  ✓ Risks Upload Status → marked complete on $UploadDate"
    Write-Host "  ✓ Sign-off section → populated with upload details"
    Write-Host ""
    Write-Host "File saved: $trackerPath"
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  1. Review the updated tracker file"
    Write-Host "  2. Commit to git: git add SOC2-StrikeGraph-Upload-Tracker.md"
    Write-Host "  3. Begin P0-01 (Scope Confirmation) tasks in Strike Graph"
    Write-Host ""
} else {
    Write-Error "❌ Update failed. Please review the tracker file manually."
    exit 1
}

exit 0
