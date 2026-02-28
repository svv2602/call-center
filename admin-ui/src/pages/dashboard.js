import { api, fetchWithAuth } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, downloadBlob, updateTimestamp } from '../utils.js';
import { skeletonCards } from '../skeleton.js';
import { registerPageLoader, setRefreshTimer } from '../router.js';
import { hasPermission } from '../auth.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

const _BASE_INTERVAL = 30_000;  // 30s normal refresh
const _MAX_INTERVAL = 300_000;  // 5min max backoff
let _currentInterval = _BASE_INTERVAL;
let _selectedTenantId = '';
let _selectedPeriod = 'today'; // today | 7d | 30d | all
let _tenants = [];

function _periodDates() {
    const today = new Date();
    const fmt = d => d.toISOString().split('T')[0];
    const dt = fmt(today);
    if (_selectedPeriod === 'all') return {};
    if (_selectedPeriod === '30d') {
        const d = new Date(today); d.setDate(d.getDate() - 30);
        return { date_from: fmt(d), date_to: dt };
    }
    if (_selectedPeriod === '7d') {
        const d = new Date(today); d.setDate(d.getDate() - 7);
        return { date_from: fmt(d), date_to: dt };
    }
    // today
    return { date_from: dt, date_to: dt };
}

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
    let html = `<label class="text-sm font-medium text-neutral-600 dark:text-neutral-400">${t('dashboard.filterByNetwork')}</label>`;
    html += `<select id="dashboardTenantSelect" class="${tw.selectSm} w-auto" onchange="window._pages.dashboard.onTenantChange(this.value)">`;
    html += `<option value="">${t('dashboard.allNetworks')}</option>`;
    for (const ten of _tenants) {
        const sel = ten.id === _selectedTenantId ? ' selected' : '';
        html += `<option value="${escapeHtml(ten.id)}"${sel}>${escapeHtml(ten.name)}</option>`;
    }
    html += '</select>';
    container.innerHTML = html;
}

function _renderPeriodFilter() {
    const container = document.getElementById('dashboardPeriodFilter');
    if (!container) return;
    const periods = [
        { key: 'today', label: t('dashboard.periodToday') },
        { key: '7d', label: t('dashboard.period7d') },
        { key: '30d', label: t('dashboard.period30d') },
        { key: 'all', label: t('dashboard.periodAll') },
    ];
    let html = `<label class="text-sm font-medium text-neutral-600 dark:text-neutral-400">${t('dashboard.period')}</label>`;
    html += '<div class="inline-flex rounded-md border border-neutral-300 dark:border-neutral-700 overflow-hidden">';
    for (const p of periods) {
        const active = p.key === _selectedPeriod;
        const cls = active
            ? 'bg-blue-600 text-white'
            : 'bg-white dark:bg-neutral-800 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-700';
        html += `<button class="px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer ${cls}" onclick="window._pages.dashboard.onPeriodChange('${p.key}')">${p.label}</button>`;
    }
    html += '</div>';
    container.innerHTML = html;
}

function onTenantChange(tenantId) {
    _selectedTenantId = tenantId;
    loadDashboard();
}

function onPeriodChange(period) {
    _selectedPeriod = period;
    _renderPeriodFilter();
    loadDashboard();
}

async function loadDashboard() {
    if (!hasPermission('analytics:read')) {
        document.getElementById('dashboardStats').innerHTML = `
            <div class="${tw.emptyState}">${t('dashboard.noAnalyticsAccess')}</div>
        `;
        return;
    }
    // Show skeleton on initial load
    const statsEl = document.getElementById('dashboardStats');
    if (statsEl && statsEl.children.length === 0) {
        statsEl.innerHTML = skeletonCards(5);
    }

    try {
        const qp = new URLSearchParams();
        if (_selectedTenantId) qp.set('tenant_id', _selectedTenantId);
        const dates = _periodDates();
        if (dates.date_from) qp.set('date_from', dates.date_from);
        if (dates.date_to) qp.set('date_to', dates.date_to);
        const qs = qp.toString() ? `?${qp}` : '';

        const data = await api(`/analytics/summary${qs}`);
        const stats = data.daily_stats || [];

        _renderTenantFilter();
        _renderPeriodFilter();

        // Aggregate across all returned days
        const totalCalls = stats.reduce((s, d) => s + (d.total_calls || 0), 0);
        const resolved = stats.reduce((s, d) => s + (d.resolved_by_bot || 0), 0);
        const transferred = stats.reduce((s, d) => s + (d.transferred || 0), 0);
        const totalCost = stats.reduce((s, d) => s + (parseFloat(d.total_cost_usd) || 0), 0);
        const qualitySum = stats.reduce((s, d) => s + (d.avg_quality_score || 0), 0);
        const qualityCount = stats.filter(d => d.avg_quality_score != null).length || 1;
        const avgQuality = qualitySum / qualityCount;
        const resolvedPct = totalCalls > 0 ? (resolved / totalCalls * 100).toFixed(1) : '0.0';

        const isToday = _selectedPeriod === 'today';
        const callsLabel = isToday ? t('dashboard.callsToday') : t('dashboard.calls');
        const costLabel = isToday ? t('dashboard.costToday') : t('dashboard.cost');

        document.getElementById('dashboardStats').innerHTML = `
            <div class="${tw.card} text-center"><div class="${tw.statValue}">${totalCalls}</div><div class="${tw.statLabel}">${callsLabel}</div></div>
            <div class="${tw.card} text-center"><div class="${tw.statValue}">${resolved}<small class="text-xs text-neutral-500 dark:text-neutral-400"> (${resolvedPct}%)</small></div><div class="${tw.statLabel}">${t('dashboard.resolvedByBot')}</div></div>
            <div class="${tw.card} text-center"><div class="${tw.statValue}">${transferred}</div><div class="${tw.statLabel}">${t('dashboard.transferred')}</div></div>
            <div class="${tw.card} text-center"><div class="${tw.statValue}">${avgQuality.toFixed(2)}</div><div class="${tw.statLabel}">${t('dashboard.avgQuality')}</div></div>
            <div class="${tw.card} text-center"><div class="${tw.statValue}">$${totalCost.toFixed(2)}</div><div class="${tw.statLabel}">${costLabel}</div></div>
        `;
        updateTimestamp('dashboardLastUpdated');
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

function _activateGrafana() {
    try {
        const grafanaBlock = document.getElementById('grafanaBlock');
        const grafanaFrame = document.getElementById('grafanaFrame');
        if (!grafanaBlock || !grafanaFrame) return;
        const baseUrl = window.location.origin;
        const dashPath = '/grafana/d/calls-overview/calls-overview?orgId=1&refresh=30s&kiosk';
        grafanaFrame.onerror = () => { grafanaBlock.style.display = 'none'; };
        grafanaFrame.src = baseUrl + dashPath;
        grafanaBlock.style.display = '';
    } catch {
        // Grafana unavailable — leave block hidden
    }
}

async function exportStatsCSV() {
    try {
        const qp = new URLSearchParams();
        if (_selectedTenantId) qp.set('tenant_id', _selectedTenantId);
        const dates = _periodDates();
        if (dates.date_from) qp.set('date_from', dates.date_from);
        if (dates.date_to) qp.set('date_to', dates.date_to);
        const qs = qp.toString() ? `?${qp}` : '';

        const res = await fetchWithAuth(`/analytics/summary/export${qs}`);
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
    const dates = _periodDates();
    // PDF always needs a date range — default to last 7 days if "all"
    const today = new Date();
    const fmt = d => d.toISOString().split('T')[0];
    const df = dates.date_from || (() => { const d = new Date(today); d.setDate(d.getDate() - 7); return fmt(d); })();
    const dt = dates.date_to || fmt(today);
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
        _renderPeriodFilter();
        _activateGrafana();
        loadDashboard();
        if (canAnalytics) setRefreshTimer(loadDashboard, 30000);
    });
}

// Expose for onclick handlers in HTML
window._pages = window._pages || {};
window._pages.dashboard = { loadDashboard, exportStatsCSV, downloadPdfReport, onTenantChange, onPeriodChange };
