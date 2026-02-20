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

export function toggleSidebarGroup(group) {
    const el = document.querySelector(`.nav-group[data-group="${group}"]`);
    if (el) el.classList.toggle('open');
}

export function showPage(page) {
    document.querySelectorAll('[id^="page-"]').forEach(el => el.style.display = 'none');
    const pageEl = document.getElementById(`page-${page}`);
    if (pageEl) pageEl.style.display = 'block';

    // Clear active state from all nav items and flyout links
    document.querySelectorAll('.nav-item[data-page], .nav-flyout-link[data-page]').forEach(a => a.classList.remove('active'));
    document.querySelectorAll('.nav-group-trigger').forEach(a => a.classList.remove('active'));

    // Set active on the matching page link
    const pageLink = document.querySelector(`.nav-item[data-page="${page}"], .nav-flyout-link[data-page="${page}"]`);
    if (pageLink) pageLink.classList.add('active');

    // If it's inside a group flyout, also highlight the group trigger
    const group = pageLink?.closest('.nav-group');
    if (group) {
        const trigger = group.querySelector('.nav-group-trigger');
        if (trigger) trigger.classList.add('active');
    }

    localStorage.setItem('admin_active_page', page);
    clearRefreshTimer();

    const loader = pageLoaders[page];
    if (loader) loader();
}

export function setRefreshTimer(fn, interval) {
    clearRefreshTimer();
    dashboardRefreshTimer = setInterval(fn, interval);
}
