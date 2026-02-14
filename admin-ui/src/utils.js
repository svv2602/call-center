export function qualityBadge(score) {
    if (score == null) return '<span class="badge">N/A</span>';
    const s = parseFloat(score).toFixed(2);
    if (score >= 0.8) return `<span class="badge badge-green">${s}</span>`;
    if (score >= 0.5) return `<span class="badge badge-yellow">${s}</span>`;
    return `<span class="badge badge-red">${s}</span>`;
}

export function formatDate(d) {
    if (!d) return '-';
    return new Date(d).toLocaleString('uk-UA', { dateStyle: 'short', timeStyle: 'short' });
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
    if (!status) return '<span class="badge badge-gray">unknown</span>';
    const map = { online: 'badge-green', offline: 'badge-gray', busy: 'badge-yellow', break: 'badge-blue' };
    return `<span class="badge ${map[status] || 'badge-gray'}">${escapeHtml(status)}</span>`;
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
