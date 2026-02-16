export function showToast(message, type = 'success') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    const bg = type === 'error'
        ? 'bg-red-600'
        : 'bg-emerald-600';
    toast.className = `toast px-4 py-2.5 rounded-lg text-sm text-white shadow-lg ${bg}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}
