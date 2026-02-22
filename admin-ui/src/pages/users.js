import { api } from '../api.js';
import { showToast } from '../notifications.js';
import { formatDate, escapeHtml, closeModal } from '../utils.js';
import { registerPageLoader } from '../router.js';
import { t } from '../i18n.js';
import { makeSortable } from '../sorting.js';
import * as tw from '../tw.js';

const PERMISSION_GROUPS = {
    sandbox: ['sandbox:read', 'sandbox:write', 'sandbox:delete'],
    knowledge: ['knowledge:read', 'knowledge:write', 'knowledge:delete'],
    scraper: ['scraper:read', 'scraper:write', 'scraper:delete', 'scraper:execute'],
    training: ['training:read', 'training:write', 'training:delete', 'training:execute'],
    prompts: ['prompts:read', 'prompts:write', 'prompts:delete'],
    users: ['users:read', 'users:write'],
    audit: ['audit:read'],
    tenants: ['tenants:read', 'tenants:write', 'tenants:delete'],
    operators: ['operators:read', 'operators:write'],
    analytics: ['analytics:read', 'analytics:export'],
    llm_config: ['llm_config:read', 'llm_config:write'],
    notifications: ['notifications:read', 'notifications:write'],
    system: ['system:read', 'system:write'],
    vehicles: ['vehicles:read', 'vehicles:write'],
    pronunciation: ['pronunciation:read', 'pronunciation:write'],
};

const ROLE_DEFAULTS = {
    admin: ['*'],
    analyst: ['analytics:read', 'analytics:export', 'knowledge:read', 'training:read', 'prompts:read', 'vehicles:read', 'operators:read'],
    operator: ['operators:read'],
    content_manager: [
        'sandbox:read', 'sandbox:write', 'sandbox:delete',
        'knowledge:read', 'knowledge:write', 'knowledge:delete',
        'scraper:read', 'scraper:write', 'scraper:delete', 'scraper:execute',
        'training:read', 'training:write', 'training:delete', 'training:execute',
        'prompts:read', 'prompts:write', 'prompts:delete',
    ],
};

function roleLabel(role) {
    const key = `users.role${role.charAt(0).toUpperCase()}${role.slice(1).replace(/_(\w)/g, (_, c) => c.toUpperCase())}`;
    return t(key) || role;
}

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
            <div class="overflow-x-auto min-h-[480px]"><table class="${tw.table}" id="usersTable"><thead><tr><th class="${tw.thSortable}" data-sortable>${t('users.username')}</th><th class="${tw.thSortable}" data-sortable>${t('users.role')}</th><th class="${tw.thSortable}" data-sortable>${t('users.permissions')}</th><th class="${tw.thSortable}" data-sortable>${t('users.activeCol')}</th><th class="${tw.thSortable}" data-sortable>${t('users.lastLogin')}</th><th class="${tw.th}">${t('users.actionsCol')}</th></tr></thead><tbody>
            ${users.map(u => {
                const permsBadge = u.permissions !== null && u.permissions !== undefined
                    ? `<span class="${tw.badgeYellow}">${t('users.customPermissions')}</span>`
                    : `<span class="${tw.badgeGray}">${t('users.useRoleDefaults')}</span>`;
                return `
                <tr class="${tw.trHover}">
                    <td class="${tw.td}" data-label="${t('users.username')}">${escapeHtml(u.username)}</td>
                    <td class="${tw.td}" data-label="${t('users.role')}"><span class="${tw.badgeBlue}">${escapeHtml(roleLabel(u.role))}</span></td>
                    <td class="${tw.td}" data-label="${t('users.permissions')}">${permsBadge}</td>
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
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.users.changeRole('${u.id}', 'content_manager')">${t('users.roleContentManager')}</button>
                                <div class="border-t border-neutral-200 dark:border-neutral-700 my-1"></div>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" data-id="${escapeHtml(u.id)}" data-role="${escapeHtml(u.role)}" data-perms="${escapeHtml(JSON.stringify(u.permissions))}" onclick="window._pages.users.showPermissionsEditor(this.dataset.id, this.dataset.role, this.dataset.perms)">${t('users.editPermissions')}</button>
                                <div class="border-t border-neutral-200 dark:border-neutral-700 my-1"></div>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" onclick="window._pages.users.toggleUser('${u.id}', ${u.is_active})">${u.is_active ? t('common.deactivate') : t('common.activate')}</button>
                                <button class="w-full text-left px-3 py-1.5 text-xs hover:bg-neutral-100 dark:hover:bg-neutral-700 cursor-pointer" data-id="${escapeHtml(u.id)}" data-name="${escapeHtml(u.username)}" onclick="window._pages.users.resetUserPassword(this.dataset.id, this.dataset.name)">${t('users.resetPwd')}</button>
                            </div>
                        </div>
                    </td>
                </tr>
            `}).join('')}
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

function showPermissionsEditor(userId, role, permsJson) {
    let customPerms = null;
    try { customPerms = JSON.parse(permsJson); } catch { customPerms = null; }
    if (customPerms === 'null' || permsJson === 'null') customPerms = null;
    const isCustom = customPerms !== null;
    const defaults = ROLE_DEFAULTS[role] || [];
    const effective = isCustom ? customPerms : defaults;
    const isWildcard = effective.includes('*');

    // Build modal content
    let html = `
        <div class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center" id="permissionsOverlay" onclick="if(event.target===this) window._pages.users.closePermissions()">
            <div class="bg-white dark:bg-neutral-900 rounded-lg shadow-xl w-full max-w-2xl max-h-[80vh] overflow-y-auto p-6">
                <h3 class="text-lg font-semibold mb-4">${t('users.editPermissions')}</h3>
                <label class="flex items-center gap-2 mb-4 cursor-pointer">
                    <input type="checkbox" id="permUseDefaults" ${!isCustom ? 'checked' : ''} onchange="window._pages.users.togglePermDefaults()">
                    <span class="text-sm">${t('users.useRoleDefaults')} (${escapeHtml(roleLabel(role))})</span>
                </label>
                <div id="permGrid" class="${!isCustom ? 'opacity-40 pointer-events-none' : ''}">
    `;

    for (const [group, perms] of Object.entries(PERMISSION_GROUPS)) {
        html += `<div class="mb-3"><div class="text-xs font-semibold text-neutral-500 uppercase mb-1">${escapeHtml(group)}</div><div class="flex flex-wrap gap-x-4 gap-y-1">`;
        for (const perm of perms) {
            const action = perm.split(':')[1];
            const checked = isWildcard || effective.includes(perm) ? 'checked' : '';
            html += `<label class="flex items-center gap-1 text-sm cursor-pointer"><input type="checkbox" class="perm-cb" value="${escapeHtml(perm)}" ${checked}><span>${escapeHtml(action)}</span></label>`;
        }
        html += '</div></div>';
    }

    html += `
                </div>
                <div class="flex justify-end gap-2 mt-6">
                    <button class="px-4 py-2 text-sm rounded-md border border-neutral-300 dark:border-neutral-600 cursor-pointer" onclick="window._pages.users.closePermissions()">${t('common.cancel')}</button>
                    <button class="px-4 py-2 text-sm rounded-md bg-violet-600 text-white hover:bg-violet-700 cursor-pointer" onclick="window._pages.users.savePermissions('${escapeHtml(userId)}')">${t('common.save')}</button>
                </div>
            </div>
        </div>
    `;

    const existing = document.getElementById('permissionsOverlay');
    if (existing) existing.remove();
    document.body.insertAdjacentHTML('beforeend', html);
}

function togglePermDefaults() {
    const useDefaults = document.getElementById('permUseDefaults').checked;
    const grid = document.getElementById('permGrid');
    grid.className = useDefaults ? 'opacity-40 pointer-events-none' : '';
}

async function savePermissions(userId) {
    const useDefaults = document.getElementById('permUseDefaults').checked;
    let permissions = null;
    if (!useDefaults) {
        permissions = [];
        document.querySelectorAll('.perm-cb:checked').forEach(cb => {
            permissions.push(cb.value);
        });
    }
    try {
        await api(`/admin/users/${userId}`, { method: 'PATCH', body: JSON.stringify({ permissions }) });
        showToast(t('users.permissionsUpdated'));
        closePermissions();
        loadUsers();
    } catch (e) { showToast(t('users.permissionsFailed', {error: e.message}), 'error'); }
}

function closePermissions() {
    const overlay = document.getElementById('permissionsOverlay');
    if (overlay) overlay.remove();
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
window._pages.users = {
    loadUsers, showCreateUser, createUser, changeRole, toggleUser, resetUserPassword,
    showPermissionsEditor, togglePermDefaults, savePermissions, closePermissions
};
