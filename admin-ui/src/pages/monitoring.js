import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

async function loadSettings() {
    const container = document.getElementById('systemInfo');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const [health, ready] = await Promise.all([
            fetch('/health').then(r => r.json()),
            fetch('/health/ready').then(r => r.json()).catch(() => null),
        ]);
        let html = `<div class="overflow-x-auto"><table class="${tw.table}">
            <tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.status')}</td><td class="${tw.td}"><span class="${tw.badgeGreen}">${health.status}</span></td></tr>
            <tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.activeCalls')}</td><td class="${tw.td}">${health.active_calls}</td></tr>
            <tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.redis')}</td><td class="${tw.td}"><span class="${health.redis === 'connected' ? tw.badgeGreen : tw.badgeRed}">${health.redis}</span></td></tr>
        `;
        if (ready) {
            if (ready.store_api) html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.storeAPI')}</td><td class="${tw.td}"><span class="${ready.store_api === 'reachable' ? tw.badgeGreen : tw.badgeRed}">${ready.store_api}</span></td></tr>`;
            if (ready.claude_api) html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.claudeAPI')}</td><td class="${tw.td}"><span class="${ready.claude_api === 'reachable' ? tw.badgeGreen : tw.badgeRed}">${ready.claude_api}</span></td></tr>`;
            if (ready.google_stt) html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.googleSTT')}</td><td class="${tw.td}"><span class="${ready.google_stt === 'credentials_present' ? tw.badgeGreen : tw.badgeYellow}">${ready.google_stt}</span></td></tr>`;
            if (ready.tts_engine) html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.ttsEngine')}</td><td class="${tw.td}"><span class="${ready.tts_engine === 'initialized' ? tw.badgeGreen : tw.badgeYellow}">${ready.tts_engine}</span></td></tr>`;
        }
        html += '</table></div>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('settings.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.monitoring.loadSettings()">${t('common.retry')}</button></div>`;
    }
}

async function loadSystemStatus() {
    const container = document.getElementById('extendedStatus');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/admin/system-status');
        const uptimeH = Math.floor(data.uptime_seconds / 3600);
        const uptimeM = Math.floor((data.uptime_seconds % 3600) / 60);
        let html = `<div class="overflow-x-auto"><table class="${tw.table}">`;
        html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.extVersion')}</td><td class="${tw.td}">${escapeHtml(data.version || 'unknown')}</td></tr>`;
        html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.uptime')}</td><td class="${tw.td}">${t('settings.uptimeValue', {hours: uptimeH, minutes: uptimeM})}</td></tr>`;
        if (data.postgres_db_size_bytes) {
            const sizeMB = (data.postgres_db_size_bytes / 1048576).toFixed(1);
            html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.pgDbSize')}</td><td class="${tw.td}">${sizeMB} MB</td></tr>`;
        }
        if (data.postgres_connections !== undefined) html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.pgConnections')}</td><td class="${tw.td}">${data.postgres_connections}</td></tr>`;
        if (data.redis_used_memory) html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.redisMemory')}</td><td class="${tw.td}">${escapeHtml(data.redis_used_memory)}</td></tr>`;
        html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.celeryWorkers')}</td><td class="${tw.td}"><span class="${data.celery_workers_online > 0 ? tw.badgeGreen : tw.badgeRed}">${data.celery_workers_online}</span></td></tr>`;
        if (data.last_backup) {
            const sizeMB = (data.last_backup.size_bytes / 1048576).toFixed(1);
            html += `<tr class="${tw.trHover}"><td class="${tw.td}">${t('settings.lastBackup')}</td><td class="${tw.td}">${escapeHtml(data.last_backup.file)} (${sizeMB} MB, ${escapeHtml(data.last_backup.modified)})</td></tr>`;
        }
        html += '</table></div>';
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('settings.extFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

function loadFlower() {
    const flowerUrl = `${window.location.protocol}//${window.location.hostname}:5555`;
    const container = document.getElementById('flowerContainer');
    const frame = document.getElementById('flowerFrame');
    const link = document.getElementById('flowerExternalLink');
    if (link) link.href = flowerUrl;
    if (frame) frame.src = flowerUrl;
    if (container) container.style.display = 'block';
}

export function init() {
    // Set external Flower link on page load
    const link = document.getElementById('flowerExternalLink');
    if (link) link.href = `${window.location.protocol}//${window.location.hostname}:5555`;
    registerPageLoader('monitoring', () => loadSettings());
}

window._pages = window._pages || {};
window._pages.monitoring = { loadSettings, loadSystemStatus, loadFlower };
