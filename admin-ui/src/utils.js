import * as tw from './tw.js';
import { getLocale } from './i18n.js';

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

export function closeModal(id) {
    document.getElementById(id).classList.remove('show');
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
