import { api, getToken, setToken, clearToken, onLogout } from './api.js';
import { showToast } from './notifications.js';
import { connectWebSocket, disconnectWebSocket } from './websocket.js';
import { showPage, clearRefreshTimer, getPageFromHash } from './router.js';
import { t } from './i18n.js';

let _userPermissions = [];

export function getUserRole() {
    const token = getToken();
    if (!token) return '';
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        return payload.role || '';
    } catch { return ''; }
}

export function hasPermission(perm) {
    return _userPermissions.includes('*') || _userPermissions.includes(perm);
}

export function getUserPermissions() {
    return _userPermissions;
}

export async function loadPermissions() {
    try {
        const data = await api('/auth/me');
        _userPermissions = data.permissions || [];
        localStorage.setItem('admin_permissions', JSON.stringify(_userPermissions));
    } catch {
        // Fallback: restore from localStorage
        try {
            _userPermissions = JSON.parse(localStorage.getItem('admin_permissions') || '[]');
        } catch { _userPermissions = []; }
    }
}

// Page → required permission mapping
const PAGE_PERMISSIONS = {
    dashboard: null, // all authenticated users
    calls: 'analytics:read',
    prompts: 'prompts:read',
    knowledge: 'knowledge:read',
    scenarios: 'training:read',
    tools: 'training:read',
    sandbox: 'sandbox:read',
    operators: 'operators:read',
    monitoring: 'monitoring:read',
    configuration: 'configuration:read',
    tenants: 'tenants:read',
    notifications: 'notifications:read',
    users: 'users:read',
    audit: 'audit:read',
    vehicles: 'vehicles:read',
    'onec-data': 'onec_data:read',
    'stt-hints': 'configuration:read',
};

export function applyRoleVisibility() {
    // Permission-based nav visibility
    document.querySelectorAll('[data-page]').forEach(el => {
        const page = el.getAttribute('data-page');
        const requiredPerm = PAGE_PERMISSIONS[page];
        if (requiredPerm === null || requiredPerm === undefined) return; // always visible
        el.style.display = hasPermission(requiredPerm) ? '' : 'none';
    });

    // .admin-only elements: visible only if user has wildcard
    document.querySelectorAll('.admin-only').forEach(el => {
        // Skip nav items — they're handled by data-page above
        if (el.hasAttribute('data-page')) return;
        el.style.display = hasPermission('*') ? '' : 'none';
    });
}

export async function login() {
    const u = document.getElementById('loginUsername').value;
    const p = document.getElementById('loginPassword').value;
    if (!u || !p) {
        document.getElementById('loginError').textContent = t('login.error');
        document.getElementById('loginError').style.display = 'block';
        return;
    }
    try {
        const data = await api('/auth/login', {
            method: 'POST',
            body: JSON.stringify({ username: u, password: p })
        });
        setToken(data.token);
        document.getElementById('loginContainer').style.display = 'none';
        document.getElementById('app').style.display = 'block';
        document.getElementById('loginError').style.display = 'none';
        await loadPermissions();
        applyRoleVisibility();
        connectWebSocket();
        const hashPage = getPageFromHash();
        const savedPage = localStorage.getItem('admin_active_page') || 'dashboard';
        showPage(hashPage || savedPage);
    } catch (e) {
        document.getElementById('loginError').textContent = t('login.error');
        document.getElementById('loginError').style.display = 'block';
    }
}

export function logout() {
    clearToken();
    _userPermissions = [];
    localStorage.removeItem('admin_permissions');
    clearRefreshTimer();
    disconnectWebSocket();
    document.getElementById('app').style.display = 'none';
    document.getElementById('loginContainer').style.display = 'flex';
}

export function checkTokenExpiry() {
    const token = getToken();
    if (!token) return;
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        if (payload.exp && payload.exp < Date.now() / 1000) {
            showToast(t('api.sessionExpired'), 'error');
            logout();
        }
    } catch { /* ignore parse errors */ }
}

// Register logout callback for 401 handling in api.js
onLogout(logout);
