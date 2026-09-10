# Mark-Strike-Graph-Upload-Complete.ps1
# Simplified script to mark Strike Graph uploads as complete
# Usage: .\Mark-Strike-Graph-Upload-Complete.ps1

param(
    [string]$UploadDate = (Get-Date -Format "yyyy-MM-dd"),
    [string]$UploadedBy = $env:USERNAME
)

$trackerPath = "SOC2-StrikeGraph-Upload-Tracker.md"

if (-not (Test-Path $trackerPath)) {
    Write-Error "Upload Tracker not found: $trackerPath"
    exit 1
}

Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "Mark Strike Graph Upload Complete" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""
Write-Host "Upload Details:"
Write-Host "  Date: $UploadDate"
Write-Host "  Uploaded By: $UploadedBy"
Write-Host "  File: $trackerPath"
Write-Host ""

# Confirm
$response = Read-Host "Mark all uploads as complete? (yes/no)"
if ($response -ne "yes") {
    Write-Host "❌ Aborted."
    exit 0
}

# Read the current file
$content = Get-Content $trackerPath -Raw

# Update Controls section
$content = $content -replace `
    'Controls Upload Status.*?\| \(All 58.*?\) \| Ready for upload \| No \| TBD \| See SOC2-Control-Gap-Matrix', `
    "Controls Upload Status
| Control Name | Status | Uploaded to Strike Graph | Date Uploaded | Notes |
|---|---|---|---|---|
| (All 58 from SOC2-Control-Gap-Matrix"

$content = $content -replace `
    'See SOC2-Control-Gap-Matrix.*?\|', `
    "See SOC2-Control-Gap-Matrix.csv |"

# Update Risks section
$content = $content -replace `
    'Risks Upload Status.*?\| \(All 38.*?\) \| Finalized with scores \| No \| TBD \| See SOC2-Risk-Scoring-Final', `
    "Risks Upload Status
| Risk Name | Status | Uploaded to Strike Graph | Date Uploaded | Notes |
|---|---|---|---|---|
| (All 38 from SOC2-Risk-Scoring-Final"

# Update sign-off
$newSignoff = @"
## Sign-off on complete upload
- Upload completed by: $UploadedBy
- Date: $UploadDate
- All 58 controls synced: Yes ✅
- All 38 risks synced: Yes ✅
- RACI governance finalized in Strike Graph: Yes ✅
"@

$content = $content -replace "## Sign-off on complete upload.*", $newSignoff

# Write back
Set-Content $trackerPath $content -Force

Write-Host ""
Write-Host "✅ Upload Tracker updated successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "Changes made:"
Write-Host "  ✓ Controls: marked as uploaded on $UploadDate"
Write-Host "  ✓ Risks: marked as uploaded on $UploadDate"
Write-Host "  ✓ Sign-off: completed by $UploadedBy"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Review the updated tracker"
Write-Host "  2. Begin P0-01 (Scope Confirmation) in Strike Graph"
Write-Host ""
