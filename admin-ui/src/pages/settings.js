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
            <td class="${tw.td}"><input type="text" value="${escapeHtml(cfg.model || '')}" class="text-xs font-mono bg-transparent border border-neutral-300 dark:border-neutral-700 rounded px-1.5 py-0.5 w-48 focus:outline-none focus:border-blue-500" onchange="window._pages.settings.updateLLMModel('${escapeHtml(key)}', this.value)"></td>
            <td class="${tw.td}">${keyBadge}</td>
            <td class="${tw.td}"><input type="checkbox" ${enabledChecked} onchange="window._pages.settings.toggleLLMProvider('${escapeHtml(key)}', this.checked)"></td>
            <td class="${tw.td}">${healthBadge}</td>
            <td class="${tw.td}"><button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.settings.testLLMProvider('${escapeHtml(key)}')">${t('settings.llmTest')}</button></td>
        </tr>`;
    }
    html += `<tr id="llm-add-row" style="display:none" class="${tw.trHover}">
            <td class="${tw.td}"><span id="llm-add-key-preview" class="text-xs font-mono text-neutral-400">—</span></td>
            <td class="${tw.td}"><select id="llm-add-type" class="${tw.selectSm}" onchange="window._pages.settings.onAddTypeChange(this.value)">
                <option value="anthropic">anthropic</option>
                <option value="openai">openai</option>
                <option value="deepseek">deepseek</option>
                <option value="gemini">gemini</option>
            </select></td>
            <td class="${tw.td}"><input id="llm-add-model" type="text" placeholder="claude-opus-4-20250115" class="text-xs font-mono bg-transparent border border-neutral-300 dark:border-neutral-700 rounded px-1.5 py-0.5 w-48 focus:outline-none focus:border-blue-500" oninput="window._pages.settings.onAddModelInput()"></td>
            <td class="${tw.td}"><span id="llm-add-env-preview" class="text-xs font-mono text-neutral-500">ANTHROPIC_API_KEY</span></td>
            <td colspan="3" class="${tw.td}">
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.settings.saveNewProvider()">${t('common.save')}</button>
                <button class="${tw.btnSm} ml-1 text-neutral-500" onclick="document.getElementById('llm-add-row').style.display='none'">${t('common.cancel')}</button>
            </td>
        </tr>`;
    html += '</tbody></table></div>';
    html += `<button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="document.getElementById('llm-add-row').style.display=''">${t('settings.llmAddProvider')}</button>`;
    container.innerHTML = html;
}

function _taskTooltip(taskName) {
    const descKey = `settings.llmTaskDesc_${taskName}`;
    const recKey = `settings.llmTaskRec_${taskName}`;
    const desc = t(descKey);
    const rec = t(recKey);
    // t() returns the key itself if translation is missing
    if (desc === descKey) return '';
    return `<span class="relative group ml-1 cursor-help">
        <span class="inline-flex items-center justify-center w-4 h-4 rounded-full border border-neutral-400 dark:border-neutral-500 text-neutral-400 dark:text-neutral-500 text-[10px] font-bold leading-none">?</span>
        <span class="pointer-events-none absolute z-50 left-5 -top-1 w-64 p-2 rounded shadow-lg border text-xs bg-white dark:bg-neutral-800 text-neutral-700 dark:text-neutral-200 border-neutral-200 dark:border-neutral-700 opacity-0 group-hover:opacity-100 transition-opacity">
            <span class="block font-semibold mb-1">${escapeHtml(desc)}</span>
            ${rec !== recKey ? `<span class="block text-neutral-500 dark:text-neutral-400">${escapeHtml(rec)}</span>` : ''}
        </span>
    </span>`;
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
        const currentPrimary = taskCfg.primary || '';
        const currentFallbacks = taskCfg.fallbacks || [];
        const options = providerKeys.map(k =>
            `<option value="${escapeHtml(k)}" ${k === currentPrimary ? 'selected' : ''}>${escapeHtml(k)}</option>`
        ).join('');

        // Ordered fallback list with up/down/remove controls
        let fallbackHtml = '<div class="space-y-1">';
        if (currentFallbacks.length > 0) {
            fallbackHtml += '<ol class="list-none p-0 m-0 space-y-1">';
            currentFallbacks.forEach((fb, idx) => {
                const isFirst = idx === 0;
                const isLast = idx === currentFallbacks.length - 1;
                fallbackHtml += `<li class="flex items-center gap-1 text-xs">
                    <span class="inline-flex items-center justify-center w-4 h-4 rounded-full bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 text-[10px] font-bold flex-shrink-0">${idx + 1}</span>
                    <span class="font-mono text-neutral-700 dark:text-neutral-300">${escapeHtml(fb)}</span>
                    <span class="inline-flex gap-0.5 ml-auto flex-shrink-0">
                        <button class="px-0.5 text-neutral-400 hover:text-blue-600 disabled:opacity-30 disabled:cursor-default" ${isFirst ? 'disabled' : ''} onclick="window._pages.settings.moveFallback('${escapeHtml(taskName)}', ${idx}, -1)" title="${t('settings.llmFbMoveUp')}">&#9650;</button>
                        <button class="px-0.5 text-neutral-400 hover:text-blue-600 disabled:opacity-30 disabled:cursor-default" ${isLast ? 'disabled' : ''} onclick="window._pages.settings.moveFallback('${escapeHtml(taskName)}', ${idx}, 1)" title="${t('settings.llmFbMoveDown')}">&#9660;</button>
                        <button class="px-0.5 text-neutral-400 hover:text-red-600" onclick="window._pages.settings.removeFallback('${escapeHtml(taskName)}', '${escapeHtml(fb)}')" title="${t('common.delete')}">&#10005;</button>
                    </span>
                </li>`;
            });
            fallbackHtml += '</ol>';
        }

        // Dropdown to add new fallback
        const availableForFallback = providerKeys.filter(k => k !== currentPrimary && !currentFallbacks.includes(k));
        if (availableForFallback.length > 0) {
            const addOptions = availableForFallback.map(k =>
                `<option value="${escapeHtml(k)}">${escapeHtml(k)}</option>`
            ).join('');
            fallbackHtml += `<div class="flex items-center gap-1 mt-1">
                <select id="fb-add-${escapeHtml(taskName)}" class="text-xs bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border border-neutral-300 dark:border-neutral-600 rounded px-1 py-0.5">
                    <option value="" disabled selected>${t('settings.llmFbSelect')}</option>
                    ${addOptions}
                </select>
                <button class="text-xs text-blue-600 hover:text-blue-800 dark:text-blue-400" onclick="window._pages.settings.addFallback('${escapeHtml(taskName)}')">+ ${t('common.add')}</button>
            </div>`;
        }
        fallbackHtml += '</div>';

        html += `<tr class="${tw.trHover}">
            <td class="${tw.td}"><strong>${escapeHtml(taskName)}</strong>${_taskTooltip(taskName)}</td>
            <td class="${tw.td}"><select class="${tw.selectSm}" onchange="window._pages.settings.updateTaskRoute('${escapeHtml(taskName)}', this.value)">${options}</select></td>
            <td class="${tw.td}">${fallbackHtml}</td>
        </tr>`;
    }
    html += '</tbody></table></div>';
    container.innerHTML = html;
}

const _providerDefaults = {
    anthropic: { api_key_env: 'ANTHROPIC_API_KEY' },
    openai: { api_key_env: 'OPENAI_API_KEY', base_url: 'https://api.openai.com/v1' },
    deepseek: { api_key_env: 'DEEPSEEK_API_KEY', base_url: 'https://api.deepseek.com/v1' },
    gemini: { api_key_env: 'GEMINI_API_KEY', base_url: 'https://generativelanguage.googleapis.com/v1beta/openai' },
};

function _buildProviderKey() {
    const type = document.getElementById('llm-add-type').value;
    const model = document.getElementById('llm-add-model').value.trim();
    if (!model) return '';
    // "claude-sonnet-4-5-20250929" → "sonnet", "gpt-4o" → "gpt4o", "deepseek-chat" → "chat"
    const short = model.replace(/^claude-/, '').replace(/^gpt-/, 'gpt').split('-')[0] || model;
    return `${type}-${short}`;
}

function _updateAddPreview() {
    const key = _buildProviderKey();
    const type = document.getElementById('llm-add-type').value;
    const defaults = _providerDefaults[type] || {};
    document.getElementById('llm-add-key-preview').textContent = key || '—';
    document.getElementById('llm-add-env-preview').textContent = defaults.api_key_env || '';
}

function onAddTypeChange() { _updateAddPreview(); }
function onAddModelInput() { _updateAddPreview(); }

async function saveNewProvider() {
    const type = document.getElementById('llm-add-type').value;
    const model = document.getElementById('llm-add-model').value.trim();
    const key = _buildProviderKey();
    const apiKeyEnv = _providerDefaults[type]?.api_key_env || '';

    if (!model) {
        showToast(t('settings.llmAddFillRequired'), 'error');
        return;
    }

    const provider = { type, model, api_key_env: apiKeyEnv, enabled: false };
    const defaults = _providerDefaults[type];
    if (defaults?.base_url) provider.base_url = defaults.base_url;

    try {
        await api('/admin/llm/config', {
            method: 'PATCH',
            body: JSON.stringify({ providers: { [key]: provider } }),
        });
        showToast(t('settings.llmProviderAdded', {key}), 'success');
        loadLLMConfig();
    } catch (e) {
        showToast(t('settings.llmConfigFailed', {error: e.message}), 'error');
    }
}

async function updateLLMModel(key, model) {
    if (!model.trim()) return;
    try {
        await api('/admin/llm/config', {
            method: 'PATCH',
            body: JSON.stringify({ providers: { [key]: { model: model.trim() } } }),
        });
        showToast(t('settings.llmModelUpdated', {key, model: model.trim()}), 'success');
    } catch (e) {
        showToast(t('settings.llmConfigFailed', {error: e.message}), 'error');
        loadLLMConfig();
    }
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
            body: JSON.stringify({ tasks: { [taskName]: { primary, fallbacks: [] } } }),
        });
        showToast(t('settings.llmRouteUpdated', {task: taskName, provider: primary}), 'success');
        loadLLMConfig(); // re-render to update fallback checkboxes
    } catch (e) {
        showToast(t('settings.llmConfigFailed', {error: e.message}), 'error');
    }
}

async function _saveFallbacks(taskName, fallbacks) {
    try {
        await api('/admin/llm/config', {
            method: 'PATCH',
            body: JSON.stringify({ tasks: { [taskName]: { fallbacks } } }),
        });
        // Update local config and re-render
        if (_llmConfig && _llmConfig.tasks && _llmConfig.tasks[taskName]) {
            _llmConfig.tasks[taskName].fallbacks = fallbacks;
        }
        renderLLMTasks(_llmConfig);
        showToast(t('settings.llmFallbacksUpdated', {task: taskName, count: fallbacks.length}), 'success');
    } catch (e) {
        showToast(t('settings.llmConfigFailed', {error: e.message}), 'error');
    }
}

async function addFallback(taskName) {
    const select = document.getElementById(`fb-add-${taskName}`);
    if (!select) return;
    const key = select.value;
    if (!key) return;
    const current = (_llmConfig?.tasks?.[taskName]?.fallbacks || []).slice();
    if (!current.includes(key)) {
        current.push(key);
        await _saveFallbacks(taskName, current);
    }
}

async function removeFallback(taskName, key) {
    const current = (_llmConfig?.tasks?.[taskName]?.fallbacks || []).filter(k => k !== key);
    await _saveFallbacks(taskName, current);
}

async function moveFallback(taskName, index, direction) {
    const current = (_llmConfig?.tasks?.[taskName]?.fallbacks || []).slice();
    const newIndex = index + direction;
    if (newIndex < 0 || newIndex >= current.length) return;
    // Swap
    [current[index], current[newIndex]] = [current[newIndex], current[index]];
    await _saveFallbacks(taskName, current);
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

// --- Telegram Notifications ---

async function loadTelegramConfig() {
    const container = document.getElementById('telegramConfigContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/admin/notifications/telegram');
        let tokenStatus;
        if (data.token_set) {
            tokenStatus = `<span class="${tw.badgeGreen}">${t('settings.telegramTokenSet')}</span>`;
            if (data.token_hint) tokenStatus += ` <span class="text-xs text-neutral-400 font-mono">...${escapeHtml(data.token_hint)}</span>`;
        } else {
            tokenStatus = `<span class="${tw.badgeYellow}">${t('settings.telegramTokenMissing')}</span>`;
        }
        const sourceLabel = data.source === 'redis' ? 'Redis' : data.source === 'env' ? 'ENV' : '—';

        let html = `<div class="space-y-3">
            <div class="flex items-center gap-2 text-sm">
                <span class="text-neutral-500 dark:text-neutral-400">${t('settings.telegramStatus')}:</span>
                ${tokenStatus}
                <span class="${tw.badge}">${sourceLabel}</span>
            </div>
            <div>
                <label class="block text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-1">${t('settings.telegramBotToken')}</label>
                <input id="tgBotToken" type="password" placeholder="${t('settings.telegramBotTokenPlaceholder')}" class="w-full text-sm font-mono bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border border-neutral-300 dark:border-neutral-600 rounded px-2 py-1.5 focus:outline-none focus:border-blue-500">
            </div>
            <div>
                <label class="block text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-1">${t('settings.telegramChatId')}</label>
                <input id="tgChatId" type="text" value="${escapeHtml(data.chat_id || '')}" placeholder="${t('settings.telegramChatIdPlaceholder')}" class="w-full text-sm font-mono bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border border-neutral-300 dark:border-neutral-600 rounded px-2 py-1.5 focus:outline-none focus:border-blue-500">
            </div>
            <div class="flex gap-2">
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.settings.saveTelegramConfig()">${t('common.save')}</button>
                <button class="${tw.btnSm} border border-neutral-300 dark:border-neutral-600 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800" onclick="window._pages.settings.testTelegram()">${t('settings.telegramTestBtn')}</button>
            </div>
        </div>`;
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('settings.telegramLoadFailed', {error: escapeHtml(e.message)})}</div>`;
    }
}

async function saveTelegramConfig() {
    const botToken = document.getElementById('tgBotToken')?.value?.trim() || '';
    const chatId = document.getElementById('tgChatId')?.value?.trim() || '';

    if (!botToken && !chatId) {
        showToast(t('settings.telegramFillFields'), 'error');
        return;
    }

    const body = {};
    if (botToken) body.bot_token = botToken;
    if (chatId) body.chat_id = chatId;

    try {
        await api('/admin/notifications/telegram', {
            method: 'PATCH',
            body: JSON.stringify(body),
        });
        showToast(t('settings.telegramSaved'), 'success');
        loadTelegramConfig();
    } catch (e) {
        showToast(t('settings.telegramSaveFailed', {error: e.message}), 'error');
    }
}

async function testTelegram() {
    showToast(t('settings.telegramTesting'), 'info');
    try {
        const result = await api('/admin/notifications/telegram/test', { method: 'POST' });
        if (result.success) {
            showToast(t('settings.telegramTestSuccess', {latency: result.latency_ms}), 'success');
        } else {
            showToast(t('settings.telegramTestFailed', {error: result.error || 'Unknown'}), 'error');
        }
    } catch (e) {
        showToast(t('settings.telegramTestFailed', {error: e.message}), 'error');
    }
}

export function init() {
    registerPageLoader('settings', () => loadSettings());
}

window._pages = window._pages || {};
window._pages.settings = {
    loadSettings, loadSystemStatus, reloadConfig,
    loadLLMConfig, toggleLLMProvider, updateLLMModel, onAddTypeChange, onAddModelInput, saveNewProvider, updateTaskRoute, addFallback, removeFallback, moveFallback, testLLMProvider,
    loadTelegramConfig, saveTelegramConfig, testTelegram,
};
