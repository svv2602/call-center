import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, statusBadge, closeModal } from '../utils.js';
import { registerPageLoader, setRefreshTimer } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import * as tw from '../tw.js';

async function loadOperators() {
    const loading = document.getElementById('operatorsLoading');
    const tbody = document.querySelector('#operatorsTable tbody');
    loading.style.display = 'flex';

    try {
        const data = await api('/operators');
        loading.style.display = 'none';
        let ops = data.operators || [];
        const totalCount = ops.length;

        // Client-side filtering (filters are in DOM, preserved across auto-refresh)
        const searchText = (document.getElementById('operatorSearch')?.value || '').toLowerCase().trim();
        const filterStatus = document.getElementById('operatorFilterStatus')?.value || '';
        if (searchText) ops = ops.filter(o =>
            (o.name || '').toLowerCase().includes(searchText) ||
            (o.extension || '').toLowerCase().includes(searchText)
        );
        if (filterStatus) ops = ops.filter(o => o.current_status === filterStatus);

        if (ops.length === 0) {
            tbody.innerHTML = `<tr><td colspan="7" class="${tw.emptyState}">${t('operators.noOperators')}</td></tr>`;
            return;
        }
        tbody.innerHTML = ops.map(o => `
            <tr class="${tw.trHover}">
                <td class="${tw.td}">${escapeHtml(o.name)}</td>
                <td class="${tw.td}">${escapeHtml(o.extension)}</td>
                <td class="${tw.td}">${statusBadge(o.current_status)}</td>
                <td class="${tw.td}">${(o.skills || []).map(s => `<span class="${tw.badgeBlue}">${escapeHtml(s)}</span>`).join(' ')}</td>
                <td class="${tw.td}">${o.shift_start || '09:00'} - ${o.shift_end || '18:00'}</td>
                <td class="${tw.td}">${o.is_active ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                <td class="${tw.td}">
                    <div class="flex flex-wrap items-center gap-1">
                        <select onchange="window._pages.operators.changeOperatorStatus('${o.id}', this.value); this.selectedIndex=0" class="${tw.selectSm}">
                            <option value="">${t('operators.statusSelect')}</option>
                            <option value="online">${t('operators.statusOnline')}</option>
                            <option value="offline">${t('operators.statusOffline')}</option>
                            <option value="busy">${t('operators.statusBusy')}</option>
                            <option value="break">${t('operators.statusBreak')}</option>
                        </select>
                        <button class="${tw.btnPrimary} ${tw.btnSm}" onclick="window._pages.operators.editOperator('${o.id}')">${t('operators.editBtn')}</button>
                        ${o.is_active ? `<button class="${tw.btnDanger} ${tw.btnSm}" data-id="${escapeHtml(o.id)}" data-name="${escapeHtml(o.name)}" onclick="window._pages.operators.deactivateOperator(this.dataset.id, this.dataset.name)">×</button>` : ''}
                    </div>
                </td>
            </tr>
        `).join('');
        if (ops.length < totalCount) {
            tbody.innerHTML += `<tr><td colspan="7" class="${tw.mutedText} text-center py-2">${t('common.showing', {shown: ops.length, total: totalCount})}</td></tr>`;
        }

        makeSortable('operatorsTable');
    } catch (e) {
        loading.style.display = 'none';
        tbody.innerHTML = `<tr><td colspan="7" class="${tw.emptyState}">${t('operators.failedToLoad', {error: escapeHtml(e.message)})}
            <br><button class="${tw.btnPrimary} ${tw.btnSm} mt-2" onclick="window._pages.operators.loadOperators()">${t('common.retry')}</button></td></tr>`;
    }
}

async function loadQueueStatus() {
    try {
        const data = await api('/operators/queue');
        document.getElementById('operatorQueueStats').innerHTML = `
            <div class="${tw.card} text-center"><div class="${tw.statValue}">${data.operators_online || 0}</div><div class="${tw.statLabel}">${t('operators.online')}</div></div>
            <div class="${tw.card} text-center"><div class="${tw.statValue}">${data.transfers_last_hour || 0}</div><div class="${tw.statLabel}">${t('operators.transfers1h')}</div></div>
        `;
    } catch (e) {
        document.getElementById('operatorQueueStats').innerHTML = `<div class="${tw.emptyState}">${t('operators.queueUnavailable')}</div>`;
    }
}

function showCreateOperator() {
    document.getElementById('operatorModalTitle').textContent = t('operators.newOperatorTitle');
    document.getElementById('editOperatorId').value = '';
    document.getElementById('operatorName').value = '';
    document.getElementById('operatorExtension').value = '';
    document.getElementById('operatorSkills').value = '';
    document.getElementById('operatorShiftStart').value = '09:00';
    document.getElementById('operatorShiftEnd').value = '18:00';
    document.getElementById('operatorModal').classList.add('show');
}

async function editOperator(id) {
    try {
        const data = await api('/operators');
        const op = (data.operators || []).find(o => o.id === id);
        if (!op) { showToast(t('operators.notFound'), 'error'); return; }
        document.getElementById('operatorModalTitle').textContent = t('operators.editOperatorTitle');
        document.getElementById('editOperatorId').value = id;
        document.getElementById('operatorName').value = op.name || '';
        document.getElementById('operatorExtension').value = op.extension || '';
        document.getElementById('operatorSkills').value = (op.skills || []).join(', ');
        document.getElementById('operatorShiftStart').value = op.shift_start || '09:00';
        document.getElementById('operatorShiftEnd').value = op.shift_end || '18:00';
        document.getElementById('operatorModal').classList.add('show');
    } catch (e) { showToast(t('operators.loadFailed', {error: e.message}), 'error'); }
}

async function saveOperator() {
    const id = document.getElementById('editOperatorId').value;
    const name = document.getElementById('operatorName').value.trim();
    const extension = document.getElementById('operatorExtension').value.trim();
    const skillsStr = document.getElementById('operatorSkills').value.trim();
    const skills = skillsStr ? skillsStr.split(',').map(s => s.trim()).filter(Boolean) : [];
    const shift_start = document.getElementById('operatorShiftStart').value;
    const shift_end = document.getElementById('operatorShiftEnd').value;
    if (!name || !extension) { showToast(t('operators.nameRequired'), 'error'); return; }
    try {
        if (id) {
            await api(`/operators/${id}`, { method: 'PATCH', body: JSON.stringify({ name, extension, skills, shift_start, shift_end }) });
            showToast(t('operators.operatorUpdated'));
        } else {
            await api('/operators', { method: 'POST', body: JSON.stringify({ name, extension, skills, shift_start, shift_end }) });
            showToast(t('operators.operatorCreated'));
        }
        closeModal('operatorModal');
        loadOperators();
    } catch (e) { showToast(t('operators.saveFailed', {error: e.message}), 'error'); }
}

async function changeOperatorStatus(id, status) {
    if (!status) return;
    try {
        await api(`/operators/${id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) });
        showToast(t('operators.statusChanged', {status}));
        loadOperators();
    } catch (e) { showToast(t('operators.statusFailed', {error: e.message}), 'error'); }
}

async function deactivateOperator(id, name) {
    if (!confirm(t('operators.deactivateConfirm', {name}))) return;
    try {
        await api(`/operators/${id}`, { method: 'DELETE' });
        showToast(t('operators.operatorDeactivated'));
        loadOperators();
    } catch (e) { showToast(t('operators.deactivateFailed', {error: e.message}), 'error'); }
}

export function init() {
    registerPageLoader('operators', () => {
        loadOperators();
        loadQueueStatus();
        setRefreshTimer(() => { loadOperators(); loadQueueStatus(); }, 10000);
    });
}

window._pages = window._pages || {};
window._pages.operators = { loadOperators, loadQueueStatus, showCreateOperator, editOperator, saveOperator, changeOperatorStatus, deactivateOperator };
