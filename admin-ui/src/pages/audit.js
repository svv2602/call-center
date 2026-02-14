import { api } from '../api.js';
import { formatDate, escapeHtml } from '../utils.js';
import { registerPageLoader } from '../router.js';

let auditOffset = 0;

async function loadAuditLog(offset = 0) {
    auditOffset = offset;
    const container = document.getElementById('auditContainer');
    container.innerHTML = '<div class="loading-overlay"><div class="spinner"></div></div>';

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
            container.innerHTML = '<div class="empty-state">No audit entries found</div>';
            document.getElementById('auditPagination').innerHTML = '';
            return;
        }
        container.innerHTML = `
            <table><thead><tr><th>Date</th><th>User</th><th>Action</th><th>Resource</th><th>IP</th></tr></thead><tbody>
            ${entries.map(e => `
                <tr>
                    <td>${formatDate(e.created_at)}</td>
                    <td>${escapeHtml(e.username) || '-'}</td>
                    <td><span class="badge badge-blue">${escapeHtml(e.action)}</span></td>
                    <td>${escapeHtml(e.resource_type || '')}${e.resource_id ? '/' + escapeHtml(e.resource_id) : ''}</td>
                    <td>${escapeHtml(e.ip_address) || '-'}</td>
                </tr>
            `).join('')}
            </tbody></table>
        `;

        const pages = Math.ceil(data.total / 50);
        const current = Math.floor(offset / 50);
        document.getElementById('auditPagination').innerHTML = Array.from({length: Math.min(pages, 10)}, (_, i) =>
            `<button class="${i === current ? 'active' : ''}" onclick="window._pages.audit.loadAuditLog(${i * 50})">${i + 1}</button>`
        ).join('');
    } catch (e) {
        container.innerHTML = `<div class="empty-state">Failed to load audit log: ${escapeHtml(e.message)}</div>`;
    }
}

export function init() {
    registerPageLoader('audit', () => loadAuditLog());
}

window._pages = window._pages || {};
window._pages.audit = { loadAuditLog };
