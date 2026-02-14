import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml } from '../utils.js';
import { registerPageLoader } from '../router.js';

async function loadSettings() {
    const container = document.getElementById('systemInfo');
    container.innerHTML = '<div class="loading-overlay"><div class="spinner"></div></div>';
    try {
        const [health, ready] = await Promise.all([
            fetch('/health').then(r => r.json()),
            fetch('/health/ready').then(r => r.json()).catch(() => null),
        ]);
        let html = `<table>
            <tr><td>Status</td><td><span class="badge badge-green">${health.status}</span></td></tr>
            <tr><td>Active calls</td><td>${health.active_calls}</td></tr>
            <tr><td>Redis</td><td><span class="badge ${health.redis === 'connected' ? 'badge-green' : 'badge-red'}">${health.redis}</span></td></tr>
        `;
        if (ready) {
            if (ready.store_api) html += `<tr><td>Store API</td><td><span class="badge ${ready.store_api === 'reachable' ? 'badge-green' : 'badge-red'}">${ready.store_api}</span></td></tr>`;
            if (ready.claude_api) html += `<tr><td>Claude API</td><td><span class="badge ${ready.claude_api === 'reachable' ? 'badge-green' : 'badge-red'}">${ready.claude_api}</span></td></tr>`;
            if (ready.google_stt) html += `<tr><td>Google STT</td><td><span class="badge ${ready.google_stt === 'credentials_present' ? 'badge-green' : 'badge-yellow'}">${ready.google_stt}</span></td></tr>`;
            if (ready.tts_engine) html += `<tr><td>TTS Engine</td><td><span class="badge ${ready.tts_engine === 'initialized' ? 'badge-green' : 'badge-yellow'}">${ready.tts_engine}</span></td></tr>`;
        }
        html += '</table>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="empty-state">Failed to load system info: ${escapeHtml(e.message)}
            <br><button class="btn btn-primary btn-sm" onclick="window._pages.settings.loadSettings()" style="margin-top:.5rem">Retry</button></div>`;
    }
}

async function loadSystemStatus() {
    const container = document.getElementById('extendedStatus');
    container.innerHTML = '<div class="loading-overlay"><div class="spinner"></div></div>';
    try {
        const data = await api('/admin/system-status');
        const uptimeH = Math.floor(data.uptime_seconds / 3600);
        const uptimeM = Math.floor((data.uptime_seconds % 3600) / 60);
        let html = '<table>';
        html += `<tr><td>Version</td><td>${escapeHtml(data.version || 'unknown')}</td></tr>`;
        html += `<tr><td>Uptime</td><td>${uptimeH}h ${uptimeM}m</td></tr>`;
        if (data.postgres_db_size_bytes) {
            const sizeMB = (data.postgres_db_size_bytes / 1048576).toFixed(1);
            html += `<tr><td>PostgreSQL DB size</td><td>${sizeMB} MB</td></tr>`;
        }
        if (data.postgres_connections !== undefined) html += `<tr><td>PostgreSQL connections</td><td>${data.postgres_connections}</td></tr>`;
        if (data.redis_used_memory) html += `<tr><td>Redis memory</td><td>${escapeHtml(data.redis_used_memory)}</td></tr>`;
        html += `<tr><td>Celery workers</td><td><span class="badge ${data.celery_workers_online > 0 ? 'badge-green' : 'badge-red'}">${data.celery_workers_online}</span></td></tr>`;
        if (data.last_backup) {
            const sizeMB = (data.last_backup.size_bytes / 1048576).toFixed(1);
            html += `<tr><td>Last backup</td><td>${escapeHtml(data.last_backup.file)} (${sizeMB} MB, ${escapeHtml(data.last_backup.modified)})</td></tr>`;
        }
        html += '</table>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="empty-state">Failed: ${escapeHtml(e.message)}</div>`;
    }
}

async function reloadConfig() {
    if (!confirm('Reload configuration from environment?')) return;
    try {
        const data = await api('/admin/config/reload', { method: 'POST' });
        let msg = 'Config reloaded:\n';
        if (data.changes) {
            for (const [k, v] of Object.entries(data.changes)) msg += `${k}: ${v}\n`;
        }
        showToast(msg, 'success');
    } catch (e) { showToast('Reload failed: ' + e.message, 'error'); }
}

export function init() {
    registerPageLoader('settings', () => loadSettings());
}

window._pages = window._pages || {};
window._pages.settings = { loadSettings, loadSystemStatus, reloadConfig };
