import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal, showModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import { renderPagination, buildParams } from '../pagination.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _offset = 0;
let _allTools = [];
// config keys owned by the «Условия сети» form, carried through the edit modal
const NET_CONFIG_KEYS = ['sales_enabled', 'network_policy'];
let _editPolicyKeys = {};

// Canonical tool names (fallback if API unavailable)
const CANONICAL_TOOLS = [
    'get_vehicle_tire_sizes', 'search_tires', 'check_availability',
    'transfer_to_operator', 'get_order_status', 'create_order_draft',
    'update_order_delivery', 'confirm_order', 'get_fitting_stations',
    'get_fitting_slots', 'book_fitting', 'search_knowledge_base',
];

// ─── Provider override dropdown ──────────────────────────────────────────
// Cache the list of enabled providers so we don't refetch on every dialog open.
let _providersCache = null;

async function loadProvidersForOverride() {
    if (_providersCache !== null) return _providersCache;
    try {
        const data = await api('/admin/llm/providers');
        // Only include enabled providers with a working API key
        _providersCache = (data.providers || [])
            .filter(p => p.enabled && p.api_key_set)
            .map(p => ({ key: p.key, model: p.model }));
    } catch {
        _providersCache = [];
    }
    return _providersCache;
}

async function populateProviderOverrideSelect(currentValue) {
    const sel = document.getElementById('tenantAgentProviderOverride');
    if (!sel) return;
    const providers = await loadProvidersForOverride();
    // Rebuild: keep the leading "default" option, then add all providers
    const defaultLabel = t('tenants.agentProviderDefault');
    sel.innerHTML = `<option value="">${defaultLabel}</option>` +
        providers.map(p =>
            `<option value="${p.key}">${p.key} (${p.model})</option>`
        ).join('');
    sel.value = currentValue || '';
}

async function loadToolNames() {
    if (_allTools.length > 0) return;
    try {
        const data = await api('/admin/training/tools');
        _allTools = (data.tools || []).map(t => t.name);
    } catch {
        _allTools = CANONICAL_TOOLS;
    }
}

// ─── Working hours editor ────────────────────────────────────

const _DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

function _dayLabel(key) {
    return t(`tenants.day_${key}`);
}

function _renderWorkingHoursRows(workingHours) {
    // `workingHours` may be null (means 24/7) — in that case seed weekday defaults
    const wh = workingHours || {};
    const html = _DAY_KEYS.map(key => {
        const raw = wh[key];
        const isClosed = raw === null || raw === undefined;
        const start = (raw && raw.start) || (['sat', 'sun'].includes(key) ? '10:00' : '09:00');
        const end = (raw && raw.end) || (['sat', 'sun'].includes(key) ? '16:00' : '18:00');
        return `
            <div class="flex items-center gap-2 wh-row" data-day="${key}">
                <span class="text-xs text-neutral-600 dark:text-neutral-400 w-10">${_dayLabel(key)}</span>
                <input type="time" class="wh-start text-xs bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border border-neutral-300 dark:border-neutral-600 rounded px-1.5 py-0.5 focus:outline-none focus:border-blue-500 ${isClosed ? 'opacity-40' : ''}" value="${start}" ${isClosed ? 'disabled' : ''}>
                <span class="text-xs text-neutral-500">–</span>
                <input type="time" class="wh-end text-xs bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border border-neutral-300 dark:border-neutral-600 rounded px-1.5 py-0.5 focus:outline-none focus:border-blue-500 ${isClosed ? 'opacity-40' : ''}" value="${end}" ${isClosed ? 'disabled' : ''}>
                <label class="flex items-center gap-1 text-xs cursor-pointer ml-2">
                    <input type="checkbox" class="wh-closed" ${isClosed ? 'checked' : ''}>
                    <span data-i18n="tenants.dayClosed">выходной</span>
                </label>
            </div>
        `;
    }).join('');
    document.getElementById('tenantWorkingHoursRows').innerHTML = html;

    // Wire checkbox → enable/disable time inputs on that row
    document.querySelectorAll('#tenantWorkingHoursRows .wh-row').forEach(row => {
        const cb = row.querySelector('.wh-closed');
        cb.addEventListener('change', () => {
            const disabled = cb.checked;
            row.querySelectorAll('.wh-start, .wh-end').forEach(inp => {
                inp.disabled = disabled;
                inp.classList.toggle('opacity-40', disabled);
            });
        });
    });
}

function fillWorkingHours(workingHours) {
    const enabled = workingHours !== null && workingHours !== undefined;
    document.getElementById('tenantWorkingHoursEnabled').checked = enabled;
    document.getElementById('tenantWorkingHoursBody').classList.toggle('hidden', !enabled);
    document.getElementById('tenantWorkingHoursTz').value = (workingHours && workingHours.timezone) || 'Europe/Kyiv';
    _renderWorkingHoursRows(workingHours);
}

function readWorkingHoursFromForm() {
    if (!document.getElementById('tenantWorkingHoursEnabled').checked) return null;
    const tz = document.getElementById('tenantWorkingHoursTz').value.trim() || 'Europe/Kyiv';
    const out = { timezone: tz };
    document.querySelectorAll('#tenantWorkingHoursRows .wh-row').forEach(row => {
        const key = row.dataset.day;
        const isClosed = row.querySelector('.wh-closed').checked;
        if (isClosed) {
            out[key] = null;
        } else {
            const start = row.querySelector('.wh-start').value;
            const end = row.querySelector('.wh-end').value;
            if (start && end && start < end) {
                out[key] = { start, end };
            } else {
                out[key] = null;
            }
        }
    });
    return out;
}

// ─── Load & render tenants ───────────────────────────────────

async function loadTenants(offset) {
    if (offset !== undefined) _offset = offset;
    const container = document.getElementById('tenantsContainer');
    if (!container) return;
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = buildParams({
        offset: _offset,
        filters: { is_active: 'tenantsActiveFilter' },
    });

    try {
        const data = await api(`/admin/tenants?${params}`);
        const tenants = data.tenants || [];
        if (tenants.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('tenants.noTenants')}</div>`;
            renderPagination({ containerId: 'tenantsPagination', total: 0, offset: 0 });
            return;
        }
        container.innerHTML = `
            <div class="overflow-x-auto min-h-[480px]"><table class="${tw.table}" id="tenantsTable"><thead><tr>
                <th class="${tw.thSortable}" data-sortable>${t('tenants.slug')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('tenants.name')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('tenants.networkId')}</th>
                <th class="${tw.th}">${t('tenants.extensions')}</th>
                <th class="${tw.th}">${t('tenants.agentName')}</th>
                <th class="${tw.th}">${t('tenants.enabledTools')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('tenants.statusCol')}</th>
                <th class="${tw.th}">${t('tenants.actions')}</th>
            </tr></thead><tbody>
            ${tenants.map(tn => {
                const toolsCount = (tn.enabled_tools || []).length;
                const toolsBadge = toolsCount > 0
                    ? `<span class="${tw.badge}">${toolsCount} tools</span>`
                    : `<span class="${tw.mutedText} text-xs">${t('common.all')}</span>`;
                const statusBadge = tn.is_active
                    ? `<span class="${tw.badgeGreen}">${t('tenants.active')}</span>`
                    : `<span class="${tw.badgeRed}">${t('tenants.inactive')}</span>`;
                return `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}"><span class="font-mono text-xs">${escapeHtml(tn.slug)}</span></td>
                    <td class="${tw.td}">${escapeHtml(tn.name)}</td>
                    <td class="${tw.td}"><span class="font-mono text-xs">${escapeHtml(tn.network_id)}</span></td>
                    <td class="${tw.td}">${(tn.extensions || []).length > 0
                        ? (tn.extensions || []).map(e => `<span class="font-mono text-xs inline-block bg-neutral-100 dark:bg-neutral-800 rounded px-1.5 py-0.5 mr-1">${escapeHtml(e)}</span>`).join('')
                        : `<span class="${tw.mutedText} text-xs">—</span>`}</td>
                    <td class="${tw.td}">${escapeHtml(tn.agent_name || 'Олена')}</td>
                    <td class="${tw.td}">${toolsBadge}</td>
                    <td class="${tw.td}" data-sort-value="${tn.is_active ? 1 : 0}">${statusBadge}</td>
                    <td class="${tw.td}">
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-40 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.tenants.editTenant('${tn.id}')">${t('common.edit')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" data-id="${escapeHtml(tn.id)}" onclick="window._pages.tenants.editNetworkSettings(this.dataset.id)">${t('tenants.net.menu')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.tenants.toggleTenant('${tn.id}', ${tn.is_active})">${tn.is_active ? t('common.deactivate') : t('common.activate')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 cursor-pointer" data-id="${escapeHtml(tn.id)}" data-name="${escapeHtml(tn.name)}" onclick="window._pages.tenants.deleteTenant(this.dataset.id, this.dataset.name)">${t('common.delete')}</button>
                            </div>
                        </div>
                    </td>
                </tr>`;
            }).join('')}
            </tbody></table></div>
            <p class="${tw.mutedText} mt-2">${t('common.showing', {shown: tenants.length, total: data.total})}</p>`;

        makeSortable('tenantsTable');
        renderPagination({
            containerId: 'tenantsPagination',
            total: data.total,
            offset: _offset,
            onPage: (newOffset) => loadTenants(newOffset),
        });
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('tenants.loadFailed', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.tenants.loadTenants()">${t('common.retry')}</button></div>`;
    }
}

// ─── Create / Edit modal ─────────────────────────────────────

function renderToolCheckboxes(selectedTools) {
    const tools = _allTools.length > 0 ? _allTools : CANONICAL_TOOLS;
    const selected = new Set(selectedTools || []);
    return tools.map(name => `
        <label class="flex items-center gap-1.5 text-xs cursor-pointer">
            <input type="checkbox" value="${escapeHtml(name)}" ${selected.has(name) ? 'checked' : ''} class="tenant-tool-cb">
            <span class="font-mono">${escapeHtml(name)}</span>
        </label>
    `).join('');
}

async function showCreateTenant() {
    await loadToolNames();
    document.getElementById('tenantModalTitle').textContent = t('tenants.newTenant');
    document.getElementById('editTenantId').value = '';
    document.getElementById('tenantSlug').value = '';
    document.getElementById('tenantSlug').removeAttribute('readonly');
    document.getElementById('tenantName').value = '';
    document.getElementById('tenantNetworkId').value = '';
    document.getElementById('tenantExtensions').value = '';
    document.getElementById('tenantAgentName').value = 'Олена';
    document.getElementById('tenantGreeting').value = '';
    document.getElementById('tenantPromptSuffix').value = '';
    document.getElementById('tenantConfig').value = '{}';
    _editPolicyKeys = {};
    document.getElementById('tenantIsActive').checked = true;
    document.getElementById('tenantToolsContainer').innerHTML = renderToolCheckboxes([]);
    fillWorkingHours(null);
    await populateProviderOverrideSelect('');
    document.getElementById('tenantModal').classList.add('show');
}

async function editTenant(id) {
    await loadToolNames();
    try {
        const data = await api(`/admin/tenants/${id}`);
        const tn = data.tenant;
        document.getElementById('tenantModalTitle').textContent = t('tenants.editTenant');
        document.getElementById('editTenantId').value = id;
        document.getElementById('tenantSlug').value = tn.slug || '';
        document.getElementById('tenantSlug').setAttribute('readonly', 'readonly');
        document.getElementById('tenantName').value = tn.name || '';
        document.getElementById('tenantNetworkId').value = tn.network_id || '';
        document.getElementById('tenantExtensions').value = (tn.extensions || []).join(', ');
        document.getElementById('tenantAgentName').value = tn.agent_name || 'Олена';
        document.getElementById('tenantGreeting').value = tn.greeting || '';
        document.getElementById('tenantPromptSuffix').value = tn.prompt_suffix || '';
        // Split config: extract agent_provider_override into its dedicated
        // dropdown, keep the rest in the raw-JSON textarea for advanced use.
        const cfg = tn.config || {};
        const providerOverride = cfg.agent_provider_override || '';
        const restCfg = { ...cfg };
        delete restCfg.agent_provider_override;
        // sales_enabled / network_policy belong to the «Условия сети» form:
        // carried through this save unchanged, not shown in the raw JSON.
        _editPolicyKeys = {};
        for (const key of NET_CONFIG_KEYS) {
            if (key in restCfg) {
                _editPolicyKeys[key] = restCfg[key];
                delete restCfg[key];
            }
        }
        document.getElementById('tenantConfig').value = JSON.stringify(restCfg, null, 2);
        document.getElementById('tenantIsActive').checked = tn.is_active !== false;
        document.getElementById('tenantToolsContainer').innerHTML = renderToolCheckboxes(tn.enabled_tools || []);
        fillWorkingHours(tn.working_hours || null);
        await populateProviderOverrideSelect(providerOverride);
        document.getElementById('tenantModal').classList.add('show');
    } catch (e) {
        showToast(t('tenants.loadFailed', {error: e.message}), 'error');
    }
}

async function saveTenant() {
    const id = document.getElementById('editTenantId').value;
    const slug = document.getElementById('tenantSlug').value.trim();
    const name = document.getElementById('tenantName').value.trim();
    const network_id = document.getElementById('tenantNetworkId').value.trim();
    const agent_name = document.getElementById('tenantAgentName').value.trim() || 'Олена';
    const greeting = document.getElementById('tenantGreeting').value.trim() || null;
    const prompt_suffix = document.getElementById('tenantPromptSuffix').value.trim() || null;
    const is_active = document.getElementById('tenantIsActive').checked;

    let config;
    try {
        config = JSON.parse(document.getElementById('tenantConfig').value);
    } catch {
        showToast(t('tenants.invalidConfigJson'), 'error');
        return;
    }
    // Merge the provider-override dropdown into config. Empty value = remove
    // the key so the tenant falls back to normal task routing.
    const providerOverride = document.getElementById('tenantAgentProviderOverride').value.trim();
    if (providerOverride) {
        config.agent_provider_override = providerOverride;
    } else {
        delete config.agent_provider_override;
    }

    const enabled_tools = Array.from(document.querySelectorAll('.tenant-tool-cb:checked')).map(cb => cb.value);
    const extensionsRaw = document.getElementById('tenantExtensions').value.trim();
    const extensions = extensionsRaw ? extensionsRaw.split(',').map(e => e.trim()).filter(Boolean) : [];

    if (!name || !network_id) {
        showToast(t('tenants.nameRequired'), 'error');
        return;
    }
    if (!id && !slug) {
        showToast(t('tenants.slugRequired'), 'error');
        return;
    }

    // Put back the form-managed keys (a key typed into the raw JSON wins).
    for (const [key, value] of Object.entries(_editPolicyKeys)) {
        if (!(key in config)) config[key] = value;
    }

    const working_hours = readWorkingHoursFromForm();
    const body = { name, network_id, agent_name, greeting, enabled_tools, extensions, prompt_suffix, config, working_hours, is_active };
    if (!id) body.slug = slug;

    // A written policy (or sales on) without a service the tools serve makes
    // the bot refuse that service — the API wants it confirmed.
    const policy = config.network_policy;
    if ((policy && typeof policy === 'object') || config.sales_enabled === true) {
        const services = (policy && Array.isArray(policy.services)) ? policy.services.map(s => String(s).trim().toLowerCase()) : [];
        const missing = _uncoveredServices(services, enabled_tools);
        if (missing.length > 0) {
            const names = missing.map(s => t(`tenants.net.service.${s}`)).join(', ');
            if (!confirm(t('tenants.net.noServicesConfirm', { services: names }))) return;
            body.confirm_no_services = true;
        }
    }

    try {
        if (id) {
            await api(`/admin/tenants/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
            showToast(t('tenants.updated'));
        } else {
            await api('/admin/tenants', { method: 'POST', body: JSON.stringify(body) });
            showToast(t('tenants.created'));
        }
        closeModal('tenantModal');
        loadTenants(_offset);
    } catch (e) {
        showToast(t('tenants.saveFailed', {error: e.message}), 'error');
    }
}

async function toggleTenant(id, currentlyActive) {
    try {
        await api(`/admin/tenants/${id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !currentlyActive }) });
        showToast(currentlyActive ? t('tenants.deactivated') : t('tenants.activated'));
        loadTenants(_offset);
    } catch (e) {
        showToast(t('tenants.saveFailed', {error: e.message}), 'error');
    }
}

async function deleteTenant(id, name) {
    if (!confirm(t('tenants.deleteConfirm', {name}))) return;
    try {
        await api(`/admin/tenants/${id}`, { method: 'DELETE' });
        showToast(t('tenants.deleted'));
        loadTenants(_offset);
    } catch (e) {
        showToast(t('tenants.saveFailed', {error: e.message}), 'error');
    }
}

// ─── «Умови мережі» (config.sales_enabled + config.network_policy) ─────
// Enum values mirror src/agent/network_policy.py (DELIVERY_MODES,
// PAYMENT_LABELS, BANK_LABELS, SERVICE_LABELS, RECOMMEND_COUNT_MIN/MAX);
// the API rejects anything else with 422. Saved through
// PUT /admin/tenants/{id}/network-settings, which merges the two keys into
// config — store_api_url, excluded_station_ids, agent_provider_override and
// every other key stay as they are.

const NET_DELIVERY_MODES = ['free', 'carrier_tariff', 'unknown'];
const NET_PAYMENT_METHODS = ['cod', 'card', 'prepay', 'installments'];
const NET_BANKS = ['monobank', 'privatbank'];
const NET_SERVICES = ['fitting', 'storage'];
const NET_RECOMMEND_COUNTS = [2, 3];
// Same map as src/main.py _SERVICE_TOOLS: a service left out of the policy
// strips these tools and the guard refuses the service in live calls.
const NET_SERVICE_TOOLS = {
    fitting: ['get_fitting_stations', 'get_fitting_slots', 'reserve_fitting_slot', 'book_fitting',
        'cancel_fitting', 'get_fitting_price', 'get_customer_bookings'],
    storage: ['find_storage'],
};

// Input classes copied from the tenant modal (index.html) — never removed at
// runtime, so the dark-theme contrast stays intact.
const _netInput = 'w-full text-sm bg-white dark:bg-neutral-800 text-neutral-800 dark:text-neutral-200 border border-neutral-300 dark:border-neutral-600 rounded px-2 py-1.5 focus:outline-none focus:border-blue-500';
const _netLabel = 'block text-xs font-medium text-neutral-600 dark:text-neutral-400 mb-1';
const _netHint = 'text-xs text-neutral-500 dark:text-neutral-400 mt-0.5';
const _netSection = 'border-t border-neutral-200 dark:border-neutral-700 pt-3 space-y-2';

let _netState = { id: null, wasSalesEnabled: false, enabledTools: [], orderFinish: null, policy: {} };

function _netCheckboxes(name, values, labelPrefix) {
    return values.map(v => `
        <label class="flex items-center gap-1.5 text-xs cursor-pointer">
            <input type="checkbox" class="tn-net-${name}" value="${v}">
            <span>${t(`${labelPrefix}.${v}`)}</span>
        </label>`).join('');
}

function _ensureNetworkModal() {
    // Rebuilt on every open so the labels follow a language switch.
    const old = document.getElementById('tenantNetworkModal');
    if (old) old.remove();
    const el = document.createElement('div');
    el.id = 'tenantNetworkModal';
    el.className = 'modal-overlay fixed inset-0 bg-black/50 z-[100] justify-center items-center';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    // Only translated labels and fixed enum values are interpolated here;
    // tenant data is written through DOM properties in _fillNetworkForm.
    el.innerHTML = `
        <div class="bg-white dark:bg-neutral-900 border border-neutral-200 dark:border-neutral-800 rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-hidden flex flex-col">
            <div class="modal-fixed-header">
                <h2 class="text-base font-semibold text-neutral-900 dark:text-neutral-50" id="tnNetTitle"></h2>
            </div>
            <div class="modal-body">
            <div class="space-y-3">
                <p id="tnNetNotConfigured" class="hidden text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 rounded px-2 py-1.5">${t('tenants.net.notConfigured')}</p>
                <div>
                    <label class="flex items-center gap-2 text-sm cursor-pointer">
                        <input type="checkbox" id="tnNetSalesEnabled">
                        <span class="font-medium">${t('tenants.net.salesEnabled')}</span>
                    </label>
                    <p class="${_netHint}">${t('tenants.net.salesEnabledHint')}</p>
                    <p id="tnNetSalesWarning" class="hidden mt-1 text-xs text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 rounded px-2 py-1.5">${t('tenants.net.salesWarning')}</p>
                </div>
                <div class="${_netSection}">
                    <label class="${_netLabel}">${t('tenants.net.services')}</label>
                    <div class="flex flex-wrap gap-3">${_netCheckboxes('service', NET_SERVICES, 'tenants.net.service')}</div>
                    <p class="${_netHint}">${t('tenants.net.servicesHint')}</p>
                </div>
                <div class="${_netSection}">
                    <div>
                        <label class="${_netLabel}" for="tnNetDeliveryMode">${t('tenants.net.deliveryMode')}</label>
                        <select id="tnNetDeliveryMode" class="${_netInput}">
                            ${NET_DELIVERY_MODES.map(m => `<option value="${m}">${t(`tenants.net.delivery.${m}`)}</option>`).join('')}
                        </select>
                    </div>
                    <div>
                        <label class="${_netLabel}" for="tnNetCarriers">${t('tenants.net.carriers')}</label>
                        <input id="tnNetCarriers" type="text" class="${_netInput}">
                        <p class="${_netHint}">${t('tenants.net.commaHint')}</p>
                    </div>
                    <div>
                        <label class="${_netLabel}" for="tnNetEta">${t('tenants.net.eta')}</label>
                        <input id="tnNetEta" type="text" class="${_netInput}">
                    </div>
                    <label class="flex items-center gap-1.5 text-xs cursor-pointer">
                        <input type="checkbox" id="tnNetPickup">
                        <span>${t('tenants.net.pickup')}</span>
                    </label>
                </div>
                <div class="${_netSection}">
                    <label class="${_netLabel}">${t('tenants.net.payment')}</label>
                    <div class="flex flex-wrap gap-3">${_netCheckboxes('payment', NET_PAYMENT_METHODS, 'tenants.net.pay')}</div>
                    <div>
                        <label class="${_netLabel}" for="tnNetCodFee">${t('tenants.net.codFee')}</label>
                        <input id="tnNetCodFee" type="text" class="${_netInput}">
                    </div>
                    <div>
                        <label class="${_netLabel}">${t('tenants.net.banks')}</label>
                        <div class="flex flex-wrap gap-3">${_netCheckboxes('bank', NET_BANKS, 'tenants.net.bank')}</div>
                    </div>
                </div>
                <div class="${_netSection}">
                    <div>
                        <label class="${_netLabel}" for="tnNetWarranty">${t('tenants.net.warranty')}</label>
                        <input id="tnNetWarranty" type="text" class="${_netInput}">
                        <p class="${_netHint}">${t('tenants.net.warrantyHint')}</p>
                    </div>
                    <div>
                        <label class="${_netLabel}" for="tnNetBrandPriority">${t('tenants.net.brandPriority')}</label>
                        <input id="tnNetBrandPriority" type="text" class="${_netInput}">
                        <p class="${_netHint}">${t('tenants.net.brandPriorityHint')}</p>
                    </div>
                    <div>
                        <label class="${_netLabel}" for="tnNetRecommendCount">${t('tenants.net.recommendCount')}</label>
                        <select id="tnNetRecommendCount" class="${_netInput}">
                            ${NET_RECOMMEND_COUNTS.map(n => `<option value="${n}">${n}</option>`).join('')}
                        </select>
                    </div>
                </div>
            </div>
            <div class="flex justify-end gap-2 mt-4">
                <button onclick="window._pages.tenants.closeNetworkSettings()" class="px-3 py-1.5 text-sm border border-neutral-300 dark:border-neutral-600 rounded-md text-neutral-700 dark:text-neutral-300 hover:bg-neutral-100 dark:hover:bg-neutral-800 cursor-pointer">${t('common.cancel')}</button>
                <button onclick="window._pages.tenants.saveNetworkSettings()" class="px-3 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 cursor-pointer">${t('common.save')}</button>
            </div>
            </div>
        </div>`;
    document.body.appendChild(el);
    el.querySelector('#tnNetSalesEnabled').addEventListener('change', (e) => {
        document.getElementById('tnNetSalesWarning').classList.toggle('hidden', !e.target.checked);
    });
}

function _setChecked(name, values) {
    const set = new Set((Array.isArray(values) ? values : []).map(v => String(v).trim().toLowerCase()));
    document.querySelectorAll(`#tenantNetworkModal .tn-net-${name}`).forEach(cb => {
        cb.checked = set.has(cb.value);
    });
}

function _getChecked(name) {
    return Array.from(document.querySelectorAll(`#tenantNetworkModal .tn-net-${name}:checked`)).map(cb => cb.value);
}

function _joinList(values) {
    return Array.isArray(values) ? values.filter(v => typeof v === 'string').join(', ') : '';
}

function _splitList(value) {
    const out = [];
    for (const part of value.split(',')) {
        const item = part.trim();
        if (item && !out.includes(item)) out.push(item);
    }
    return out;
}

function _fillNetworkForm(tn) {
    const cfg = (tn.config && typeof tn.config === 'object') ? tn.config : {};
    const configured = cfg.network_policy && typeof cfg.network_policy === 'object' && !Array.isArray(cfg.network_policy);
    const p = configured ? cfg.network_policy : {};
    const sales = cfg.sales_enabled === true;
    _netState = {
        id: tn.id,
        wasSalesEnabled: sales,
        enabledTools: tn.enabled_tools || [],
        orderFinish: typeof p.order_finish === 'string' ? p.order_finish : null,
        // Keys the form does not edit (warranty/returns/tracking texts, …):
        // sent back untouched, so saving the form never drops them.
        policy: { ...p },
    };

    document.getElementById('tnNetTitle').textContent = t('tenants.net.title', { name: tn.name || tn.slug || '' });
    document.getElementById('tnNetNotConfigured').classList.toggle('hidden', !!configured);
    document.getElementById('tnNetSalesEnabled').checked = sales;
    document.getElementById('tnNetSalesWarning').classList.toggle('hidden', !sales);
    _setChecked('service', p.services);
    document.getElementById('tnNetDeliveryMode').value = NET_DELIVERY_MODES.includes(p.delivery_mode) ? p.delivery_mode : 'unknown';
    document.getElementById('tnNetCarriers').value = _joinList(p.delivery_carriers);
    document.getElementById('tnNetEta').value = typeof p.delivery_eta_text === 'string' ? p.delivery_eta_text : '';
    document.getElementById('tnNetPickup').checked = p.pickup_available === true;
    _setChecked('payment', p.payment_methods);
    document.getElementById('tnNetCodFee').value = typeof p.cod_fee_text === 'string' ? p.cod_fee_text : '';
    _setChecked('bank', p.installment_banks);
    document.getElementById('tnNetWarranty').value = _joinList(p.extended_warranty_brands);
    document.getElementById('tnNetBrandPriority').value = _joinList(p.brand_priority);
    const count = NET_RECOMMEND_COUNTS.includes(p.recommend_count) ? p.recommend_count : Math.max(...NET_RECOMMEND_COUNTS);
    document.getElementById('tnNetRecommendCount').value = String(count);
}

function _readNetworkForm() {
    const policy = {
        ...(_netState.policy || {}),
        services: _getChecked('service'),
        delivery_mode: document.getElementById('tnNetDeliveryMode').value,
        delivery_carriers: _splitList(document.getElementById('tnNetCarriers').value),
        delivery_eta_text: document.getElementById('tnNetEta').value.trim() || null,
        pickup_available: document.getElementById('tnNetPickup').checked,
        payment_methods: _getChecked('payment'),
        cod_fee_text: document.getElementById('tnNetCodFee').value.trim() || null,
        installment_banks: _getChecked('bank'),
        extended_warranty_brands: _splitList(document.getElementById('tnNetWarranty').value),
        brand_priority: _splitList(document.getElementById('tnNetBrandPriority').value),
        recommend_count: parseInt(document.getElementById('tnNetRecommendCount').value, 10),
    };
    // The form has no field for order_finish (one mode so far) — keep it.
    if (_netState.orderFinish) policy.order_finish = _netState.orderFinish;
    return {
        sales_enabled: document.getElementById('tnNetSalesEnabled').checked,
        network_policy: policy,
    };
}

function _uncoveredServices(services, enabledTools) {
    const tools = new Set(enabledTools || []);
    const all = tools.size === 0;  // empty enabled_tools = every tool
    return NET_SERVICES.filter(s => !services.includes(s)
        && (all || NET_SERVICE_TOOLS[s].some(name => tools.has(name))));
}

async function editNetworkSettings(id) {
    try {
        const data = await api(`/admin/tenants/${id}`);
        _ensureNetworkModal();
        _fillNetworkForm(data.tenant);
        showModal('tenantNetworkModal');
    } catch (e) {
        showToast(t('tenants.loadFailed', { error: e.message }), 'error');
    }
}

function closeNetworkSettings() {
    closeModal('tenantNetworkModal');
}

async function saveNetworkSettings() {
    const id = _netState.id;
    if (!id) return;
    const body = _readNetworkForm();

    if (body.sales_enabled && !_netState.wasSalesEnabled) {
        if (!confirm(t('tenants.net.salesConfirm'))) return;
    }
    const missing = _uncoveredServices(body.network_policy.services, _netState.enabledTools);
    if (missing.length > 0) {
        const names = missing.map(s => t(`tenants.net.service.${s}`)).join(', ');
        if (!confirm(t('tenants.net.noServicesConfirm', { services: names }))) return;
        body.confirm_no_services = true;
    }

    try {
        await api(`/admin/tenants/${id}/network-settings`, { method: 'PUT', body: JSON.stringify(body) });
        showToast(t('tenants.net.saved'));
        closeModal('tenantNetworkModal');
        loadTenants(_offset);
    } catch (e) {
        showToast(t('tenants.saveFailed', { error: e.message }), 'error');
    }
}

// ─── Init & exports ──────────────────────────────────────────

export function init() {
    registerPageLoader('tenants', () => loadTenants());
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.relative.inline-block')) {
            document.querySelectorAll('#page-tenants .relative.inline-block > div:not(.hidden)').forEach(m => m.classList.add('hidden'));
        }
    });
    // Toggle working-hours block visibility
    document.addEventListener('change', (e) => {
        if (e.target && e.target.id === 'tenantWorkingHoursEnabled') {
            document.getElementById('tenantWorkingHoursBody').classList.toggle('hidden', !e.target.checked);
        }
    });
}

window._pages = window._pages || {};
window._pages.tenants = {
    loadTenants, showCreateTenant, editTenant, saveTenant,
    toggleTenant, deleteTenant,
    editNetworkSettings, saveNetworkSettings, closeNetworkSettings,
};
