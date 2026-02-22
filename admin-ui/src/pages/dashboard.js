import { api, fetchWithAuth } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, downloadBlob } from '../utils.js';
import { registerPageLoader, setRefreshTimer } from '../router.js';
import { hasPermission } from '../auth.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

const _BASE_INTERVAL = 30_000;  // 30s normal refresh
const _MAX_INTERVAL = 300_000;  // 5min max backoff
let _currentInterval = _BASE_INTERVAL;
let _selectedTenantId = '';
let _tenants = [];

async function _loadTenants() {
    if (!hasPermission('tenants:read')) { _tenants = []; return; }
    try {
        const data = await api('/admin/tenants?is_active=true&limit=100');
        _tenants = data.tenants || [];
    } catch {
        _tenants = [];
    }
}

function _renderTenantFilter() {
    const container = document.getElementById('dashboardTenantFilter');
    if (!container) return;
    let html = `<label class="text-sm font-medium text-neutral-600 dark:text-neutral-400 mr-2">${t('dashboard.filterByNetwork')}</label>`;
    html += `<select id="dashboardTenantSelect" class="${tw.selectSm} w-auto" onchange="window._pages.dashboard.onTenantChange(this.value)">`;
    html += `<option value="">${t('dashboard.allNetworks')}</option>`;
    for (const ten of _tenants) {
        const sel = ten.id === _selectedTenantId ? ' selected' : '';
        html += `<option value="${escapeHtml(ten.id)}"${sel}>${escapeHtml(ten.name)}</option>`;
    }
    html += '</select>';
    container.innerHTML = html;
}

function onTenantChange(tenantId) {
    _selectedTenantId = tenantId;
    loadDashboard();
}

async function loadDashboard() {
    if (!hasPermission('analytics:read')) {
        document.getElementById('dashboardStats').innerHTML = `
            <div class="${tw.emptyState}">${t('dashboard.noAnalyticsAccess')}</div>
        `;
        return;
    }
    try {
        const params = _selectedTenantId ? `?tenant_id=${_selectedTenantId}` : '';
        const data = await api(`/analytics/summary${params}`);
        const stats = data.daily_stats || [];
        const latest = stats[0] || {};
        const total = latest.total_calls || 0;
        const resolved = latest.resolved_by_bot || 0;
        const resolvedPct = total > 0 ? (resolved / total * 100).toFixed(1) : '0.0';

        _renderTenantFilter();

        document.getElementById('dashboardStats').innerHTML = `
            <div class="${tw.card} text-center"><div class="${tw.statValue}">${total}</div><div class="${tw.statLabel}">${t('dashboard.callsToday')}</div></div>
            <div class="${tw.card} text-center"><div class="${tw.statValue}">${resolved}<small class="text-xs text-neutral-500 dark:text-neutral-400"> (${resolvedPct}%)</small></div><div class="${tw.statLabel}">${t('dashboard.resolvedByBot')}</div></div>
            <div class="${tw.card} text-center"><div class="${tw.statValue}">${latest.transferred || 0}</div><div class="${tw.statLabel}">${t('dashboard.transferred')}</div></div>
            <div class="${tw.card} text-center"><div class="${tw.statValue}">${(latest.avg_quality_score || 0).toFixed(2)}</div><div class="${tw.statLabel}">${t('dashboard.avgQuality')}</div></div>
            <div class="${tw.card} text-center"><div class="${tw.statValue}">$${(latest.total_cost_usd || 0).toFixed(2)}</div><div class="${tw.statLabel}">${t('dashboard.costToday')}</div></div>
        `;
        // Reset backoff on success
        if (_currentInterval !== _BASE_INTERVAL) {
            _currentInterval = _BASE_INTERVAL;
            setRefreshTimer(loadDashboard, _currentInterval);
        }
    } catch (e) {
        document.getElementById('dashboardStats').innerHTML = `
            <div class="${tw.emptyState}">${t('dashboard.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.dashboard.loadDashboard()">${t('common.retry')}</button></div>
        `;
        // Exponential backoff on failure
        _currentInterval = Math.min(_currentInterval * 2, _MAX_INTERVAL);
        setRefreshTimer(loadDashboard, _currentInterval);
    }
}

async function exportStatsCSV() {
    try {
        const params = _selectedTenantId ? `?tenant_id=${_selectedTenantId}` : '';
        const res = await fetchWithAuth(`/analytics/summary/export${params}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const disposition = res.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="(.+)"/);
        const filename = match ? match[1] : 'daily_stats_export.csv';
        downloadBlob(blob, filename);
        showToast(t('dashboard.csvExported'));
    } catch (e) {
        showToast(t('dashboard.exportFailed', {error: e.message}), 'error');
    }
}

async function downloadPdfReport() {
    const today = new Date();
    const weekAgo = new Date(today);
    weekAgo.setDate(weekAgo.getDate() - 7);
    const df = weekAgo.toISOString().split('T')[0];
    const dt = today.toISOString().split('T')[0];
    try {
        const res = await fetchWithAuth(`/analytics/report/pdf?date_from=${df}&date_to=${dt}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const disposition = res.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="(.+)"/);
        const filename = match ? match[1] : `report_${df}_${dt}.pdf`;
        downloadBlob(blob, filename);
        showToast(t('dashboard.pdfDownloaded'));
    } catch (e) {
        showToast(t('dashboard.pdfFailed', {error: e.message}), 'error');
    }
}

export function init() {
    registerPageLoader('dashboard', async () => {
        // Hide export buttons if user lacks analytics permission
        const exportBtns = document.querySelectorAll('#page-dashboard .flex.gap-2 button');
        const canAnalytics = hasPermission('analytics:read');
        exportBtns.forEach(btn => { btn.style.display = canAnalytics ? '' : 'none'; });

        await _loadTenants();
        _renderTenantFilter();
        loadDashboard();
        if (canAnalytics) setRefreshTimer(loadDashboard, 30000);
    });
}

// Expose for onclick handlers in HTML
window._pages = window._pages || {};
window._pages.dashboard = { loadDashboard, exportStatsCSV, downloadPdfReport, onTenantChange };
