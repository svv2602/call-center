import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _llmConfig = null;

// ═══════════════════════════════════════════════════════════
//  Hot-reload конфиг
// ═══════════════════════════════════════════════════════════
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

// ═══════════════════════════════════════════════════════════
//  LLM Routing
// ═══════════════════════════════════════════════════════════
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
            <td class="${tw.td}"><input type="text" value="${escapeHtml(cfg.model || '')}" class="text-xs font-mono bg-transparent border border-neutral-300 dark:border-neutral-700 rounded px-1.5 py-0.5 w-48 focus:outline-none focus:border-blue-500" onchange="window._pages.configuration.updateLLMModel('${escapeHtml(key)}', this.value)"></td>
            <td class="${tw.td}">${keyBadge}</td>
            <td class="${tw.td}"><input type="checkbox" ${enabledChecked} onchange="window._pages.configuration.toggleLLMProvider('${escapeHtml(key)}', this.checked)"></td>
            <td class="${tw.td}">${healthBadge}</td>
            <td class="${tw.td}"><button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.configuration.testLLMProvider('${escapeHtml(key)}')">${t('settings.llmTest')}</button></td>
        </tr>`;
    }
    html += `<tr id="llm-add-row" style="display:none" class="${tw.trHover}">
            <td class="${tw.td}"><span id="llm-add-key-preview" class="text-xs font-mono text-neutral-400">—</span></td>
            <td class="${tw.td}"><select id="llm-add-type" class="${tw.selectSm}" onchange="window._pages.configuration.onAddTypeChange(this.value)">
                <option value="anthropic">anthropic</option>
                <option value="openai">openai</option>
                <option value="deepseek">deepseek</option>
                <option value="gemini">gemini</option>
            </select></td>
            <td class="${tw.td}"><input id="llm-add-model" type="text" placeholder="claude-opus-4-20250115" class="text-xs font-mono bg-transparent border border-neutral-300 dark:border-neutral-700 rounded px-1.5 py-0.5 w-48 focus:outline-none focus:border-blue-500" oninput="window._pages.configuration.onAddModelInput()"></td>
            <td class="${tw.td}"><span id="llm-add-env-preview" class="text-xs font-mono text-neutral-500">ANTHROPIC_API_KEY</span></td>
            <td colspan="3" class="${tw.td}">
                <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.configuration.saveNewProvider()">${t('common.save')}</button>
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
                        <button class="px-0.5 text-neutral-400 hover:text-blue-600 disabled:opacity-30 disabled:cursor-default" ${isFirst ? 'disabled' : ''} onclick="window._pages.configuration.moveFallback('${escapeHtml(taskName)}', ${idx}, -1)" title="${t('settings.llmFbMoveUp')}">&#9650;</button>
                        <button class="px-0.5 text-neutral-400 hover:text-blue-600 disabled:opacity-30 disabled:cursor-default" ${isLast ? 'disabled' : ''} onclick="window._pages.configuration.moveFallback('${escapeHtml(taskName)}', ${idx}, 1)" title="${t('settings.llmFbMoveDown')}">&#9660;</button>
                        <button class="px-0.5 text-neutral-400 hover:text-red-600" onclick="window._pages.configuration.removeFallback('${escapeHtml(taskName)}', '${escapeHtml(fb)}')" title="${t('common.delete')}">&#10005;</button>
                    </span>
                </li>`;
            });
            fallbackHtml += '</ol>';
        }

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
                <button class="text-xs text-blue-600 hover:text-blue-800 dark:text-blue-400" onclick="window._pages.configuration.addFallback('${escapeHtml(taskName)}')">+ ${t('common.add')}</button>
            </div>`;
        }
        fallbackHtml += '</div>';

        html += `<tr class="${tw.trHover}">
            <td class="${tw.td}"><strong>${escapeHtml(taskName)}</strong>${_taskTooltip(taskName)}</td>
            <td class="${tw.td}"><select class="${tw.selectSm}" onchange="window._pages.configuration.updateTaskRoute('${escapeHtml(taskName)}', this.value)">${options}</select></td>
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
        loadLLMConfig();
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

export function init() {
    registerPageLoader('configuration', loadLLMConfig);
}

window._pages = window._pages || {};
window._pages.configuration = {
    reloadConfig, loadLLMConfig, toggleLLMProvider, updateLLMModel,
    onAddTypeChange, onAddModelInput, saveNewProvider,
    updateTaskRoute, addFallback, removeFallback, moveFallback, testLLMProvider,
};
