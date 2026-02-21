import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import * as tw from '../tw.js';

async function loadUsers() {
    const container = document.getElementById('usersContainer');
    container.innerHTML = `<div class="${tw.loadingWrap}"><div class="spinner"></div></div>`;
    try {
        const data = await api('/admin/users');
        const users = data.users || [];
        if (users.length === 0) {
            container.innerHTML = `<div class="${tw.emptyState}">${t('users.noUsers')}</div>`;
            return;
        }
        container.innerHTML = `
            <div class="overflow-x-auto"><table class="${tw.table}" id="usersTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('users.username')}</th><th class="${tw.thSortable}" data-sortable>${t('users.role')}</th><th class="${tw.thSortable}" data-sortable>${t('users.activeCol')}</th><th class="${tw.thSortable}" data-sortable>${t('users.lastLogin')}</th><th class="${tw.th}">${t('users.actionsCol')}</th></tr></thead><tbody>
            ${users.map(u => `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}" data-label="${t('users.username')}">${escapeHtml(u.username)}</td>
                    <td class="${tw.td}" data-label="${t('users.role')}"><span class="${tw.badgeBlue}">${escapeHtml(u.role)}</span></td>
                    <td class="${tw.td}" data-label="${t('users.activeCol')}">${u.is_active ? `<span class="${tw.badgeGreen}">${t('common.yes')}</span>` : `<span class="${tw.badgeRed}">${t('common.no')}</span>`}</td>
                    <td class="${tw.td}" data-label="${t('users.lastLogin')}" data-sort-value="${u.last_login_at || ''}">${formatDate(u.last_login_at)}</td>
                    <td class="${tw.tdActions}">
                        <div class="relative inline-block">
                            <button class="px-1.5 py-0.5 text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200 text-sm cursor-pointer" onclick="this.nextElementSibling.classList.toggle('hidden')">&hellip;</button>
                            <div class="hidden absolute right-0 z-20 mt-1 w-44 bg-white dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 rounded-md shadow-lg py-1">
                                <div class="px-3 py-1 text-xs text-neutral-400 uppercase">${t('users.changeRole')}</div>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.users.changeRole('${u.id}', 'admin')">${t('users.roleAdmin')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.users.changeRole('${u.id}', 'analyst')">${t('users.roleAnalyst')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.users.changeRole('${u.id}', 'operator')">${t('users.roleOperator')}</button>
                                <div class="border-t border-neutral-200 dark:border-neutral-700 my-1"></div>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.users.toggleUser('${u.id}', ${u.is_active})">${u.is_active ? t('common.deactivate') : t('common.activate')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" data-id="${escapeHtml(u.id)}" data-name="${escapeHtml(u.username)}" onclick="window._pages.users.resetUserPassword(this.dataset.id, this.dataset.name)">${t('users.resetPwd')}</button>
                            </div>
                        </div>
                    </td>
                </tr>
            `).join('')}
            </tbody></table></div>
        `;

        makeSortable('usersTable');
    } catch (e) {
        container.innerHTML = `<div class="${tw.emptyState}">${t('users.failedToLoad', {error: escapeHtml(e.message)})}</div>`;
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
    if (!username || !password) { showToast(t('users.usernameRequired'), 'error'); return; }
    try {
        await api('/admin/users', { method: 'POST', body: JSON.stringify({ username, password, role }) });
        closeModal('createUserModal');
        showToast(t('users.userCreated'));
        loadUsers();
    } catch (e) { showToast(t('users.createFailed', {error: e.message}), 'error'); }
}

async function changeRole(userId, role) {
    if (!role) return;
    try {
        await api(`/admin/users/${userId}`, { method: 'PATCH', body: JSON.stringify({ role }) });
        showToast(t('users.roleUpdated'));
        loadUsers();
    } catch (e) { showToast(t('users.roleFailed', {error: e.message}), 'error'); }
}

async function toggleUser(userId, currentlyActive) {
    try {
        await api(`/admin/users/${userId}`, { method: 'PATCH', body: JSON.stringify({ is_active: !currentlyActive }) });
        showToast(currentlyActive ? t('users.userDeactivated') : t('users.userActivated'));
        loadUsers();
    } catch (e) { showToast(t('users.toggleFailed', {error: e.message}), 'error'); }
}

async function resetUserPassword(userId, username) {
    const newPwd = prompt(t('users.newPasswordPrompt', {username}));
    if (!newPwd) return;
    try {
        await api(`/admin/users/${userId}/reset-password`, { method: 'POST', body: JSON.stringify({ new_password: newPwd }) });
        showToast(t('users.passwordReset'));
    } catch (e) { showToast(t('users.resetFailed', {error: e.message}), 'error'); }
}

export function init() {
    registerPageLoader('users', () => loadUsers());
    document.addEventListener('click', (e) => {
        if (!e.target.closest('.relative')) {
            document.querySelectorAll('#page-users .relative > div:not(.hidden)').forEach(m => m.classList.add('hidden'));
        }
    });
}

window._pages = window._pages || {};
window._pages.users = { loadUsers, showCreateUser, createUser, changeRole, toggleUser, resetUserPassword };
