import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import { renderPagination, buildParams } from '../pagination.js';
import * as tw from '../tw.js';

// ─── State ───────────────────────────────────────────────────
let _offset = 0;
let _allTools = [];

// Canonical tool names (fallback if API unavailable)
const CANONICAL_TOOLS = [
    'get_vehicle_tire_sizes', 'search_tires', 'check_availability',
    'transfer_to_operator', 'get_order_status', 'create_order_draft',
    'update_order_delivery', 'confirm_order', 'get_fitting_stations',
    'get_fitting_slots', 'book_fitting', 'search_knowledge_base',
];

async function loadToolNames() {
    if (_allTools.length > 0) return;
    try {
        const data = await api('/admin/training/tools');
        _allTools = (data.tools || []).map(t => t.name);
    } catch {
        _allTools = CANONICAL_TOOLS;
    }
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
            <div class="overflow-x-auto"><table class="${tw.table}" id="tenantsTable"><thead><tr>
                <th class="${tw.thSortable}" data-sortable>${t('tenants.slug')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('tenants.name')}</th>
                <th class="${tw.thSortable}" data-sortable>${t('tenants.networkId')}</th>
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
                    <td class="${tw.td}">${escapeHtml(tn.agent_name || 'Олена')}</td>
                    <td class="${tw.td}">${toolsBadge}</td>
                    <td class="${tw.td}" data-sort-value="${tn.is_active ? 1 : 0}">${statusBadge}</td>
                    <td class="${tw.td}">
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-40 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.tenants.editTenant('${tn.id}')">${t('common.edit')}</button>
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
    document.getElementById('tenantAgentName').value = 'Олена';
    document.getElementById('tenantGreeting').value = '';
    document.getElementById('tenantPromptSuffix').value = '';
    document.getElementById('tenantConfig').value = '{}';
    document.getElementById('tenantIsActive').checked = true;
    document.getElementById('tenantToolsContainer').innerHTML = renderToolCheckboxes([]);
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
        document.getElementById('tenantAgentName').value = tn.agent_name || 'Олена';
        document.getElementById('tenantGreeting').value = tn.greeting || '';
        document.getElementById('tenantPromptSuffix').value = tn.prompt_suffix || '';
        document.getElementById('tenantConfig').value = JSON.stringify(tn.config || {}, null, 2);
        document.getElementById('tenantIsActive').checked = tn.is_active !== false;
        document.getElementById('tenantToolsContainer').innerHTML = renderToolCheckboxes(tn.enabled_tools || []);
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

    const enabled_tools = Array.from(document.querySelectorAll('.tenant-tool-cb:checked')).map(cb => cb.value);

    if (!name || !network_id) {
        showToast(t('tenants.nameRequired'), 'error');
        return;
    }
    if (!id && !slug) {
        showToast(t('tenants.slugRequired'), 'error');
        return;
    }

    const body = { name, network_id, agent_name, greeting, enabled_tools, prompt_suffix, config, is_active };
    if (!id) body.slug = slug;

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

// ─── Init & exports ──────────────────────────────────────────

export function init() {
    registerPageLoader('tenants', () => loadTenants());
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.relative')) {
            document.querySelectorAll('#page-tenants .relative > div:not(.hidden)').forEach(m => m.classList.add('hidden'));
        }
    });
}

window._pages = window._pages || {};
window._pages.tenants = {
    loadTenants, showCreateTenant, editTenant, saveTenant,
    toggleTenant, deleteTenant,
};
