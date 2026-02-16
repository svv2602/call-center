import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import * as tw from '../tw.js';

async function loadUsers() {
    const container = document.getElementById('usersContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/admin/users');
        const users = data.users || [];
        if (users.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">No users found</div>`;
            return;
        }
        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}"><thead><tr><th class="${tw.th}">Username</th><th class="${tw.th}">Role</th><th class="${tw.th}">Active</th><th class="${tw.th}">Last Login</th><th class="${tw.th}">Actions</th></tr></thead><tbody>
            ${users.map(u => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}">${escapeHtml(u.username)}</td>
                    <td class="${tw.td}"><span class="${tw.badgeBlue}">${escapeHtml(u.role)}</span></td>
                    <td class="${tw.td}">${u.is_active ? `<span class="${tw.badgeGreen}">Yes</span>` : `<span class="${tw.badgeRed}">No</span>`}</td>
                    <td class="${tw.td}">${formatDate(u.last_login_at)}</td>
                    <td class="${tw.td}">
                        <div class="flex flex-wrap items-center gap-1">
                            <select onchange="window._pages.users.changeRole('${u.id}', this.value)" class="${tw.selectSm}">
                                <option value="">Change role...</option>
                                <option value="admin">Admin</option>
                                <option value="analyst">Analyst</option>
                                <option value="operator">Operator</option>
                            </select>
                            <button class="${tw.btnSecondary} ${tw.btnSm}" onclick="window._pages.users.toggleUser('${u.id}', ${u.is_active})">${u.is_active ? 'Deactivate' : 'Activate'}</button>
                            <button class="${tw.btnPurple} ${tw.btnSm}" onclick="window._pages.users.resetUserPassword('${u.id}', '${escapeHtml(u.username)}')">Reset pwd</button>
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>
        `;
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">Failed to load users: ${escapeHtml(e.message)}</div>`;
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
