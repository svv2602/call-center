const _DURATIONS = { success: 4000, info: 5000, warning: 6000, error: 8000 };
const _MAX_TOASTS = 5;

const _BG = {
    success: 'bg-emerald-600',
    info: 'bg-blue-600',
    warning: 'bg-amber-500',
    error: 'bg-red-600',
};

export function showToast(message, type = 'success') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    // Enforce max visible toasts
    while (container.children.length >= _MAX_TOASTS) {
        container.firstChild.remove();
    }

    const toast = document.createElement('div');
    const bg = _BG[type] || _BG.success;
    toast.className = `toast px-4 py-2.5 rounded-lg text-sm text-white shadow-lg ${bg} flex items-center gap-2`;

    const text = document.createElement('span');
    text.className = 'flex-1';
    text.textContent = message;
    toast.appendChild(text);

    // Dismiss button
    const btn = document.createElement('button');
    btn.className = 'ml-1 opacity-70 hover:opacity-100 focus:opacity-100 text-white font-bold leading-none cursor-pointer';
    btn.setAttribute('aria-label', 'Close');
    btn.textContent = '\u00D7';
    btn.onclick = () => _dismiss(toast);
    toast.appendChild(btn);

    container.appendChild(toast);

    const duration = _DURATIONS[type] || _DURATIONS.success;
    setTimeout(() => _dismiss(toast), duration);
}

function _dismiss(toast) {
    if (!toast.parentNode) return;
    toast.classList.add('toast-exit');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
    // Fallback removal if animation doesn't fire
    setTimeout(() => toast.remove(), 400);
}
