import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { escapeHtml, statusBadge, closeModal } from '../utils.js';
import { registerPageLoader, setRefreshTimer } from '../router.js';

async function loadOperators() {
    const loading = document.getElementById('operatorsLoading');
    const tbody = document.querySelector('#operatorsTable tbody');
    loading.style.display = 'block';

    try {
        const data = await api('/operators');
        loading.style.display = 'none';
        const ops = data.operators || [];
        if (ops.length === 0) {
            tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No operators found</td></tr>';
            return;
        }
        tbody.innerHTML = ops.map(o => `
            <tr>
                <td>${escapeHtml(o.name)}</td>
                <td>${escapeHtml(o.extension)}</td>
                <td>${statusBadge(o.current_status)}</td>
                <td>${(o.skills || []).map(s => `<span class="badge badge-blue">${escapeHtml(s)}</span>`).join(' ')}</td>
                <td>${o.shift_start || '09:00'} - ${o.shift_end || '18:00'}</td>
                <td>${o.is_active ? '<span class="badge badge-green">Yes</span>' : '<span class="badge badge-red">No</span>'}</td>
                <td>
                    <select onchange="window._pages.operators.changeOperatorStatus('${o.id}', this.value); this.selectedIndex=0" style="font-size:.75rem;padding:.2rem">
                        <option value="">Status...</option>
                        <option value="online">Online</option>
                        <option value="offline">Offline</option>
                        <option value="busy">Busy</option>
                        <option value="break">Break</option>
                    </select>
                    <button class="btn btn-primary btn-sm" onclick="window._pages.operators.editOperator('${o.id}')">Edit</button>
                    ${o.is_active ? `<button class="btn btn-danger btn-sm" onclick="window._pages.operators.deactivateOperator('${o.id}', '${escapeHtml(o.name).replace(/'/g, "\\'")}')">Deactivate</button>` : ''}
                </td>
            </tr>
        `).join('');
    } catch (e) {
        loading.style.display = 'none';
        tbody.innerHTML = `<tr><td colspan="7" class="empty-state">Failed to load operators: ${escapeHtml(e.message)}
            <br><button class="btn btn-primary btn-sm" onclick="window._pages.operators.loadOperators()" style="margin-top:.5rem">Retry</button></td></tr>`;
    }
}

async function loadQueueStatus() {
    try {
        const data = await api('/operators/queue');
        document.getElementById('operatorQueueStats').innerHTML = `
            <div class="card stat-card"><div class="value">${data.operators_online || 0}</div><div class="label">Operators Online</div></div>
            <div class="card stat-card"><div class="value">${data.transfers_last_hour || 0}</div><div class="label">Transfers (1h)</div></div>
        `;
    } catch (e) {
        document.getElementById('operatorQueueStats').innerHTML = `<div class="empty-state">Queue status unavailable</div>`;
    }
}

function showCreateOperator() {
    document.getElementById('operatorModalTitle').textContent = 'New Operator';
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
        if (!op) { showToast('Operator not found', 'error'); return; }
        document.getElementById('operatorModalTitle').textContent = 'Edit Operator';
        document.getElementById('editOperatorId').value = id;
        document.getElementById('operatorName').value = op.name || '';
        document.getElementById('operatorExtension').value = op.extension || '';
        document.getElementById('operatorSkills').value = (op.skills || []).join(', ');
        document.getElementById('operatorShiftStart').value = op.shift_start || '09:00';
        document.getElementById('operatorShiftEnd').value = op.shift_end || '18:00';
        document.getElementById('operatorModal').classList.add('show');
    } catch (e) { showToast('Failed to load operator: ' + e.message, 'error'); }
}

async function saveOperator() {
    const id = document.getElementById('editOperatorId').value;
    const name = document.getElementById('operatorName').value.trim();
    const extension = document.getElementById('operatorExtension').value.trim();
    const skillsStr = document.getElementById('operatorSkills').value.trim();
    const skills = skillsStr ? skillsStr.split(',').map(s => s.trim()).filter(Boolean) : [];
    const shift_start = document.getElementById('operatorShiftStart').value;
    const shift_end = document.getElementById('operatorShiftEnd').value;
    if (!name || !extension) { showToast('Name and extension are required', 'error'); return; }
    try {
        if (id) {
            await api(`/operators/${id}`, { method: 'PATCH', body: JSON.stringify({ name, extension, skills, shift_start, shift_end }) });
            showToast('Operator updated');
        } else {
            await api('/operators', { method: 'POST', body: JSON.stringify({ name, extension, skills, shift_start, shift_end }) });
            showToast('Operator created');
        }
        closeModal('operatorModal');
        loadOperators();
    } catch (e) { showToast('Failed to save operator: ' + e.message, 'error'); }
}

async function changeOperatorStatus(id, status) {
    if (!status) return;
    try {
        await api(`/operators/${id}/status`, { method: 'PATCH', body: JSON.stringify({ status }) });
        showToast(`Status changed to ${status}`);
        loadOperators();
    } catch (e) { showToast('Failed to change status: ' + e.message, 'error'); }
}

async function deactivateOperator(id, name) {
    if (!confirm(`Deactivate operator "${name}"?`)) return;
    try {
        await api(`/operators/${id}`, { method: 'DELETE' });
        showToast('Operator deactivated');
        loadOperators();
    } catch (e) { showToast('Failed to deactivate: ' + e.message, 'error'); }
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
