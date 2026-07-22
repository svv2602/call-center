import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, formatDate, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import { renderPagination, buildParams } from '../pagination.js';
import { hasPermission } from '../auth.js';
import * as tw from '../tw.js';

let _customersOffset = 0;
let _sortBy = 'last_call_at';
let _sortDir = 'desc';
let _tenantsCache = null;

async function loadTenantOptions() {
    if (_tenantsCache !== null) return _tenantsCache;
    try {
        const data = await api('/admin/tenants?limit=100');
        _tenantsCache = data.tenants || [];
    } catch {
        _tenantsCache = [];
    }
    return _tenantsCache;
}

async function loadCustomers(offset = 0) {
    _customersOffset = offset;
    const loading = document.getElementById('customersLoading');
    const tbody = document.querySelector('#customersTable tbody');
    loading.style.display = 'flex';

    try {
        const includeDeleted = document.getElementById('customersIncludeDeleted')?.checked;
        const params = buildParams({
            offset: _customersOffset,
            filters: { search: 'customersSearch' },
        });
        params.set('sort_by', _sortBy);
        params.set('sort_dir', _sortDir);
        if (includeDeleted) params.set('include_deleted', 'true');

        const data = await api(`/admin/customers?${params.toString()}`);
        loading.style.display = 'none';
        const customers = data.customers || [];
        const canWrite = hasPermission('customers:write');
        const canDelete = hasPermission('customers:delete');

        if (customers.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="${tw.emptyState}">${t('customers.noCustomers')}</td></tr>`;
            renderPagination({ containerId: 'customersPagination', total: 0, offset: 0, onPage: loadCustomers });
            return;
        }

        tbody.innerHTML = customers.map(c => {
            const vehicleCount = Array.isArray(c.vehicles) ? c.vehicles.length : 0;
            const isDeleted = !!c.deleted_at;
            const rowClass = isDeleted ? 'opacity-50' : '';
            const nameCell = isDeleted
                ? `${escapeHtml(c.name || '-')} <span class="${tw.badgeRed} ml-1">${t('customers.deleted')}</span>`
                : escapeHtml(c.name || '-');
            const actions = [];
            if (canWrite && !isDeleted) {
                actions.push(`<button class="text-blue-600 dark:text-blue-400 hover:underline mr-2" onclick="event.stopPropagation(); window._pages.customers.editCustomer('${escapeHtml(c.id)}')">${t('common.edit')}</button>`);
            }
            if (canDelete) {
                if (isDeleted) {
                    actions.push(`<button class="text-green-600 dark:text-green-400 hover:underline" onclick="event.stopPropagation(); window._pages.customers.restoreCustomer('${escapeHtml(c.id)}')">${t('customers.restore')}</button>`);
                } else {
                    actions.push(`<button class="text-red-600 dark:text-red-400 hover:underline" onclick="event.stopPropagation(); window._pages.customers.deleteCustomer('${escapeHtml(c.id)}', '${escapeHtml(c.phone)}')">${t('common.delete')}</button>`);
                }
            }
            const actionsCell = `<td class="${tw.td}" data-label="${t('common.actions')}" onclick="event.stopPropagation()">${actions.join('') || '<span class="text-neutral-400">—</span>'}</td>`;
            return `
            <tr class="${tw.trHover} cursor-pointer ${rowClass}" onclick="window._pages.customers.showDetail('${escapeHtml(c.id)}')">
                <td class="${tw.td}" data-label="${t('customers.phone')}">${escapeHtml(c.phone)}</td>
                <td class="${tw.td}" data-label="${t('customers.name')}">${nameCell}</td>
                <td class="${tw.td}" data-label="${t('customers.city')}">${escapeHtml(c.city || '-')}</td>
                <td class="${tw.td}" data-label="${t('customers.vehicles')}">${vehicleCount}</td>
                <td class="${tw.td}" data-label="${t('customers.totalCalls')}">${c.total_calls || 0}</td>
                <td class="${tw.td}" data-label="${t('customers.lastCall')}" data-sort-value="${c.last_call_at || ''}">${formatDate(c.last_call_at)}</td>
                ${actionsCell}
            </tr>`;
        }).join('');

        makeSortable('customersTable');

        renderPagination({
            containerId: 'customersPagination',
            total: data.total,
            offset: _customersOffset,
            onPage: loadCustomers,
        });
    } catch (e) {
        loading.style.display = 'none';
        tbody.innerHTML = `<tr><td colspan="7" class="${tw.emptyState}">${t('customers.loadFailed', { error: escapeHtml(e.message) })}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.customers.loadCustomers()">${t('common.retry')}</button></td></tr>`;
    }
}

async function showDetail(id) {
    const body = document.getElementById('customerDetailBody');
    body.innerHTML = `<div class="flex justify-center py-8"><div class="spinner"></div></div>`;
    document.getElementById('customerDetailModal').classList.add('show');

    try {
        const data = await api(`/admin/customers/${id}`);
        const c = data.customer;
        const calls = data.recent_calls || [];
        const vehicles = Array.isArray(c.vehicles) ? c.vehicles : [];
        const canWrite = hasPermission('customers:write');
        const canDelete = hasPermission('customers:delete');
        const isDeleted = !!c.deleted_at;

        let vehiclesHtml;
        if (vehicles.length === 0) {
            vehiclesHtml = `<p class="${tw.mutedText}">${t('customers.noVehicles')}</p>`;
        } else {
            vehiclesHtml = `<table class="w-full text-sm mb-2">
                <thead><tr>
                    <th class="${tw.th}">${t('customers.vehiclePlate')}</th>
                    <th class="${tw.th}">${t('customers.vehicleModel')}</th>
                    <th class="${tw.th}">${t('customers.vehicleTires')}</th>
                </tr></thead>
                <tbody>${vehicles.map(v => `<tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(v.plate || '-')}</td>
                    <td class="${tw.td}">${escapeHtml(v.brand || v.model || '-')}</td>
                    <td class="${tw.td}">${escapeHtml(v.tire_size || '-')}</td>
                </tr>`).join('')}</tbody>
            </table>`;
        }

        let callsHtml;
        if (calls.length === 0) {
            callsHtml = `<p class="${tw.mutedText}">${t('customers.noRecentCalls')}</p>`;
        } else {
            callsHtml = `<table class="w-full text-sm">
                <thead><tr>
                    <th class="${tw.th}">${t('customers.callDate')}</th>
                    <th class="${tw.th}">${t('customers.callDuration')}</th>
                    <th class="${tw.th}">${t('customers.callScenario')}</th>
                    <th class="${tw.th}">${t('customers.callTransferred')}</th>
                </tr></thead>
                <tbody>${calls.map(cl => `<tr class="${tw.trHover}">
                    <td class="${tw.td}">${formatDate(cl.started_at)}</td>
                    <td class="${tw.td}">${cl.duration_seconds != null ? cl.duration_seconds + 's' : '-'}</td>
                    <td class="${tw.td}">${escapeHtml(cl.scenario || '-')}</td>
                    <td class="${tw.td}">${cl.transferred_to_operator ? `<span class="${tw.badgeYellow}">${t('common.yes')}</span>` : `<span class="${tw.badgeGreen}">${t('common.no')}</span>`}</td>
                </tr>`).join('')}</tbody>
            </table>`;
        }

        const deletedBanner = isDeleted
            ? `<div class="p-2 mb-3 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 rounded text-sm text-red-700 dark:text-red-300">${t('customers.deletedNotice', { at: formatDate(c.deleted_at) })}</div>`
            : '';

        const actionBtns = [];
        if (canWrite && !isDeleted) {
            actionBtns.push(`<button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.customers.editCustomer('${escapeHtml(c.id)}')">${t('common.edit')}</button>`);
        }
        if (canDelete) {
            if (isDeleted) {
                actionBtns.push(`<button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.customers.restoreCustomer('${escapeHtml(c.id)}')">${t('customers.restore')}</button>`);
            } else {
                actionBtns.push(`<button class="${tw.btnDanger} ${tw.btnSm}" onclick="window._pages.customers.deleteCustomer('${escapeHtml(c.id)}', '${escapeHtml(c.phone)}')">${t('common.delete')}</button>`);
            }
        }
        actionBtns.push(`<button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._app.closeModal('customerDetailModal')">${t('common.close')}</button>`);

        body.innerHTML = `
            <div class="space-y-4 p-4">
                ${deletedBanner}
                <div class="grid grid-cols-2 gap-3">
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.phone')}</span><div class="font-medium">${escapeHtml(c.phone)}</div></div>
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.name')}</span><div class="font-medium">${escapeHtml(c.name || '-')}</div></div>
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.city')}</span><div class="font-medium">${escapeHtml(c.city || '-')}</div></div>
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.deliveryAddress')}</span><div class="font-medium">${escapeHtml(c.delivery_address || t('customers.noAddress'))}</div></div>
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.tenant')}</span><div class="font-medium">${escapeHtml(c.tenant_name || '-')}</div></div>
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.totalCalls')}</span><div class="font-medium">${c.total_calls || 0}</div></div>
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.firstCall')}</span><div class="font-medium">${formatDate(c.first_call_at)}</div></div>
                </div>
                <div>
                    <h3 class="text-sm font-semibold text-neutral-900 dark:text-neutral-50 mb-2">${t('customers.vehicles')}</h3>
                    ${vehiclesHtml}
                </div>
                <div>
                    <h3 class="text-sm font-semibold text-neutral-900 dark:text-neutral-50 mb-2">${t('customers.recentCalls')}</h3>
                    ${callsHtml}
                </div>
                <div class="flex justify-end gap-2 pt-2">
                    ${actionBtns.join('')}
                </div>
            </div>`;
    } catch (e) {
        body.innerHTML = `<div class="${tw.emptyState} p-4">${t('customers.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

async function editCustomer(id) {
    const tenants = await loadTenantOptions();
    try {
        const data = await api(`/admin/customers/${id}`);
        const c = data.customer;

        document.getElementById('editCustomerId').value = c.id;
        document.getElementById('editCustomerPhone').value = c.phone || '';
        document.getElementById('editCustomerName').value = c.name || '';
        document.getElementById('editCustomerCity').value = c.city || '';
        document.getElementById('editCustomerAddress').value = c.delivery_address || '';
        document.getElementById('editCustomerVehicles').value = JSON.stringify(c.vehicles || [], null, 2);

        const tenantSel = document.getElementById('editCustomerTenant');
        tenantSel.innerHTML = tenants.map(tn =>
            `<option value="${escapeHtml(tn.id)}" ${tn.id === c.tenant_id ? 'selected' : ''}>${escapeHtml(tn.name)} (${escapeHtml(tn.slug)})</option>`
        ).join('');

        closeModal('customerDetailModal');
        document.getElementById('customerEditModal').classList.add('show');
    } catch (e) {
        showToast(t('customers.loadFailed', { error: e.message }), 'error');
    }
}

async function saveCustomer() {
    const id = document.getElementById('editCustomerId').value;
    const phone = document.getElementById('editCustomerPhone').value.trim();
    const name = document.getElementById('editCustomerName').value.trim();
    const city = document.getElementById('editCustomerCity').value.trim();
    const delivery_address = document.getElementById('editCustomerAddress').value.trim();
    const tenant_id = document.getElementById('editCustomerTenant').value;
    const vehiclesRaw = document.getElementById('editCustomerVehicles').value.trim();

    if (!phone) {
        showToast(t('customers.phoneRequired'), 'error');
        return;
    }

    let vehicles;
    try {
        vehicles = vehiclesRaw ? JSON.parse(vehiclesRaw) : [];
        if (!Array.isArray(vehicles)) throw new Error('not an array');
    } catch {
        showToast(t('customers.invalidVehiclesJson'), 'error');
        return;
    }

    const body = {
        phone,
        name: name || null,
        city: city || null,
        delivery_address: delivery_address || null,
        tenant_id: tenant_id || null,
        vehicles,
    };

    try {
        await api(`/admin/customers/${id}`, { method: 'PATCH', body: JSON.stringify(body) });
        showToast(t('customers.updated'));
        closeModal('customerEditModal');
        loadCustomers(_customersOffset);
    } catch (e) {
        showToast(t('customers.saveFailed', { error: e.message }), 'error');
    }
}

async function deleteCustomer(id, phone) {
    if (!confirm(t('customers.deleteConfirm', { phone }))) return;
    try {
        await api(`/admin/customers/${id}`, { method: 'DELETE' });
        showToast(t('customers.deleted'));
        closeModal('customerDetailModal');
        loadCustomers(_customersOffset);
    } catch (e) {
        showToast(t('customers.saveFailed', { error: e.message }), 'error');
    }
}

async function restoreCustomer(id) {
    try {
        await api(`/admin/customers/${id}/restore`, { method: 'POST' });
        showToast(t('customers.restored'));
        closeModal('customerDetailModal');
        loadCustomers(_customersOffset);
    } catch (e) {
        showToast(t('customers.saveFailed', { error: e.message }), 'error');
    }
}

export function init() {
    registerPageLoader('customers', () => loadCustomers());
}

window._pages = window._pages || {};
window._pages.customers = {
    loadCustomers, showDetail, editCustomer, saveCustomer,
    deleteCustomer, restoreCustomer,
};
