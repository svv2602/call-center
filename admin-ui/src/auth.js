import { api, getToken, setToken, clearToken, onLogout } from './api.js';
import { showToast } from './notifications.js';
import { connectWebSocket, disconnectWebSocket } from './websocket.js';
import { showPage, clearRefreshTimer, getPageFromHash } from './router.js';
import { t } from './i18n.js';

export function getUserRole() {
    const token = getToken();
    if (!token) return '';
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        return payload.role || '';
    } catch { return ''; }
}

export function applyRoleVisibility() {
    const role = getUserRole();
    document.querySelectorAll('.admin-only').forEach(el => {
        el.style.display = role === 'admin' ? '' : 'none';
    });
    if (role === 'analyst') {
        document.querySelectorAll('[data-page="prompts"]').forEach(el => el.style.display = 'none');
    }
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
