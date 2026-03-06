# Asset Lifecycle Tracking Feature

## Overview
The IT Asset Tracker now includes comprehensive lifecycle tracking to help you manage asset replacement planning, especially for equipment with typical 3-5 year lifespans like laptops and desktops.

## Features Added

### 1. Asset Lifecycle Fields
- **Expected Life Years**: Set the expected useful life (3-7 years, default 3 for laptops/PCs)
- **Replacement Date**: Auto-calculated based on purchase date + expected life
- **Condition**: Track current physical condition (Excellent, Good, Fair, Poor)

### 2. Lifecycle Status Tracking
Assets are automatically categorized into lifecycle stages:
- **New** (0-25% of expected life): Recently purchased, like-new condition
- **Active** (25-60% of expected life): Prime operating years
- **Aging** (60-80% of expected life): Getting older, monitor closely
- **Replace Soon** (80-100% of expected life): Within replacement window
- **End of Life** (>100% of expected life): Past expected replacement date

### 3. Dashboard Alerts
- Warning banner when assets need replacement within 6 months
- Quick link to view affected assets
- Complements existing warranty expiry alerts

### 4. Asset Views
- **List View**: Lifecycle status badge for each asset
- **Detail View**: Complete lifecycle information section showing:
  - Current condition
  - Asset age in years
  - Lifecycle status
  - Expected life years
  - Replacement date with warnings

### 5. Forms
- **Add Asset**: Set expected life and condition during creation
- **Edit Asset**: Update lifecycle fields including manual replacement date override
- Helpful hints for typical lifespans by category

## Typical Asset Lifespans
- **Laptops/Desktops**: 3-5 years
- **Monitors**: 5-7 years
- **Servers**: 5-7 years
- **Printers**: 5 years
- **Network Equipment**: 5-7 years

## Database Changes
New columns added to `asset` table:
- `expected_life_years` (INTEGER, default 3)
- `replacement_date` (DATE)
- `condition` (VARCHAR(20), default 'Good')

## Sample Data
All 10 existing assets have been updated with lifecycle data:
- 2 assets need replacement within 6 months (LAP001, DSK001)
- Ages range from 1.9 to 3.9 years
- Various lifecycle statuses: Active, Aging, Replace Soon

## Usage

### Adding New Assets
1. Go to Assets → Add Asset
2. Fill in standard fields (asset tag, name, category, etc.)
3. Set **Expected Life (years)** dropdown (defaults to 3 years)
4. Set initial **Condition** (defaults to Good)
5. If purchase date is provided, replacement date is auto-calculated

### Monitoring Replacements
1. Check dashboard for replacement alerts (red banner)
2. View assets list to see lifecycle status badges
3. Click on assets with "Replace Soon" or "End of Life" status
4. Plan budget and procurement accordingly

### Updating Lifecycle Info
1. Edit asset
2. Update condition as asset ages
3. Adjust expected life years if needed
4. Override replacement date if procurement plans change

## Replacement Planning
The system helps with:
- **Proactive Budgeting**: Know 6 months in advance which assets need replacement
- **Inventory Management**: Track which assets are aging vs. actively used
- **Condition Monitoring**: Document physical deterioration over time
- **Compliance**: Meet organizational asset refresh policies

## Access Control
- **Viewers**: Can see lifecycle status and information
- **Managers**: Can update condition and lifecycle fields
- **Admins**: Full control over lifecycle tracking

## Benefits
1. **Prevent Downtime**: Replace assets before they fail
2. **Budget Planning**: Forecast replacement costs accurately
3. **Performance**: Ensure employees have modern equipment
4. **Compliance**: Meet IT refresh cycle policies
5. **Total Cost of Ownership**: Track full asset lifecycle costs

## Next Steps
Consider adding:
- Email notifications for assets needing replacement
- Bulk lifecycle updates via CSV import
- Depreciation calculations
- Lifecycle cost reports
- Replacement approval workflow
