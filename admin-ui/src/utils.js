import * as tw from './tw.js';
import { getLocale } from './i18n.js';
import { trapFocus } from './focus-trap.js';

// Active focus trap cleanups keyed by modal id
const _modalTraps = new Map();

export function qualityBadge(score) {
    if (score == null) return `<span class="${tw.badge}">N/A</span>`;
    const s = parseFloat(score).toFixed(2);
    if (score >= 0.8) return `<span class="${tw.badgeGreen}">${s}</span>`;
    if (score >= 0.5) return `<span class="${tw.badgeYellow}">${s}</span>`;
    return `<span class="${tw.badgeRed}">${s}</span>`;
}

export function formatDate(d) {
    if (!d) return '-';
    return new Date(d).toLocaleString(getLocale(), { dateStyle: 'short', timeStyle: 'short' });
}

/**
 * Open a modal with focus trap and ARIA attributes.
 * @param {string} id - Modal element id
 */
export function showModal(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.add('show');
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    // Trap focus inside the modal
    const release = trapFocus(el);
    _modalTraps.set(id, release);
}

/**
 * Close a modal and release focus trap.
 * @param {string} id - Modal element id
 */
export function closeModal(id) {
    const el = document.getElementById(id);
    if (el) el.classList.remove('show');
    // Release focus trap and restore previous focus
    const release = _modalTraps.get(id);
    if (release) {
        release();
        _modalTraps.delete(id);
    }
}

/**
 * Close the topmost open modal (used by Escape key handler).
 * @returns {boolean} true if a modal was closed
 */
export function closeTopmostModal() {
    const openModals = document.querySelectorAll('.modal-overlay.show');
    if (openModals.length === 0) return false;
    // Close the last (topmost) one
    const topmost = openModals[openModals.length - 1];
    closeModal(topmost.id);
    return true;
}

export function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

export function statusBadge(status) {
    if (!status) return `<span class="${tw.badgeGray}">unknown</span>`;
    const map = { online: tw.badgeGreen, offline: tw.badgeGray, busy: tw.badgeYellow, break: tw.badgeBlue };
    return `<span class="${map[status] || tw.badgeGray}">${escapeHtml(status)}</span>`;
}

export function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
