let dashboardRefreshTimer = null;
let pageLoaders = {};

export function registerPageLoader(page, loader) {
    pageLoaders[page] = loader;
}

export function clearRefreshTimer() {
    if (dashboardRefreshTimer) {
        clearInterval(dashboardRefreshTimer);
        dashboardRefreshTimer = null;
    }
}

export function showPage(page) {
    document.querySelectorAll('[id^="page-"]').forEach(el => el.style.display = 'none');
    const pageEl = document.getElementById(`page-${page}`);
    if (pageEl) pageEl.style.display = 'block';
    document.querySelectorAll('.sidebar a').forEach(a => a.classList.remove('active'));
    const pageLink = document.querySelector(`[data-page="${page}"]`);
    if (pageLink) pageLink.classList.add('active');

    localStorage.setItem('admin_active_page', page);
    clearRefreshTimer();

    const loader = pageLoaders[page];
    if (loader) loader();
}

export function setRefreshTimer(fn, interval) {
    clearRefreshTimer();
    dashboardRefreshTimer = setInterval(fn, interval);
}
