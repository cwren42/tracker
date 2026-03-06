# Dashboard Customization Feature

## Overview
The IT Asset Tracker dashboard is now fully customizable, allowing each user to personalize their dashboard layout with drag-and-drop widgets.

## Features

### 1. **Customizable Widget Layout**
   - Users can add, remove, and rearrange widgets on their dashboard
   - Each user has their own unique dashboard configuration
   - Changes are saved to the database and persist across sessions

### 2. **Available Widget Types**

#### Statistics Widgets (Small tiles showing key metrics)
- **Total Assets** - Total number of assets in the system
- **Available Assets** - Assets currently available
- **In Use** - Assets currently in use
- **In Repair** - Assets under repair
- **Average Age** - Average age of all assets
- **Need Replacement** - Assets that need replacement
- **Total Licenses** - Total software licenses
- **Active Licenses** - Currently active licenses
- **Annual License Cost** - Total annual licensing costs
- **Total Employees** - Number of employees in the system

#### Table Widgets (Data tables)
- **Recent Assets** - Shows the 5 most recently added assets

#### List Widgets (Data lists with detailed information)
- **Recent Assets** - Shows the 5 most recently added assets
- **Warranty Expiring Soon** - Assets with warranties expiring within 30 days
- **Replacement Needed** - Assets that need replacement based on age
- **Licenses Expiring** - Software licenses expiring within 30 days
- **Recent Employees** - Most recently added employees

#### Chart Widgets (Visual analytics charts)
- **Assets by Category** - Doughnut chart showing asset distribution by category
- **Assets by Status** - Pie chart showing asset status breakdown
- **Assets by Department** - Horizontal bar chart of assets per department
- **Lifecycle Status** - Doughnut chart showing asset lifecycle stages
- **Licenses by Vendor** - Pie chart of licenses grouped by vendor

### 3. **How to Customize Your Dashboard**

#### Entering Edit Mode
1. Click the **"Customize Dashboard"** button in the top-right corner
2. The dashboard enters edit mode with additional controls

#### Adding Widgets
1. Click the **"+ Add Widget"** card
2. A modal will appear showing all available widgets
3. Click on any widget to add it to your dashboard
4. The widget will appear at the end of your dashboard

#### Rearranging Widgets
1. While in edit mode, hover over any widget
2. Click and drag the widget to a new position
3. Other widgets will automatically adjust to make space

#### Removing Widgets
1. While in edit mode, each widget has an **X** button in the top-right corner
2. Click the **X** button on any widget you want to remove
3. Confirm the removal when prompted

#### Saving Your Layout
1. After making all your changes, click **"Save Layout"**
2. Your configuration is saved to the database
3. You'll be redirected to the dashboard with your new layout

#### Canceling Changes
1. Click **"Cancel"** to exit edit mode without saving
2. Your dashboard will return to its previous configuration

### 4. **Resetting to Default**
To reset your dashboard to the default layout:
1. Use the `/dashboard/reset` endpoint (requires POST request)
2. This will delete your custom configuration and restore defaults

## Technical Details

### Database Schema
A new `DashboardWidget` table stores user-specific widget configurations:
- `user_id` - Links to the user
- `widget_type` - Type of widget (stat, chart, table)
- `title` - Widget title
- `config` - JSON configuration for the widget
- `position` - Order/position in the layout
- `size` - Bootstrap grid class (e.g., col-md-3)
- `enabled` - Whether the widget is active

### New Routes
- `GET /` - Dashboard with customizable widgets
- `GET /dashboard/configure` - Configure dashboard (returns available widgets)
- `POST /dashboard/configure` - Save dashboard configuration
- `POST /dashboard/reset` - Reset to default layout

### Widget System
Widgets are rendered using partial templates in `/templates/widgets/`:
- `stat_widget.html` - Renders statistics cards
- `table_widget.html` - Renders data tables
- `chart_widget.html` - Renders charts (using Chart.js)

### Dependencies
- **SortableJS** (v1.15.0) - Provides drag-and-drop functionality
- **Chart.js** (v4.4.0) - Renders interactive charts
- **Bootstrap 5.3** - Grid system and styling

## Default Dashboard Layout
If a user hasn't customized their dashboard, they see:
1. Total Assets
2. Available Assets
3. In Use Assets
4. Assets In Repair
5. Average Asset Age
6. Assets Needing Replacement

## Future Enhancements
Potential features for future development:
- Custom report widgets (pull data from saved reports)
- Widget size customization (small, medium, large)
- Export/import dashboard configurations
- Share dashboard layouts between users
- More widget types (alerts, notifications, tasks)
- Custom SQL query widgets
- Real-time data updates without page refresh

## Browser Compatibility
- Modern browsers with JavaScript enabled
- Drag-and-drop requires:
  - Chrome 85+
  - Firefox 80+
  - Safari 14+
  - Edge 85+

## Performance Considerations
- Widget data is calculated once per page load
- Database queries are optimized with proper indexes
- Heavy charts only load when the widget is present
- Sortable.js is only loaded in edit mode

## Security
- Dashboard configurations are user-specific
- Users can only modify their own dashboards
- All routes require authentication (@login_required)
- Widget configurations are validated server-side
