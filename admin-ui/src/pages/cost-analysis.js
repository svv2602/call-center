import { api } from '../api.js';
import { t } from '../i18n.js';
import { showToast } from '../notifications.js';
import { registerPageLoader } from '../router.js';

let _pricingData = [];
let _catalogSearchTimer = null;

function _buildFilterParams() {
    const params = new URLSearchParams();
    const df = document.getElementById('costDateFrom')?.value;
    const dt = document.getElementById('costDateTo')?.value;
    const tt = document.getElementById('costTaskType')?.value;
    const tid = document.getElementById('costTenant')?.value;
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    if (tt) params.set('task_type', tt);
    if (tid) params.set('tenant_id', tid);
    return params.toString();
}

// --- New models banner ---

async function loadNewModelsCount() {
    try {
        const data = await api('/admin/llm-costs/catalog/new-count');
        const banner = document.getElementById('costNewModelsBanner');
        const textEl = document.getElementById('costNewModelsText');
        if (!banner || !textEl) return;
        if (data.count > 0) {
            textEl.textContent = t('costs.newModelsFound', { count: data.count });
            banner.style.display = '';
        } else {
            banner.style.display = 'none';
        }
    } catch {
        // non-critical
    }
}

// --- Pricing sub-tabs ---

function switchPricingTab(tab) {
    const myTab = document.getElementById('costMyModelsTab');
    const catTab = document.getElementById('costCatalogTab');
    const btnMy = document.getElementById('costTabMyModels');
    const btnCat = document.getElementById('costTabCatalog');
    if (!myTab || !catTab) return;

    const activeClass = 'pb-2 text-sm font-medium border-b-2 border-blue-600 text-blue-600 dark:text-blue-400 dark:border-blue-400';
    const inactiveClass = 'pb-2 text-sm font-medium border-b-2 border-transparent text-neutral-500 dark:text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200';

    if (tab === 'catalog') {
        myTab.style.display = 'none';
        catTab.style.display = '';
        btnMy.className = inactiveClass;
        btnCat.className = activeClass;
        loadCatalog();
    } else {
        myTab.style.display = '';
        catTab.style.display = 'none';
        btnMy.className = activeClass;
        btnCat.className = inactiveClass;
    }
}

function switchToCatalog() {
    switchPricingTab('catalog');
}

// --- Pricing ---

async function loadPricing() {
    try {
        const data = await api('/admin/llm-costs/pricing');
        _pricingData = data.items || [];
        _renderPricingTable(_pricingData);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function _renderPricingTable(items) {
    const tbody = document.getElementById('pricingTableBody');
    if (!tbody) return;
    if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="8" class="px-3 py-8 text-center text-neutral-400">${t('costs.noData')}</td></tr>`;
        return;
    }
    tbody.innerHTML = items.map(r => `
        <tr class="border-b border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-800/50">
            <td class="px-3 py-2.5 text-sm font-mono">${_esc(r.provider_key)}</td>
            <td class="px-3 py-2.5 text-sm">${_esc(r.display_name)}</td>
            <td class="px-3 py-2.5 text-sm text-neutral-500 dark:text-neutral-400">${_esc(r.model_name)}</td>
            <td class="px-3 py-2.5 text-sm text-right font-mono">$${r.input_price_per_1m.toFixed(2)}</td>
            <td class="px-3 py-2.5 text-sm text-right font-mono">$${r.output_price_per_1m.toFixed(2)}</td>
            <td class="px-3 py-2.5 text-sm text-center">
                <input type="checkbox" ${r.include_in_comparison ? 'checked' : ''}
                    onchange="window._pages.costAnalysis.toggleComparison('${r.id}', this.checked)"
                    class="w-4 h-4 rounded border-neutral-300 dark:border-neutral-600 text-blue-600 focus:ring-blue-500">
            </td>
            <td class="px-3 py-2.5 text-sm text-center">${r.is_system ? '<span class="text-blue-600 dark:text-blue-400">&#10003;</span>' : ''}</td>
            <td class="px-3 py-2.5 text-sm text-right">
                <button onclick="window._pages.costAnalysis.showEditDialog('${r.id}')" class="text-blue-600 dark:text-blue-400 hover:underline text-xs mr-2">${t('costs.edit')}</button>
                ${!r.is_system ? `<button onclick="window._pages.costAnalysis.deletePricing('${r.id}')" class="text-red-600 dark:text-red-400 hover:underline text-xs">${t('costs.delete')}</button>` : ''}
            </td>
        </tr>
    `).join('');
}

async function toggleComparison(id, checked) {
    try {
        await api(`/admin/llm-costs/pricing/${id}`, {
            method: 'PATCH',
            body: JSON.stringify({ include_in_comparison: checked }),
        });
    } catch (e) {
        showToast(e.message, 'error');
        await loadPricing();
    }
}

async function syncSystem() {
    try {
        const data = await api('/admin/llm-costs/pricing/sync-system', { method: 'POST' });
        showToast(data.message, 'success');
        await loadPricing();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function deletePricing(id) {
    if (!confirm(t('costs.confirmDelete'))) return;
    try {
        await api(`/admin/llm-costs/pricing/${id}`, { method: 'DELETE' });
        showToast(t('costs.deleted'), 'success');
        await loadPricing();
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function showAddDialog() {
    _showPricingDialog(null);
}

function showEditDialog(id) {
    const item = _pricingData.find(r => r.id === id);
    if (item) _showPricingDialog(item);
}

function _showPricingDialog(existing) {
    const isEdit = !!existing;
    const title = isEdit ? t('costs.edit') : t('costs.addModel');

    // Remove any existing dialog
    document.getElementById('costPricingDialog')?.remove();

    const dialog = document.createElement('div');
    dialog.id = 'costPricingDialog';
    dialog.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
    dialog.innerHTML = `
        <div class="bg-white dark:bg-neutral-900 rounded-lg shadow-xl max-w-md w-full mx-4 overflow-hidden flex flex-col max-h-[80vh]">
            <div class="modal-fixed-header">
                <h3 class="text-lg font-semibold text-neutral-900 dark:text-neutral-50">${title}</h3>
                <span class="cursor-pointer text-lg text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200 transition-colors leading-none shrink-0" onclick="document.getElementById('costPricingDialog').remove()">&times;</span>
            </div>
            <div class="modal-body">
            <div class="space-y-3">
                <div>
                    <label class="block text-sm font-medium text-neutral-700 dark:text-neutral-300 mb-1">${t('costs.providerKey')}</label>
                    <input type="text" id="dlgProviderKey" value="${_esc(existing?.provider_key || '')}" ${isEdit ? 'disabled' : ''}
                        class="w-full px-3 py-2 text-sm border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 disabled:opacity-50">
                </div>
                <div>
                    <label class="block text-sm font-medium text-neutral-700 dark:text-neutral-300 mb-1">${t('costs.modelName')}</label>
                    <input type="text" id="dlgModelName" value="${_esc(existing?.model_name || '')}"
                        class="w-full px-3 py-2 text-sm border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100">
                </div>
                <div>
                    <label class="block text-sm font-medium text-neutral-700 dark:text-neutral-300 mb-1">${t('costs.displayName')}</label>
                    <input type="text" id="dlgDisplayName" value="${_esc(existing?.display_name || '')}"
                        class="w-full px-3 py-2 text-sm border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100">
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-sm font-medium text-neutral-700 dark:text-neutral-300 mb-1">${t('costs.inputPrice')}</label>
                        <input type="number" step="0.01" id="dlgInputPrice" value="${existing?.input_price_per_1m ?? ''}"
                            class="w-full px-3 py-2 text-sm border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100">
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-neutral-700 dark:text-neutral-300 mb-1">${t('costs.outputPrice')}</label>
                        <input type="number" step="0.01" id="dlgOutputPrice" value="${existing?.output_price_per_1m ?? ''}"
                            class="w-full px-3 py-2 text-sm border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100">
                    </div>
                </div>
            </div>
            <div class="flex justify-end gap-2 mt-5">
                <button onclick="document.getElementById('costPricingDialog').remove()"
                    class="px-4 py-2 text-sm font-medium rounded-md border border-neutral-300 dark:border-neutral-600 text-neutral-700 dark:text-neutral-200 hover:bg-neutral-50 dark:hover:bg-neutral-800">${t('costs.cancel')}</button>
                <button id="dlgSaveBtn"
                    class="px-4 py-2 text-sm font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700">${t('costs.save')}</button>
            </div>
            </div>
        </div>
    `;
    document.body.appendChild(dialog);

    // Close on backdrop click
    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.remove(); });

    document.getElementById('dlgSaveBtn').addEventListener('click', async () => {
        const body = {
            model_name: document.getElementById('dlgModelName').value.trim(),
            display_name: document.getElementById('dlgDisplayName').value.trim(),
            input_price_per_1m: parseFloat(document.getElementById('dlgInputPrice').value),
            output_price_per_1m: parseFloat(document.getElementById('dlgOutputPrice').value),
        };

        try {
            if (isEdit) {
                await api(`/admin/llm-costs/pricing/${existing.id}`, { method: 'PATCH', body: JSON.stringify(body) });
                showToast(t('costs.updated'), 'success');
            } else {
                body.provider_key = document.getElementById('dlgProviderKey').value.trim();
                await api('/admin/llm-costs/pricing', { method: 'POST', body: JSON.stringify(body) });
                showToast(t('costs.created'), 'success');
            }
            dialog.remove();
            await loadPricing();
        } catch (e) {
            showToast(e.message, 'error');
        }
    });
}

// --- Catalog ---

async function loadCatalog() {
    const params = new URLSearchParams();
    const pt = document.getElementById('catalogProviderFilter')?.value;
    const search = document.getElementById('catalogSearch')?.value;
    const showHidden = document.getElementById('catalogShowHidden')?.checked;
    if (pt) params.set('provider_type', pt);
    if (search) params.set('search', search);
    if (showHidden) params.set('include_hidden', 'true');
    const qs = params.toString();

    try {
        const [data, syncStatus] = await Promise.all([
            api(`/admin/llm-costs/catalog${qs ? '?' + qs : ''}`),
            api('/admin/llm-costs/catalog/sync-status'),
        ]);
        _renderCatalogTable(data.items || []);
        _renderSyncStatus(syncStatus.last_sync_at);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function _renderCatalogTable(items) {
    const tbody = document.getElementById('catalogTableBody');
    if (!tbody) return;
    if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="7" class="px-3 py-8 text-center text-neutral-400">${t('costs.noData')}</td></tr>`;
        return;
    }
    tbody.innerHTML = items.map(r => {
        const maxTokens = r.max_input_tokens ? _formatTokens(r.max_input_tokens) : '—';
        let statusHtml;
        if (r.is_hidden) {
            statusHtml = `<span class="text-neutral-400 dark:text-neutral-500 text-xs font-medium">${t('costs.hidden')}</span>`;
        } else if (r.is_added) {
            statusHtml = `<span class="text-green-600 dark:text-green-400 text-xs font-medium">${t('costs.added')}</span>`;
        } else if (r.is_new) {
            statusHtml = `<span class="text-blue-600 dark:text-blue-400 text-xs font-medium">${t('costs.new')}</span>`;
        } else {
            statusHtml = '<span class="text-neutral-400 text-xs">—</span>';
        }

        let actions = '';
        if (r.is_hidden) {
            actions = `<button onclick="window._pages.costAnalysis.unhideModel('${_esc(r.model_key)}')" class="text-blue-600 dark:text-blue-400 hover:underline text-xs">${t('costs.unhide')}</button>`;
        } else if (!r.is_added) {
            actions = `<button onclick="window._pages.costAnalysis.showCatalogAddDialog('${_esc(r.model_key)}')" class="text-blue-600 dark:text-blue-400 hover:underline text-xs mr-2">${t('costs.addToPricing')}</button>`;
            if (r.is_new) {
                actions += `<button onclick="window._pages.costAnalysis.dismissModel('${_esc(r.model_key)}')" class="text-neutral-500 hover:underline text-xs mr-2">${t('costs.dismiss')}</button>`;
            }
            actions += `<button onclick="window._pages.costAnalysis.hideModel('${_esc(r.model_key)}')" class="text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-300 hover:underline text-xs">${t('costs.hide')}</button>`;
        }

        const rowClass = r.is_hidden ? 'opacity-50 ' : '';
        return `
            <tr class="${rowClass}border-b border-neutral-100 dark:border-neutral-800 hover:bg-neutral-50 dark:hover:bg-neutral-800/50">
                <td class="px-3 py-2.5 text-sm font-mono">${_esc(r.model_key)}</td>
                <td class="px-3 py-2.5 text-sm">${_esc(r.display_name)}</td>
                <td class="px-3 py-2.5 text-sm text-right font-mono">$${r.input_price_per_1m.toFixed(2)}</td>
                <td class="px-3 py-2.5 text-sm text-right font-mono">$${r.output_price_per_1m.toFixed(2)}</td>
                <td class="px-3 py-2.5 text-sm text-right font-mono">${maxTokens}</td>
                <td class="px-3 py-2.5 text-sm text-center">${statusHtml}</td>
                <td class="px-3 py-2.5 text-sm text-right">${actions}</td>
            </tr>
        `;
    }).join('');
}

function _renderSyncStatus(lastSyncAt) {
    const el = document.getElementById('catalogLastSync');
    if (!el) return;
    if (lastSyncAt) {
        const d = new Date(lastSyncAt);
        el.textContent = `${t('costs.lastSync')}: ${d.toLocaleDateString()} ${d.toLocaleTimeString()}`;
    } else {
        el.textContent = t('costs.neverSynced');
    }
}

function _formatTokens(n) {
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(0) + 'M';
    if (n >= 1_000) return (n / 1_000).toFixed(0) + 'K';
    return String(n);
}

function debounceCatalogSearch() {
    if (_catalogSearchTimer) clearTimeout(_catalogSearchTimer);
    _catalogSearchTimer = setTimeout(() => loadCatalog(), 300);
}

async function syncCatalog() {
    try {
        const data = await api('/admin/llm-costs/catalog/sync', { method: 'POST' });
        showToast(t('costs.catalogSyncStarted'), 'success');
        // Reload after a short delay to see new data
        setTimeout(() => loadCatalog(), 2000);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function showCatalogAddDialog(modelKey) {
    document.getElementById('costCatalogAddDialog')?.remove();

    const dialog = document.createElement('div');
    dialog.id = 'costCatalogAddDialog';
    dialog.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-50';
    dialog.innerHTML = `
        <div class="bg-white dark:bg-neutral-900 rounded-lg shadow-xl max-w-md w-full mx-4 overflow-hidden flex flex-col max-h-[80vh]">
            <div class="modal-fixed-header">
                <h3 class="text-lg font-semibold text-neutral-900 dark:text-neutral-50">${t('costs.addToPricing')}</h3>
                <span class="cursor-pointer text-lg text-neutral-400 hover:text-neutral-600 dark:hover:text-neutral-200 transition-colors leading-none shrink-0" onclick="document.getElementById('costCatalogAddDialog').remove()">&times;</span>
            </div>
            <div class="modal-body">
            <div class="space-y-3">
                <div>
                    <label class="block text-sm font-medium text-neutral-700 dark:text-neutral-300 mb-1">Model Key</label>
                    <input type="text" value="${_esc(modelKey)}" disabled
                        class="w-full px-3 py-2 text-sm border border-neutral-300 dark:border-neutral-700 rounded-md bg-neutral-50 dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100 opacity-60">
                </div>
                <div>
                    <label class="block text-sm font-medium text-neutral-700 dark:text-neutral-300 mb-1">${t('costs.providerKey')}</label>
                    <input type="text" id="dlgCatalogProviderKey" value="${_esc(modelKey)}"
                        class="w-full px-3 py-2 text-sm border border-neutral-300 dark:border-neutral-700 rounded-md bg-white dark:bg-neutral-800 text-neutral-900 dark:text-neutral-100">
                    <p class="text-xs text-neutral-400 mt-1">${t('costs.providerKeyHint')}</p>
                </div>
            </div>
            <div class="flex justify-end gap-2 mt-5">
                <button onclick="document.getElementById('costCatalogAddDialog').remove()"
                    class="px-4 py-2 text-sm font-medium rounded-md border border-neutral-300 dark:border-neutral-600 text-neutral-700 dark:text-neutral-200 hover:bg-neutral-50 dark:hover:bg-neutral-800">${t('costs.cancel')}</button>
                <button id="dlgCatalogAddBtn"
                    class="px-4 py-2 text-sm font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700">${t('costs.save')}</button>
            </div>
            </div>
        </div>
    `;
    document.body.appendChild(dialog);

    dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.remove(); });

    document.getElementById('dlgCatalogAddBtn').addEventListener('click', async () => {
        const providerKey = document.getElementById('dlgCatalogProviderKey').value.trim();
        if (!providerKey) return;

        try {
            await api('/admin/llm-costs/catalog/add', {
                method: 'POST',
                body: JSON.stringify({ model_key: modelKey, provider_key: providerKey }),
            });
            showToast(t('costs.modelAdded'), 'success');
            dialog.remove();
            await Promise.all([loadCatalog(), loadPricing(), loadNewModelsCount()]);
        } catch (e) {
            showToast(e.message, 'error');
        }
    });
}

async function dismissModel(modelKey) {
    try {
        await api('/admin/llm-costs/catalog/dismiss', {
            method: 'POST',
            body: JSON.stringify({ model_keys: [modelKey] }),
        });
        showToast(t('costs.modelsDismissed'), 'success');
        await Promise.all([loadCatalog(), loadNewModelsCount()]);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// --- Hide / Unhide ---

async function hideModel(modelKey) {
    try {
        await api('/admin/llm-costs/catalog/hide', {
            method: 'POST',
            body: JSON.stringify({ model_keys: [modelKey] }),
        });
        showToast(t('costs.modelsHidden'), 'success');
        await Promise.all([loadCatalog(), loadNewModelsCount()]);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

async function unhideModel(modelKey) {
    try {
        await api('/admin/llm-costs/catalog/unhide', {
            method: 'POST',
            body: JSON.stringify({ model_keys: [modelKey] }),
        });
        showToast(t('costs.modelUnhidden'), 'success');
        await Promise.all([loadCatalog(), loadNewModelsCount()]);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

// --- Usage summary ---

async function loadUsage() {
    const qs = _buildFilterParams();
    try {
        const [summary, comparison] = await Promise.all([
            api(`/admin/llm-costs/usage/summary${qs ? '?' + qs : ''}`),
            api(`/admin/llm-costs/usage/model-comparison${qs ? '?' + qs : ''}`),
        ]);
        _renderSummary(summary.items || []);
        _renderComparison(comparison);
    } catch (e) {
        showToast(e.message, 'error');
    }
}

function _renderSummary(items) {
    const tbody = document.getElementById('usageSummaryBody');
    if (!tbody) return;
    if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="7" class="px-3 py-8 text-center text-neutral-400">${t('costs.noData')}</td></tr>`;
        return;
    }
    tbody.innerHTML = items.map(r => `
        <tr class="border-b border-neutral-100 dark:border-neutral-800">
            <td class="px-3 py-2.5 text-sm">${_esc(r.task_type)}</td>
            <td class="px-3 py-2.5 text-sm font-mono">${_esc(r.provider_key)}</td>
            <td class="px-3 py-2.5 text-sm text-right">${r.call_count.toLocaleString()}</td>
            <td class="px-3 py-2.5 text-sm text-right font-mono">${r.total_input_tokens.toLocaleString()}</td>
            <td class="px-3 py-2.5 text-sm text-right font-mono">${r.total_output_tokens.toLocaleString()}</td>
            <td class="px-3 py-2.5 text-sm text-right">${r.avg_latency_ms != null ? Math.round(r.avg_latency_ms) : '—'}</td>
            <td class="px-3 py-2.5 text-sm text-right font-mono font-semibold">$${r.total_cost.toFixed(4)}</td>
        </tr>
    `).join('');
}

// --- Cost Comparison ---

function _renderComparison(data) {
    const el = document.getElementById('comparisonContent');
    if (!el) return;

    if (!data.comparisons || !data.comparisons.length) {
        el.innerHTML = `<p class="text-neutral-400 text-sm py-4 text-center">${t('costs.noData')}</p>`;
        return;
    }

    const actualCost = data.actual_cost || 0;
    const rows = data.comparisons.map(c => {
        const diff = actualCost > 0 ? c.cost - actualCost : 0;
        const pct = actualCost > 0 ? ((diff / actualCost) * 100).toFixed(1) : '0.0';
        const diffClass = diff < 0 ? 'text-green-600 dark:text-green-400' : diff > 0 ? 'text-red-600 dark:text-red-400' : '';
        const diffSign = diff > 0 ? '+' : '';
        const highlight = c.is_actual ? 'bg-blue-50 dark:bg-blue-900/20' : '';
        const badge = c.is_actual ? `<span class="ml-1 text-xs text-blue-600 dark:text-blue-400">(${t('costs.actual')})</span>` : '';

        return `
            <tr class="border-b border-neutral-100 dark:border-neutral-800 ${highlight}">
                <td class="px-3 py-2.5 text-sm">${_esc(c.display_name)}${badge}</td>
                <td class="px-3 py-2.5 text-sm text-right font-mono font-semibold">$${c.cost.toFixed(4)}</td>
                <td class="px-3 py-2.5 text-sm text-right font-mono ${diffClass}">${c.is_actual ? '—' : `${diffSign}$${diff.toFixed(4)} (${diffSign}${pct}%)`}</td>
            </tr>
        `;
    }).join('');

    el.innerHTML = `
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4 text-sm">
            <div class="bg-neutral-50 dark:bg-neutral-800 rounded-lg p-3">
                <div class="text-neutral-500 dark:text-neutral-400">${t('costs.provider')}</div>
                <div class="font-mono font-semibold">${_esc(data.actual_provider || '—')}</div>
            </div>
            <div class="bg-neutral-50 dark:bg-neutral-800 rounded-lg p-3">
                <div class="text-neutral-500 dark:text-neutral-400">${t('costs.totalCost')}</div>
                <div class="font-mono font-semibold">$${actualCost.toFixed(4)}</div>
            </div>
            <div class="bg-neutral-50 dark:bg-neutral-800 rounded-lg p-3">
                <div class="text-neutral-500 dark:text-neutral-400">${t('costs.totalInputTokens')}</div>
                <div class="font-mono font-semibold">${(data.total_input_tokens || 0).toLocaleString()}</div>
            </div>
            <div class="bg-neutral-50 dark:bg-neutral-800 rounded-lg p-3">
                <div class="text-neutral-500 dark:text-neutral-400">${t('costs.totalOutputTokens')}</div>
                <div class="font-mono font-semibold">${(data.total_output_tokens || 0).toLocaleString()}</div>
            </div>
        </div>
        <div class="overflow-x-auto">
            <table class="w-full text-sm">
                <thead>
                    <tr>
                        <th class="px-3 py-2.5 text-left text-xs font-semibold text-neutral-500 dark:text-neutral-400 uppercase tracking-wider border-b border-neutral-200 dark:border-neutral-700">${t('costs.displayName')}</th>
                        <th class="px-3 py-2.5 text-right text-xs font-semibold text-neutral-500 dark:text-neutral-400 uppercase tracking-wider border-b border-neutral-200 dark:border-neutral-700">${t('costs.cost')}</th>
                        <th class="px-3 py-2.5 text-right text-xs font-semibold text-neutral-500 dark:text-neutral-400 uppercase tracking-wider border-b border-neutral-200 dark:border-neutral-700">${t('costs.difference')}</th>
                    </tr>
                </thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    `;
}

// --- Tenants dropdown ---

async function _loadTenants() {
    try {
        const data = await api('/admin/tenants');
        const sel = document.getElementById('costTenant');
        if (!sel || !data.tenants) return;
        data.tenants.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t.id;
            opt.textContent = t.name || t.slug;
            sel.appendChild(opt);
        });
    } catch {
        // non-critical
    }
}

// --- Helpers ---

function _esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// --- Init ---

export function init() {
    registerPageLoader('cost-analysis', async () => {
        // Set default date range to current month
        const now = new Date();
        const df = document.getElementById('costDateFrom');
        const dt = document.getElementById('costDateTo');
        if (df && !df.value) df.value = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
        if (dt && !dt.value) dt.value = now.toISOString().slice(0, 10);

        await Promise.all([
            loadPricing(),
            _loadTenants(),
            loadNewModelsCount(),
        ]);
        await loadUsage();
    });

    window._pages = window._pages || {};
    window._pages.costAnalysis = {
        loadPricing,
        loadUsage,
        syncSystem,
        deletePricing,
        showAddDialog,
        showEditDialog,
        toggleComparison,
        switchPricingTab,
        switchToCatalog,
        loadCatalog,
        debounceCatalogSearch,
        syncCatalog,
        showCatalogAddDialog,
        dismissModel,
        hideModel,
        unhideModel,
    };
}
