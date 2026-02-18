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
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.settings.loadSettings()">${t('common.retry')}</button></div>`;
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

async function reloadConfig() {
    if (!confirm(t('settings.reloadConfirm'))) return;
    try {
        const data = await api('/admin/config/reload', { method: 'POST' });
        let msg = t('settings.configReloaded');
        if (data.changes) {
            for (const [k, v] of Object.entries(data.changes)) msg += `${k}: ${v}\n`;
        }
        showToast(msg, 'success');
    } catch (e) { showToast(t('settings.reloadFailed', {error: e.message}), 'error'); }
}

// --- LLM Routing ---

let _llmConfig = null;

async function loadLLMConfig() {
    const provContainer = document.getElementById('llmProvidersContainer');
    const taskContainer = document.getElementById('llmTasksContainer');
    provContainer.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    taskContainer.innerHTML = '';
    try {
        const [configData, providersData] = await Promise.all([
            api('/admin/llm/config'),
            api('/admin/llm/providers'),
        ]);
        _llmConfig = configData.config;
        const healthMap = {};
        for (const p of providersData.providers) {
            healthMap[p.key] = p;
        }
        renderLLMProviders(_llmConfig, healthMap);
        renderLLMTasks(_llmConfig);
    } catch (e) {
        provContainer.innerHTML = `<div class="${tw.emptyState}">${t('settings.llmLoadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

function renderLLMProviders(config, healthMap) {
    const container = document.getElementById('llmProvidersContainer');
    const providers = config.providers || {};
    let html = `<div class="overflow-x-auto"><table class="${tw.table}">
        <thead><tr>
            <th class="${tw.th}">${t('settings.llmProvider')}</th>
            <th class="${tw.th}">${t('settings.llmType')}</th>
            <th class="${tw.th}">${t('settings.llmModel')}</th>
            <th class="${tw.th}">${t('settings.llmApiKey')}</th>
            <th class="${tw.th}">${t('settings.llmEnabled')}</th>
            <th class="${tw.th}">${t('settings.llmHealth')}</th>
            <th class="${tw.th}">${t('settings.llmActions')}</th>
        </tr></thead><tbody>`;

    for (const [key, cfg] of Object.entries(providers)) {
        const health = healthMap[key] || {};
        const enabledChecked = cfg.enabled ? 'checked' : '';
        const healthBadge = health.healthy === true
            ? `<span class="${tw.badgeGreen}">OK</span>`
            : health.healthy === false
                ? `<span class="${tw.badgeRed}">DOWN</span>`
                : `<span class="${tw.badge}">—</span>`;
        const keyBadge = cfg.api_key_set
            ? `<span class="${tw.badgeGreen}">set</span>`
            : `<span class="${tw.badgeYellow}">missing</span>`;

        html += `<tr class="${tw.trHover}">
            <td class="${tw.td}"><strong>${escapeHtml(key)}</strong></td>
            <td class="${tw.td}">${escapeHtml(cfg.type || '')}</td>
            <td class="${tw.td}"><code class="text-xs">${escapeHtml(cfg.model || '')}</code></td>
            <td class="${tw.td}">${keyBadge}</td>
            <td class="${tw.td}"><input type="checkbox" ${enabledChecked} onchange="window._pages.settings.toggleLLMProvider('${escapeHtml(key)}', this.checked)"></td>
            <td class="${tw.td}">${healthBadge}</td>
            <td class="${tw.td}"><button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.settings.testLLMProvider('${escapeHtml(key)}')">${t('settings.llmTest')}</button></td>
        </tr>`;
    }
    html += '</tbody></table></div>';
    container.innerHTML = html;
}

function renderLLMTasks(config) {
    const container = document.getElementById('llmTasksContainer');
    const tasks = config.tasks || {};
    const providerKeys = Object.keys(config.providers || {});

    let html = `<h4 class="${tw.sectionTitle}">${t('settings.llmTaskRouting')}</h4>`;
    html += `<div class="overflow-x-auto"><table class="${tw.table}">
        <thead><tr>
            <th class="${tw.th}">${t('settings.llmTask')}</th>
            <th class="${tw.th}">${t('settings.llmPrimary')}</th>
            <th class="${tw.th}">${t('settings.llmFallbacks')}</th>
        </tr></thead><tbody>`;

    for (const [taskName, taskCfg] of Object.entries(tasks)) {
        const options = providerKeys.map(k =>
            `<option value="${escapeHtml(k)}" ${k === taskCfg.primary ? 'selected' : ''}>${escapeHtml(k)}</option>`
        ).join('');
        const fallbackStr = (taskCfg.fallbacks || []).join(', ') || '—';
        html += `<tr class="${tw.trHover}">
            <td class="${tw.td}"><strong>${escapeHtml(taskName)}</strong></td>
            <td class="${tw.td}"><select class="${tw.selectSm}" onchange="window._pages.settings.updateTaskRoute('${escapeHtml(taskName)}', this.value)">${options}</select></td>
            <td class="${tw.td}"><span class="text-xs text-neutral-500">${escapeHtml(fallbackStr)}</span></td>
        </tr>`;
    }
    html += '</tbody></table></div>';
    container.innerHTML = html;
}

async function toggleLLMProvider(key, enabled) {
    try {
        await api('/admin/llm/config', {
            method: 'PATCH',
            body: JSON.stringify({ providers: { [key]: { enabled } } }),
        });
        showToast(t('settings.llmProviderToggled', {key, state: enabled ? 'ON' : 'OFF'}), 'success');
    } catch (e) {
        showToast(t('settings.llmConfigFailed', {error: e.message}), 'error');
        loadLLMConfig();
    }
}

async function updateTaskRoute(taskName, primary) {
    try {
        await api('/admin/llm/config', {
            method: 'PATCH',
            body: JSON.stringify({ tasks: { [taskName]: { primary } } }),
        });
        showToast(t('settings.llmRouteUpdated', {task: taskName, provider: primary}), 'success');
    } catch (e) {
        showToast(t('settings.llmConfigFailed', {error: e.message}), 'error');
    }
}

async function testLLMProvider(key) {
    showToast(t('settings.llmTesting', {key}), 'info');
    try {
        const result = await api(`/admin/llm/providers/${encodeURIComponent(key)}/test`, { method: 'POST' });
        if (result.success) {
            showToast(t('settings.llmTestSuccess', {key, latency: result.latency_ms}), 'success');
        } else {
            showToast(t('settings.llmTestFailed', {key, error: result.error || 'Unknown'}), 'error');
        }
    } catch (e) {
        showToast(t('settings.llmTestFailed', {key, error: e.message}), 'error');
    }
}

export function init() {
    registerPageLoader('settings', () => loadSettings());
}

window._pages = window._pages || {};
window._pages.settings = {
    loadSettings, loadSystemStatus, reloadConfig,
    loadLLMConfig, toggleLLMProvider, updateTaskRoute, testLLMProvider,
};
