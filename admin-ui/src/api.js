import { showToast } from './notifications.js';
import { t } from './i18n.js';

const API = '';
const DEFAULT_TIMEOUT_MS = 30_000;
let token = localStorage.getItem('admin_token');
let logoutCallback = null;

export function getToken() { return token; }
export function setToken(t) { token = t; localStorage.setItem('admin_token', t); }
export function clearToken() { token = null; localStorage.removeItem('admin_token'); }
export function onLogout(cb) { logoutCallback = cb; }

function _buildError(err) {
    if (err.name === 'AbortError') return new Error(t('api.requestTimeout'));
    if (err instanceof TypeError && err.message === 'Failed to fetch') return new Error(t('api.networkError'));
    return err;
}

export async function api(path, opts = {}) {
    const headers = { 'Content-Type': 'application/json' };
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const timeout = opts.timeout || DEFAULT_TIMEOUT_MS;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);

    let res;
    try {
        const { timeout: _t, ...fetchOpts } = opts;
        res = await fetch(`${API}${path}`, { ...fetchOpts, headers, signal: controller.signal });
    } catch (err) {
        throw _buildError(err);
    } finally {
        clearTimeout(timer);
    }

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

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 120_000); // 2min for uploads

    let res;
    try {
        res = await fetch(`${API}${path}`, { method: 'POST', headers, body: formData, signal: controller.signal });
    } catch (err) {
        throw _buildError(err);
    } finally {
        clearTimeout(timer);
    }

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

/**
 * Disable a button during an async action, restoring it in `finally`.
 * @param {string} btnId - DOM element id
 * @param {Function} fn - async function to run while button is locked
 * @param {string} [loadingText] - text to show on button while running
 */
export async function withButtonLock(btnId, fn, loadingText) {
    const btn = document.getElementById(btnId);
    const origHTML = btn?.innerHTML;
    if (btn) {
        btn.disabled = true;
        btn.setAttribute('aria-busy', 'true');
        btn.classList.add('opacity-50', 'cursor-not-allowed');
        if (loadingText) {
            btn.innerHTML = `<span class="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin mr-1.5 align-middle"></span>${loadingText}`;
        }
    }
    try {
        return await fn();
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.removeAttribute('aria-busy');
            btn.classList.remove('opacity-50', 'cursor-not-allowed');
            if (loadingText) btn.innerHTML = origHTML;
        }
    }
}
