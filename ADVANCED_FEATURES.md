# Advanced Features - Asset Tracker

## 6. Advanced Search & Filters ✅

### Features Implemented:

#### 1. **Collapsible Search Panel**
- Expandable/collapsible filter interface
- Clean UI with toggle button
- Always visible by default

#### 2. **Multi-Select Categories**
- Select multiple categories at once
- Hold Ctrl/Cmd to select multiple options
- Filter assets from multiple categories simultaneously

#### 3. **Date Range Filters**
- Purchase Date From: Filter assets purchased after a specific date
- Purchase Date To: Filter assets purchased before a specific date
- Useful for finding assets from specific time periods

#### 4. **Warranty Status Filter**
Options:
- **Active**: Warranty still valid
- **Expiring Soon**: Warranty expires within 30 days
- **Expired**: Warranty has expired
- **No Warranty**: Assets without warranty information

#### 5. **Lifecycle Status Filter**
Filter by asset lifecycle stage:
- New (< 1 year)
- Active (1-3 years or 1-4 years depending on expected life)
- Aging (approaching expected life)
- Replace Soon (within 6 months of expected life)
- End of Life (past expected life)

#### 6. **Saved Search Filters**
- Click "Save Search" to save current filter combination
- Give it a custom name
- Access saved filters via "Saved Filters" button
- Apply saved filters with one click
- Delete saved filters you no longer need
- Stored locally in browser (localStorage)

#### 7. **Combined Filters**
- All filters work together
- Example: Find all Laptops + Desktops that are In Use with Active lifecycle
- Active filters displayed as removable badges
- "Clear All" button to reset all filters

### Usage:
1. Navigate to Assets page: http://10.15.0.53/assets
2. Expand "Advanced Search & Filters" section
3. Select your desired filters
4. Click "Apply Filters"
5. Optionally save your filter combination for future use

---

## 7. Bulk Operations ✅

### Features Implemented:

#### 1. **Asset Selection**
- Checkboxes appear for Admin and Manager roles
- "Select All" checkbox in table header
- Individual asset selection
- Selected count displayed in bulk toolbar

#### 2. **Bulk Status Update**
- Select multiple assets
- Click "Update Status"
- Choose new status from modal
- Updates all selected assets at once
- Logs each change in asset history

Available statuses:
- Available
- In Use
- In Repair
- Retired

#### 3. **Bulk Department Assignment**
- Select multiple assets
- Click "Assign Department"
- Enter department name
- Assigns all selected assets to that department
- Logs each assignment in asset history

#### 4. **Bulk Export Selected**
- Select specific assets to export
- Click "Export Selected"
- Downloads CSV with only selected assets
- Includes all asset details
- Filename includes timestamp

#### 5. **Bulk Operations Toolbar**
- Appears automatically when assets are selected
- Shows count of selected assets
- Provides quick access to bulk actions
- "Clear Selection" button to deselect all

#### 6. **History Logging**
- All bulk operations are logged
- Each asset gets individual history entry
- Marked as "(Bulk update)" or "(Bulk assignment)"
- User who performed action is recorded
- Timestamp automatically added

### Usage:
1. Login as Admin or Manager
2. Navigate to Assets page
3. Check the boxes next to assets you want to modify
4. Bulk toolbar appears automatically
5. Choose your bulk action
6. Confirm in the modal dialog
7. Changes are applied to all selected assets

### Permissions:
- **Admin & Manager**: Full bulk operations access
- **Viewer**: Read-only, no checkboxes visible

---

## Backend Routes

### Filter Routes:
- `GET /assets?search=...` - Text search
- `GET /assets?categories=...&categories=...` - Multi-category
- `GET /assets?status=...` - Status filter
- `GET /assets?lifecycle=...` - Lifecycle filter
- `GET /assets?warranty_status=...` - Warranty filter
- `GET /assets?purchase_from=...&purchase_to=...` - Date range
- All parameters can be combined

### Bulk Operation Routes:
- `POST /assets/bulk/status` - Update status for multiple assets
- `POST /assets/bulk/department` - Assign department to multiple assets
- `POST /assets/bulk/export` - Export selected assets to CSV

### Request Format (JSON):
```json
{
  "asset_ids": [1, 2, 3, 4],
  "status": "In Repair"  // or "department": "IT"
}
```

### Response Format:
```json
{
  "success": true,
  "count": 4,
  "message": "Successfully updated 4 assets"
}
```

---

## JavaScript Functions

### Bulk Operations:
- `toggleSelectAll(checkbox)` - Select/deselect all assets
- `updateBulkToolbar()` - Show/hide bulk toolbar based on selection
- `getSelectedAssetIds()` - Get array of selected asset IDs
- `clearSelection()` - Deselect all assets
- `bulkUpdateStatus()` - Show status update modal
- `submitBulkStatus()` - Execute bulk status update
- `bulkAssignDepartment()` - Show department modal
- `submitBulkDepartment()` - Execute bulk department assignment
- `bulkExportSelected()` - Export selected assets

### Saved Filters:
- `saveCurrentFilters()` - Save current filter state
- `loadSavedFilters()` - Load saved filters from localStorage
- `applyFilter(filterId)` - Apply a saved filter
- `deleteFilter(filterId)` - Delete a saved filter

---

## Technical Details

### Database Changes:
- No schema changes required
- Uses existing Asset and AssetHistory models
- Warranty and lifecycle filtering done in Python

### Dependencies:
- Bootstrap 5.3.0 (modals, tooltips, cards)
- Bootstrap Icons (filter, download, etc.)
- Vanilla JavaScript (no jQuery required)
- localStorage API for saved filters

### Browser Compatibility:
- Chrome/Edge: ✅ Full support
- Firefox: ✅ Full support
- Safari: ✅ Full support (localStorage supported)

---

## Testing

All features tested and verified:
- ✅ Multi-category filtering
- ✅ Date range filtering
- ✅ Warranty status filtering
- ✅ Lifecycle filtering
- ✅ Combined filters
- ✅ Bulk status updates
- ✅ Bulk department assignments
- ✅ Bulk CSV export
- ✅ History logging
- ✅ Saved filters (save/load/delete)

Access the application at: **http://10.15.0.53/assets**

Test credentials:
- Admin: `admin / admin123`
- Manager: `manager1 / manager123`
- Viewer: `viewer1 / viewer123`

---

## Future Enhancements (Optional)

Potential improvements:
- Save filters to database (instead of localStorage)
- Share saved filters between users
- Scheduled bulk operations
- Bulk import with CSV
- Export with custom column selection
- Advanced date filters (last 30 days, last quarter, etc.)
- Bulk QR code generation
- Email notifications for bulk operations

---

**Last Updated**: December 16, 2025
**Version**: 1.0
**Status**: Production Ready ✅
