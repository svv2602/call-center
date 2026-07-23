import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, formatDate } from '../utils.js';
import { registerPageLoader, setRefreshTimer } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import * as tw from '../tw.js';

let _tenantsLoaded = false;

async function loadTenantOptions() {
    if (_tenantsLoaded) return;
    try {
        const data = await api('/admin/tenants?is_active=true');
        const select = document.getElementById('callbacksFilterTenant');
        if (!select) return;
        const opts = (data.tenants || []).map(tn => `<option value="${escapeHtml(tn.id)}">${escapeHtml(tn.name)}</option>`);
        select.insertAdjacentHTML('beforeend', opts.join(''));
        _tenantsLoaded = true;
    } catch { /* silently ignore — filter still works with empty tenant list */ }
}

function _statusBadge(status) {
    const key = `callbacks.status_${status}`;
    const label = t(key) || status;
    let color;
    switch (status) {
        case 'pending': color = 'bg-yellow-100 text-yellow-800 dark:bg-yellow-950/50 dark:text-yellow-300'; break;
        case 'in_progress': color = 'bg-blue-100 text-blue-800 dark:bg-blue-950/50 dark:text-blue-300'; break;
        case 'done': color = 'bg-green-100 text-green-800 dark:bg-green-950/50 dark:text-green-300'; break;
        case 'cancelled': color = 'bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400'; break;
        default: color = 'bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300';
    }
    return `<span class="inline-block px-2 py-0.5 rounded text-xs font-medium ${color}">${escapeHtml(label)}</span>`;
}

async function loadCallbacks() {
    const loading = document.getElementById('callbacksLoading');
    const tbody = document.querySelector('#callbacksTable tbody');
    if (!tbody) return;
    loading.style.display = 'flex';

    const status = document.getElementById('callbacksFilterStatus')?.value || '';
    const tenantId = document.getElementById('callbacksFilterTenant')?.value || '';

    const params = new URLSearchParams({ limit: '200' });
    if (status) params.set('status', status);
    if (tenantId) params.set('tenant_id', tenantId);

    try {
        const data = await api(`/admin/callbacks?${params}`);
        loading.style.display = 'none';
        const callbacks = data.callbacks || [];
        if (callbacks.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="${tw.emptyState}">${t('callbacks.empty')}</td></tr>`;
            return;
        }
        tbody.innerHTML = callbacks.map(c => {
            const canAdvance = c.status !== 'done' && c.status !== 'cancelled';
            return `
            <tr class="${tw.trHover}">
                <td class="${tw.td}" data-label="${t('callbacks.created')}">${escapeHtml(formatDate(c.created_at))}</td>
                <td class="${tw.td}" data-label="${t('callbacks.tenant')}">${escapeHtml(c.tenant_name || c.tenant_slug || '—')}</td>
                <td class="${tw.td} font-mono" data-label="${t('callbacks.phone')}">${escapeHtml(c.phone)}</td>
                <td class="${tw.td}" data-label="${t('callbacks.preferredTime')}">${escapeHtml(c.preferred_time || '—')}</td>
                <td class="${tw.td}" data-label="${t('callbacks.note')}">${escapeHtml(c.note || '')}</td>
                <td class="${tw.td}" data-label="${t('callbacks.status')}">${_statusBadge(c.status)}</td>
                <td class="${tw.tdActions}">
                    <div class="relative inline-block">
                        <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                        <div class="hidden absolute right-0 z-20 mt-1 w-52 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                            ${canAdvance ? `
                            <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.callbacks.markStatus('${c.id}', 'in_progress')">${t('callbacks.markInProgress')}</button>
                            <button class="w-full text-left px-3 py-1.5 text-xs text-green-700 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-950/30 cursor-pointer" onclick="window._pages.callbacks.markDone('${c.id}')">${t('callbacks.markDone')}</button>
                            <button class="w-full text-left px-3 py-1.5 text-xs text-neutral-600 dark:text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.callbacks.markStatus('${c.id}', 'cancelled')">${t('callbacks.markCancelled')}</button>
                            ` : `<div class="px-3 py-1 text-xs text-neutral-400">${t('callbacks.noActions')}</div>`}
                        </div>
                    </div>
                </td>
            </tr>
        `;
        }).join('');
        makeSortable('callbacksTable');
    } catch (e) {
        loading.style.display = 'none';
        tbody.innerHTML = `<tr><td colspan="7" class="${tw.emptyState}">${t('callbacks.failedToLoad', {error: escapeHtml(e.message)})}</td></tr>`;
    }
}

async function markStatus(id, status) {
    try {
        await api(`/admin/callbacks/${id}`, { method: 'PATCH', body: JSON.stringify({ status }) });
        showToast(t('callbacks.statusUpdated'));
        loadCallbacks();
    } catch (e) { showToast(t('callbacks.updateFailed', {error: e.message}), 'error'); }
}

async function markDone(id) {
    const note = prompt(t('callbacks.noteResultPrompt'));
    if (note === null) return; // user cancelled
    try {
        await api(`/admin/callbacks/${id}`, {
            method: 'PATCH',
            body: JSON.stringify({ status: 'done', note_result: note.trim() || null }),
        });
        showToast(t('callbacks.statusUpdated'));
        loadCallbacks();
    } catch (e) { showToast(t('callbacks.updateFailed', {error: e.message}), 'error'); }
}

export function init() {
    registerPageLoader('callbacks', () => {
        loadTenantOptions();
        loadCallbacks();
        setRefreshTimer(() => loadCallbacks(), 30000);
    });
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.relative.inline-block')) {
            document.querySelectorAll('#page-callbacks .relative.inline-block > div:not(.hidden)').forEach(m => m.classList.add('hidden'));
        }
    });
}

window._pages = window._pages || {};
window._pages.callbacks = { loadCallbacks, markStatus, markDone };
