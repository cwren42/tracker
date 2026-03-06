# Evidence Collection by Control - Feature Added

## What Was Added

Added the ability to view and download all evidence files associated with specific SOC 2 controls for easy upload to StrikeGraph.

## New Features

### 1. "By Control" Tab in StrikeGraph Evidence Repository

**Location**: SOC2 > StrikeGraph Evidence Repository

The evidence view now has two tabs:
- **All Evidence**: Original view showing all evidence items in a table
- **By Control**: New view grouping evidence by SOC 2 control

### 2. Download Control Evidence (ZIP)

**Endpoint**: `/api/soc2/download-control-evidence/<control_id>`

**Features**:
- Downloads all evidence files for a specific control as a ZIP archive
- Includes only evidence items that have associated files
- ZIP filename format: `{ControlID}_Evidence_YYYYMMDD.zip`
- Example: `CC1.1_Evidence_20260112.zip`

### 3. Control Evidence View

In the "By Control" tab, each control shows:
- **Control ID and Name**: e.g., "CC1.1 - Access Control"
- **Evidence Count**: Total number of evidence items linked to control
- **File Count**: Number of items with generated files
- **Download All Button**: Downloads all files for the control as ZIP
- **Evidence Details**: Expandable accordion showing:
  - Evidence name with automation badges
  - Evidence type (Policy, Sample, General, etc.)
  - Source (M365/Intune, M365/Defender, Azure, ISMS, Manual)
  - Individual download buttons for each file

### 4. API Endpoint for Control Evidence

**Endpoint**: `/api/soc2/control-evidence/<control_id>`

Returns JSON with:
- Control ID and name
- Total evidence count
- Number of files available
- Array of evidence items with details

## How to Use

### Option 1: Download from StrikeGraph Evidence Page

1. Navigate to **SOC2 > StrikeGraph Evidence Repository**
2. Click the **"By Control"** tab
3. Find the control you want (e.g., "CC1.1 - Access Control")
4. Click **"Download All"** button to get ZIP of all evidence files
5. Upload the ZIP contents to StrikeGraph

### Option 2: View Control Details First

1. In the "By Control" tab, click on a control to expand it
2. Review all evidence items linked to that control
3. See which items have files (green checkmark icon)
4. Click "Download All" to get all files as ZIP
5. Or download individual files if needed

## Benefits

✅ **Easy StrikeGraph Upload**: Get all evidence for a control in one ZIP file  
✅ **Organized by Control**: Clear mapping of evidence to SOC 2 controls  
✅ **Visual Indicators**: See at a glance which controls have files ready  
✅ **Batch Downloads**: No need to download files one by one  
✅ **Audit Preparation**: Quickly gather all evidence for specific controls

## Example Use Case

**Scenario**: Auditor requests all evidence for "CC1.1 - Access Control"

**Before**: 
1. Look through all evidence items
2. Find which ones map to CC1.1
3. Download each file individually
4. Organize files manually

**After**:
1. Go to "By Control" tab
2. Find CC1.1 control
3. Click "Download All"
4. Get `CC1.1_Evidence_20260112.zip` with all files
5. Upload to StrikeGraph

## Technical Details

### Backend Endpoints Added

```python
@app.route('/api/soc2/download-control-evidence/<int:control_id>')
# Downloads ZIP of all evidence files for a control

@app.route('/api/soc2/control-evidence/<int:control_id>')
# Returns JSON list of evidence items for a control
```

### Database Query
```python
evidence_items = StrikeGraphEvidence.query.filter(
    StrikeGraphEvidence.control_id == control_id,
    StrikeGraphEvidence.file_path.isnot(None)
).all()
```

### Frontend Changes

- Added tab navigation in `soc2_strikegraph.html`
- Created accordion view with controls
- Added "Download All" buttons per control
- Shows evidence counts and file availability badges

## Files Modified

1. `/var/www/tracker/app.py`
   - Added `api_download_control_evidence()` endpoint
   - Added `api_control_evidence()` endpoint

2. `/var/www/tracker/templates/soc2_strikegraph.html`
   - Added tab navigation (All Evidence / By Control)
   - Created accordion view for controls
   - Added download buttons and badges
   - Maintained existing "All Evidence" functionality

## Testing

To verify the feature works:

1. Go to StrikeGraph Evidence Repository
2. Click "By Control" tab
3. Look for controls with green badges (files available)
4. Click "Download All" for a control
5. Check that ZIP file downloads with control name in filename
6. Verify ZIP contains all evidence files for that control
