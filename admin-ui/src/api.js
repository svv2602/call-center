import { showToast } from './notifications.js';
import { t } from './i18n.js';

const API = '';
let token = localStorage.getItem('admin_token');
let logoutCallback = null;

export function getToken() { return token; }
export function setToken(t) { token = t; localStorage.setItem('admin_token', t); }
export function clearToken() { token = null; localStorage.removeItem('admin_token'); }
export function onLogout(cb) { logoutCallback = cb; }

export async function api(path, opts = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${API}${path}`, { ...opts, headers });
    if (res.status === 401) {
        showToast(t('api.sessionExpired'), 'error');
        if (logoutCallback) logoutCallback();
        throw new Error(t('api.unauthorized'));
    }
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

export async function apiUpload(path, formData) {
    const headers = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const res = await fetch(`${API}${path}`, { method: 'POST', headers, body: formData });
    if (res.status === 401) {
        showToast(t('api.sessionExpired'), 'error');
        if (logoutCallback) logoutCallback();
        throw new Error(t('api.unauthorized'));
    }
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `HTTP ${res.status}`);
    }
    return res.json();
}

export async function fetchWithAuth(path) {
    const headers = {};
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return fetch(`${API}${path}`, { headers });
}
