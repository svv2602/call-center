import { api, fetchWithAuth } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, downloadBlob } from '../utils.js';
import { registerPageLoader, setRefreshTimer } from '../router.js';

async function loadDashboard() {
    try {
        const data = await api('/analytics/summary');
        const stats = data.daily_stats || [];
        const latest = stats[0] || {};
        const total = latest.total_calls || 0;
        const resolved = latest.resolved_by_bot || 0;
        const resolvedPct = total > 0 ? (resolved / total * 100).toFixed(1) : '0.0';
        document.getElementById('dashboardStats').innerHTML = `
            <div class="card stat-card"><div class="value">${total}</div><div class="label">Calls today</div></div>
            <div class="card stat-card"><div class="value">${resolved}<small style="font-size:.7rem;color:#64748b"> (${resolvedPct}%)</small></div><div class="label">Resolved by bot</div></div>
            <div class="card stat-card"><div class="value">${latest.transferred || 0}</div><div class="label">Transferred</div></div>
            <div class="card stat-card"><div class="value">${(latest.avg_quality_score || 0).toFixed(2)}</div><div class="label">Avg quality</div></div>
            <div class="card stat-card"><div class="value">$${(latest.total_cost_usd || 0).toFixed(2)}</div><div class="label">Cost today</div></div>
        `;
    } catch (e) {
        document.getElementById('dashboardStats').innerHTML = `
            <div class="empty-state">Failed to load dashboard: ${escapeHtml(e.message)}
            <br><button class="btn btn-primary" onclick="window._pages.dashboard.loadDashboard()" style="margin-top:.5rem">Retry</button></div>
        `;
    }
}

async function exportStatsCSV() {
    try {
        const res = await fetchWithAuth('/analytics/summary/export');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const blob = await res.blob();
        const disposition = res.headers.get('Content-Disposition') || '';
        const match = disposition.match(/filename="(.+)"/);
        const filename = match ? match[1] : 'daily_stats_export.csv';
        downloadBlob(blob, filename);
        showToast('CSV exported');
    } catch (e) {
        showToast('Export failed: ' + e.message, 'error');
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
        showToast('PDF report downloaded');
    } catch (e) {
        showToast('PDF download failed: ' + e.message, 'error');
    }
}

export function init() {
    registerPageLoader('dashboard', () => {
        loadDashboard();
        setRefreshTimer(loadDashboard, 30000);
    });
}

// Expose for onclick handlers in HTML
window._pages = window._pages || {};
window._pages.dashboard = { loadDashboard, exportStatsCSV, downloadPdfReport };
