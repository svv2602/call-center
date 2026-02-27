import { confirmIfDirty } from './form-guard.js';

let dashboardRefreshTimer = null;
let pageLoaders = {};
let _currentPage = null;

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

/** Extract page name from location.hash (e.g. "#/calls" → "calls") */
export function getPageFromHash() {
    return location.hash.replace(/^#\/?/, '') || null;
}

export function showPage(page) {
    // Guard: warn if there are unsaved form changes
    if (_currentPage && _currentPage !== page && !confirmIfDirty()) return;

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

    _currentPage = page;

    // Scroll to top on page change
    const mainContent = document.getElementById('mainContent');
    if (mainContent) mainContent.scrollTop = 0;
    window.scrollTo(0, 0);

    // Sync URL hash
    if (getPageFromHash() !== page) {
        history.pushState(null, '', '#/' + page);
    }

    localStorage.setItem('admin_active_page', page);
    clearRefreshTimer();

    const loader = pageLoaders[page];
    if (loader) loader();
}

/** Initialize hash-based routing: listen for back/forward navigation */
export function initRouter() {
    // hashchange fires on back/forward and direct hash edits
    window.addEventListener('hashchange', () => {
        const page = getPageFromHash();
        if (page && page !== _currentPage) {
            showPage(page);
        }
    });

    // popstate fires on back/forward after pushState
    window.addEventListener('popstate', () => {
        const page = getPageFromHash();
        if (page && page !== _currentPage) {
            showPage(page);
        }
    });
}

export function setRefreshTimer(fn, interval) {
    clearRefreshTimer();
    dashboardRefreshTimer = setInterval(fn, interval);
}
