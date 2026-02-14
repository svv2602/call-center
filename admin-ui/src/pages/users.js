import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';

async function loadUsers() {
    const container = document.getElementById('usersContainer');
    container.innerHTML = '<div class="loading-overlay"><div class="spinner"></div></div>';
    try {
        const data = await api('/admin/users');
        const users = data.users || [];
        if (users.length === 0) {
            container.innerHTML = '<div class="empty-state">No users found</div>';
            return;
        }
        container.innerHTML = `
            <table><thead><tr><th>Username</th><th>Role</th><th>Active</th><th>Last Login</th><th>Actions</th></tr></thead><tbody>
            ${users.map(u => `
                <tr>
                    <td>${escapeHtml(u.username)}</td>
                    <td><span class="badge badge-blue">${escapeHtml(u.role)}</span></td>
                    <td>${u.is_active ? '<span class="badge badge-green">Yes</span>' : '<span class="badge badge-red">No</span>'}</td>
                    <td>${formatDate(u.last_login_at)}</td>
                    <td>
                        <select onchange="window._pages.users.changeRole('${u.id}', this.value)" style="font-size:.75rem;padding:.2rem">
                            <option value="">Change role...</option>
                            <option value="admin">Admin</option>
                            <option value="analyst">Analyst</option>
                            <option value="operator">Operator</option>
                        </select>
                        <button class="btn btn-sm" style="background:#64748b;color:#fff" onclick="window._pages.users.toggleUser('${u.id}', ${u.is_active})">${u.is_active ? 'Deactivate' : 'Activate'}</button>
                        <button class="btn btn-sm" style="background:#7c3aed;color:#fff" onclick="window._pages.users.resetUserPassword('${u.id}', '${escapeHtml(u.username)}')">Reset pwd</button>
                    </td>
                </tr>
            `).join('')}
            </tbody></table>
        `;
    } catch (e) {
        container.innerHTML = `<div class="empty-state">Failed to load users: ${escapeHtml(e.message)}</div>`;
    }
}

function showCreateUser() {
    document.getElementById('newUsername').value = '';
    document.getElementById('newPassword').value = '';
    document.getElementById('newRole').value = 'operator';
    document.getElementById('createUserModal').classList.add('show');
}

async function createUser() {
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newPassword').value;
    const role = document.getElementById('newRole').value;
    if (!username || !password) { showToast('Username and password are required', 'error'); return; }
    try {
        await api('/admin/users', { method: 'POST', body: JSON.stringify({ username, password, role }) });
        closeModal('createUserModal');
        showToast('User created');
        loadUsers();
    } catch (e) { showToast('Failed to create user: ' + e.message, 'error'); }
}

async function changeRole(userId, role) {
    if (!role) return;
    try {
        await api(`/admin/users/${userId}`, { method: 'PATCH', body: JSON.stringify({ role }) });
        showToast('Role updated');
        loadUsers();
    } catch (e) { showToast('Failed to update role: ' + e.message, 'error'); }
}

async function toggleUser(userId, currentlyActive) {
    try {
        await api(`/admin/users/${userId}`, { method: 'PATCH', body: JSON.stringify({ is_active: !currentlyActive }) });
        showToast(currentlyActive ? 'User deactivated' : 'User activated');
        loadUsers();
    } catch (e) { showToast('Failed to update user: ' + e.message, 'error'); }
}

async function resetUserPassword(userId, username) {
    const newPwd = prompt(`New password for ${username}:`);
    if (!newPwd) return;
    try {
        await api(`/admin/users/${userId}/reset-password`, { method: 'POST', body: JSON.stringify({ new_password: newPwd }) });
        showToast('Password reset');
    } catch (e) { showToast('Failed to reset password: ' + e.message, 'error'); }
}

export function init() {
    registerPageLoader('users', () => loadUsers());
}

window._pages = window._pages || {};
window._pages.users = { loadUsers, showCreateUser, createUser, changeRole, toggleUser, resetUserPassword };
