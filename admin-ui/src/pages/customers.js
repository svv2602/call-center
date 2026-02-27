import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, formatDate } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import { renderPagination, buildParams } from '../pagination.js';
import * as tw from '../tw.js';

let _customersOffset = 0;
let _sortBy = 'last_call_at';
let _sortDir = 'desc';

async function loadCustomers(offset = 0) {
    _customersOffset = offset;
    const loading = document.getElementById('customersLoading');
    const tbody = document.querySelector('#customersTable tbody');
    loading.style.display = 'flex';

    try {
        const params = buildParams({
            offset: _customersOffset,
            filters: { search: 'customersSearch' },
        });
        params.set('sort_by', _sortBy);
        params.set('sort_dir', _sortDir);

        const data = await api(`/admin/customers?${params.toString()}`);
        loading.style.display = 'none';
        const customers = data.customers || [];

        if (customers.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" class="${tw.emptyState}">${t('customers.noCustomers')}</td></tr>`;
            renderPagination({ containerId: 'customersPagination', total: 0, offset: 0, onPage: loadCustomers });
            return;
        }

        tbody.innerHTML = customers.map(c => {
            const vehicleCount = Array.isArray(c.vehicles) ? c.vehicles.length : 0;
            return `
            <tr class="${tw.trHover} cursor-pointer" onclick="window._pages.customers.showDetail('${escapeHtml(c.id)}')">
                <td class="${tw.td}" data-label="${t('customers.phone')}">${escapeHtml(c.phone)}</td>
                <td class="${tw.td}" data-label="${t('customers.name')}">${escapeHtml(c.name || '-')}</td>
                <td class="${tw.td}" data-label="${t('customers.city')}">${escapeHtml(c.city || '-')}</td>
                <td class="${tw.td}" data-label="${t('customers.vehicles')}">${vehicleCount}</td>
                <td class="${tw.td}" data-label="${t('customers.totalCalls')}">${c.total_calls || 0}</td>
                <td class="${tw.td}" data-label="${t('customers.lastCall')}" data-sort-value="${c.last_call_at || ''}">${formatDate(c.last_call_at)}</td>
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
        tbody.innerHTML = `<tr><td colspan="6" class="${tw.emptyState}">${t('customers.loadFailed', { error: escapeHtml(e.message) })}
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

        body.innerHTML = `
            <div class="space-y-4 p-4">
                <div class="grid grid-cols-2 gap-3">
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.phone')}</span><div class="font-medium">${escapeHtml(c.phone)}</div></div>
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.name')}</span><div class="font-medium">${escapeHtml(c.name || '-')}</div></div>
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.city')}</span><div class="font-medium">${escapeHtml(c.city || '-')}</div></div>
                    <div><span class="text-xs text-neutral-500 dark:text-neutral-400">${t('customers.deliveryAddress')}</span><div class="font-medium">${escapeHtml(c.delivery_address || t('customers.noAddress'))}</div></div>
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
                <div class="flex justify-end pt-2">
                    <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._app.closeModal('customerDetailModal')">${t('common.close')}</button>
                </div>
            </div>`;
    } catch (e) {
        body.innerHTML = `<div class="${tw.emptyState} p-4">${t('customers.loadFailed', { error: escapeHtml(e.message) })}</div>`;
    }
}

export function init() {
    registerPageLoader('customers', () => loadCustomers());
}

window._pages = window._pages || {};
window._pages.customers = { loadCustomers, showDetail };
