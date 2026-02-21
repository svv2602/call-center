import { api } from '../api.js';
import { formatDate, escapeHtml } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import * as tw from '../tw.js';

let auditOffset = 0;

async function loadAuditLog(offset = 0) {
    auditOffset = offset;
    const container = document.getElementById('auditContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;

    const params = new URLSearchParams({ limit: 50, offset });
    const df = document.getElementById('auditDateFrom').value;
    const dt = document.getElementById('auditDateTo').value;
    const action = document.getElementById('auditAction').value;
    if (df) params.set('date_from', df);
    if (dt) params.set('date_to', dt);
    if (action) params.set('action', action);

    try {
        const data = await api(`/admin/audit-log?${params}`);
        const entries = data.entries || [];
        if (entries.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('audit.noEntries')}</div>`;
            document.getElementById('auditPagination').innerHTML = '';
            return;
        }
        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}" id="auditTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('audit.date')}</th><th class="${tw.thSortable}" data-sortable>${t('audit.user')}</th><th class="${tw.thSortable}" data-sortable>${t('audit.action')}</th><th class="${tw.thSortable}" data-sortable>${t('audit.resource')}</th><th class="${tw.th}">${t('audit.ip')}</th></tr></thead><tbody>
            ${entries.map(e => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}" data-label="${t('audit.date')}" data-sort-value="${e.created_at || ''}">${formatDate(e.created_at)}</td>
                    <td class="${tw.td}" data-label="${t('audit.user')}">${escapeHtml(e.username) || '-'}</td>
                    <td class="${tw.td}" data-label="${t('audit.action')}"><span class="${tw.badgeBlue}">${escapeHtml(e.action)}</span></td>
                    <td class="${tw.td}" data-label="${t('audit.resource')}">${escapeHtml(e.resource_type || '')}${e.resource_id ? '/' + escapeHtml(e.resource_id) : ''}</td>
                    <td class="${tw.td}" data-label="${t('audit.ip')}">${escapeHtml(e.ip_address) || '-'}</td>
                </tr>
            `).join('')}
            </tbody></table></div>
        `;

        makeSortable('auditTable');

        const pages = Math.ceil(data.total / 50);
        const current = Math.floor(offset / 50);
        document.getElementById('auditPagination').innerHTML = Array.from({length: Math.min(pages, 10)}, (_, i) =>
            `<button class="${tw.pageBtn}${i === current ? ' active' : ''}" onclick="window._pages.audit.loadAuditLog(${i * 50})">${i + 1}</button>`
        ).join('');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('audit.failedToLoad', {error: escapeHtml(e.message)})}</div>`;
    }
}

export function init() {
    registerPageLoader('audit', () => loadAuditLog());
}

window._pages = window._pages || {};
window._pages.audit = { loadAuditLog };
