// Styles
import './styles/main.css';

// Core modules
import { getToken } from './api.js';
import { login, logout, checkTokenExpiry, applyRoleVisibility } from './auth.js';
import { showPage, toggleSidebarGroup } from './router.js';
import { connectWebSocket, setWsEventHandler, refreshWsStatus } from './websocket.js';
import { closeModal } from './utils.js';
import { initTheme, toggleTheme, refreshThemeLabel } from './theme.js';
import { initLang, toggleLang, translateStaticDOM } from './i18n.js';

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
import { init as initNotifications } from './pages/notifications-page.js';
import { init as initUsers } from './pages/users.js';
import { init as initAudit } from './pages/audit.js';
import { init as initVehicles } from './pages/vehicles.js';
import { init as initSandbox } from './pages/sandbox.js';

// Initialize language and theme
initLang();
initTheme();
translateStaticDOM();

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
initNotifications();
initUsers();
initAudit();
initVehicles();
initSandbox();

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
        applyRoleVisibility();
        connectWebSocket();
        showPage(savedPage || 'dashboard');
    }
}

// Global event listeners
document.getElementById('loginPassword').addEventListener('keypress', e => {
    if (e.key === 'Enter') login();
});

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal-overlay.show').forEach(m => m.classList.remove('show'));
    }
});

// Mobile sidebar toggle
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

document.querySelectorAll('.sidebar a').forEach(a => {
    a.addEventListener('click', () => {
        if (window.innerWidth <= 768) {
            document.getElementById('sidebar').classList.remove('open');
        }
    });
});

// Language change handler — re-render active page and refresh labels
window.addEventListener('langchange', () => {
    refreshThemeLabel();
    refreshWsStatus();
    const activePage = localStorage.getItem('admin_active_page') || 'dashboard';
    showPage(activePage);
});

// Expose globals for onclick handlers in HTML
window._app = { login, logout, showPage, closeModal, toggleSidebar, toggleTheme, toggleLang, toggleSidebarGroup };
