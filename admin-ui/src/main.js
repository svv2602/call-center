// Styles
import './styles/main.css';

// Core modules
import { getToken } from './api.js';
import { login, logout, checkTokenExpiry, applyRoleVisibility, loadPermissions } from './auth.js';
import { showPage, toggleSidebarGroup, initRouter, getPageFromHash } from './router.js';
import { connectWebSocket, setWsEventHandler, refreshWsStatus } from './websocket.js';
import { closeModal, closeTopmostModal } from './utils.js';
import { initDropdownKeyboard } from './dropdown-keyboard.js';
import { initTheme, toggleTheme, refreshThemeLabel } from './theme.js';
import { initLang, toggleLang, translateStaticDOM } from './i18n.js';
import { initHelp, openHelp, closeHelp } from './help.js';
import { initTooltips } from './tooltip.js';

// Page modules — each registers its page loader via init()
import { init as initDashboard } from './pages/dashboard.js';
import { init as initCalls, getCallsOffset } from './pages/calls.js';
import { init as initPrompts } from './pages/prompts.js';
import { init as initKnowledge } from './pages/knowledge.js';
import { init as initScenarios } from './pages/scenarios.js';
import { init as initTools } from './pages/tools-config.js';
import { init as initOperators } from './pages/operators.js';
import { init as initMonitoring } from './pages/monitoring.js';
import { init as initConfiguration } from './pages/configuration.js';
import { init as initCustomers } from './pages/customers.js';
import { init as initTenants } from './pages/tenants.js';
import { init as initNotifications } from './pages/notifications-page.js';
import { init as initUsers } from './pages/users.js';
import { init as initAudit } from './pages/audit.js';
import { init as initVehicles } from './pages/vehicles.js';
import { init as initSandbox } from './pages/sandbox.js';
import { init as initOnecData } from './pages/onec-data.js';
import { init as initSttHints } from './pages/stt-hints.js';
import { init as initPointHints } from './pages/point-hints.js';
import { init as initCostAnalysis } from './pages/cost-analysis.js';

// Initialize language, theme, and hash router
initLang();
initTheme();
translateStaticDOM();
initRouter();
initHelp();
initDropdownKeyboard();
initTooltips();

// Initialize all page loaders
initDashboard();
initCalls();
initPrompts();
initKnowledge();
initScenarios();
initTools();
initOperators();
initMonitoring();
initConfiguration();
initCustomers();
initTenants();
initNotifications();
initUsers();
initAudit();
initVehicles();
initSandbox();
initOnecData();
initSttHints();
initPointHints();
initCostAnalysis();

// WebSocket event handler — dispatches real-time updates to active page
setWsEventHandler((msg) => {
    const activePage = localStorage.getItem('admin_active_page') || 'dashboard';

    if (msg.type === 'call:started' || msg.type === 'call:ended' || msg.type === 'call:transferred') {
        if (activePage === 'dashboard') window._pages.dashboard.loadDashboard();
        if (activePage === 'calls') window._pages.calls.loadCalls(getCallsOffset());
    }
    if (msg.type === 'operator:status_changed') {
        if (activePage === 'operators') {
            window._pages.operators.loadOperators();
            window._pages.operators.loadQueueStatus();
        }
    }
    if (msg.type === 'dashboard:metrics_updated') {
        if (activePage === 'dashboard') window._pages.dashboard.loadDashboard();
    }
});

// Periodic token expiry check
setInterval(checkTokenExpiry, 60000);

// localStorage migration for old page names
let savedPage = localStorage.getItem('admin_active_page');
if (savedPage === 'training') { savedPage = 'prompts'; localStorage.setItem('admin_active_page', savedPage); }
if (savedPage === 'settings') { savedPage = 'monitoring'; localStorage.setItem('admin_active_page', savedPage); }

// App initialization — restore session if token exists
if (getToken()) {
    checkTokenExpiry();
    if (getToken()) {
        document.getElementById('loginContainer').style.display = 'none';
        document.getElementById('app').style.display = 'block';
        loadPermissions().then(() => {
            applyRoleVisibility();
            connectWebSocket();
            // Prefer URL hash over localStorage for initial page
            const hashPage = getPageFromHash();
            showPage(hashPage || savedPage || 'dashboard');
        });
    }
}

// Offline/online detection
function _showOfflineBanner(show) {
    let banner = document.getElementById('offlineBanner');
    if (show && !banner) {
        banner = document.createElement('div');
        banner.id = 'offlineBanner';
        banner.className = 'fixed top-0 left-0 right-0 z-[70] bg-amber-500 text-white text-center text-sm py-2 font-medium';
        banner.setAttribute('role', 'alert');
        banner.textContent = t('common.offline');
        document.body.prepend(banner);
    } else if (!show && banner) {
        banner.remove();
        showToast(t('common.backOnline'), 'info');
    }
}
window.addEventListener('offline', () => _showOfflineBanner(true));
window.addEventListener('online', () => _showOfflineBanner(false));

// Global event listeners
document.getElementById('loginPassword').addEventListener('keypress', e => {
    if (e.key === 'Enter') login();
});

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        closeTopmostModal();
    }
});

// Auto focus trap: observe .modal-overlay elements for .show class changes
import { trapFocus } from './focus-trap.js';
const _autoTraps = new Map();

const modalObserver = new MutationObserver((mutations) => {
    for (const m of mutations) {
        if (m.type !== 'attributes' || m.attributeName !== 'class') continue;
        const el = m.target;
        if (!el.classList.contains('modal-overlay')) continue;
        const id = el.id;
        if (el.classList.contains('show')) {
            // Modal opened — trap focus (if not already trapped by showModal)
            if (!_autoTraps.has(id)) {
                _autoTraps.set(id, trapFocus(el));
            }
        } else {
            // Modal closed — release trap
            const release = _autoTraps.get(id);
            if (release) {
                release();
                _autoTraps.delete(id);
            }
        }
    }
});
// Start observing once DOM is ready
requestAnimationFrame(() => {
    document.querySelectorAll('.modal-overlay').forEach(el => {
        modalObserver.observe(el, { attributes: true, attributeFilter: ['class'] });
    });
});

// Mobile sidebar toggle
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}
// Close sidebar when tapping backdrop on mobile
document.getElementById('sidebarBackdrop')?.addEventListener('click', () => {
    document.getElementById('sidebar').classList.remove('open');
});

// Expand/collapse sidebar (icon-only ↔ full)
function toggleSidebarExpand() {
    const sidebar = document.getElementById('sidebar');
    const main = document.querySelector('.main-content');
    const expanded = sidebar.classList.toggle('expanded');
    if (main) {
        main.style.marginLeft = expanded ? '14rem' : '';
    }
    localStorage.setItem('sidebar_expanded', expanded ? '1' : '0');
}

// Restore sidebar state on load
if (localStorage.getItem('sidebar_expanded') === '1') {
    document.getElementById('sidebar')?.classList.add('expanded');
    const main = document.querySelector('.main-content');
    if (main) main.style.marginLeft = '14rem';
}

// Set proper hash hrefs on nav links and handle click navigation
document.querySelectorAll('a[data-page]').forEach(a => {
    const page = a.dataset.page;
    a.href = '#/' + page;
    a.removeAttribute('onclick');
    a.addEventListener('click', (e) => {
        e.preventDefault();
        showPage(page);
        if (window.innerWidth <= 768) {
            document.getElementById('sidebar').classList.remove('open');
        }
    });
});

// Sidebar keyboard navigation (Arrow Up/Down between nav items)
document.getElementById('sidebar')?.addEventListener('keydown', (e) => {
    if (!['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft'].includes(e.key)) return;
    const items = [...document.querySelectorAll('#sidebar .nav-item, #sidebar .nav-group-trigger, #sidebar .nav-flyout-link')].filter(
        el => el.offsetParent !== null
    );
    const idx = items.indexOf(document.activeElement);
    if (idx === -1) return;

    e.preventDefault();
    if (e.key === 'ArrowDown') {
        items[(idx + 1) % items.length].focus();
    } else if (e.key === 'ArrowUp') {
        items[(idx - 1 + items.length) % items.length].focus();
    } else if (e.key === 'ArrowRight') {
        // Open group submenu
        const group = document.activeElement.closest('.nav-group');
        if (group && !group.classList.contains('open')) {
            toggleSidebarGroup(group.dataset.group);
            requestAnimationFrame(() => {
                const firstLink = group.querySelector('.nav-flyout-link');
                if (firstLink) firstLink.focus();
            });
        }
    } else if (e.key === 'ArrowLeft') {
        // Close group submenu
        const group = document.activeElement.closest('.nav-group');
        if (group && group.classList.contains('open')) {
            toggleSidebarGroup(group.dataset.group);
            const trigger = group.querySelector('.nav-group-trigger');
            if (trigger) trigger.focus();
        }
    }
});

// Language change handler — re-render active page and refresh labels
window.addEventListener('langchange', () => {
    refreshThemeLabel();
    refreshWsStatus();
    const activePage = localStorage.getItem('admin_active_page') || 'dashboard';
    showPage(activePage);
});

// Shared accordion toggle for collapsible card sections
function toggleAccordion(btn) {
    const card = btn.closest('.acc-section') || btn.closest('.onec-accordion');
    if (!card) return;
    const body = card.querySelector('.acc-body') || card.querySelector('.onec-section-body');
    const chevron = card.querySelector('.acc-chevron') || card.querySelector('.onec-chevron');
    const isOpen = card.dataset.open === 'true';
    if (isOpen) {
        body.style.display = 'none';
        if (chevron) chevron.classList.add('rotate-[-90deg]');
        card.dataset.open = 'false';
    } else {
        body.style.display = '';
        if (chevron) chevron.classList.remove('rotate-[-90deg]');
        card.dataset.open = 'true';
    }
}

// Tab-bar scroll fade indicator
function _initTabBarFade() {
    document.querySelectorAll('.tab-bar').forEach(bar => {
        function update() {
            const { scrollLeft, scrollWidth, clientWidth } = bar;
            if (scrollWidth <= clientWidth) {
                bar.classList.add('no-overflow');
                bar.classList.remove('scrolled-end', 'scrolled-mid');
            } else {
                bar.classList.remove('no-overflow');
                const atEnd = scrollLeft + clientWidth >= scrollWidth - 4;
                const atStart = scrollLeft <= 4;
                bar.classList.toggle('scrolled-end', atEnd && !atStart);
                bar.classList.toggle('scrolled-mid', !atEnd && !atStart);
            }
        }
        bar.addEventListener('scroll', update, { passive: true });
        // Initial check (may run before content, so also observe)
        requestAnimationFrame(update);
        new MutationObserver(update).observe(bar, { childList: true });
    });
}
requestAnimationFrame(_initTabBarFade);

// Expose globals for onclick handlers in HTML
window._app = { login, logout, showPage, closeModal, toggleSidebar, toggleSidebarExpand, toggleTheme, toggleLang, toggleSidebarGroup, openHelp, closeHelp, toggleAccordion };
