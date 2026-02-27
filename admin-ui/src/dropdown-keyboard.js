/**
 * Keyboard navigation for action dropdown menus.
 * Works with the existing pattern: trigger button → sibling .hidden menu.
 * Enhances all dropdowns matching `[data-dropdown-trigger]` or the
 * common pattern of ellipsis buttons with `.nextElementSibling` toggle.
 *
 * Auto-initializes via event delegation on document.
 */

/**
 * Initialize global keyboard support for dropdown menus.
 * Call once at app startup.
 */
export function initDropdownKeyboard() {
    // Close dropdowns on outside click
    document.addEventListener('click', (e) => {
        document.querySelectorAll('.dropdown-menu-open').forEach(menu => {
            if (!menu.contains(e.target) && !menu.previousElementSibling?.contains(e.target)) {
                menu.classList.add('hidden');
                menu.classList.remove('dropdown-menu-open');
                const trigger = menu.previousElementSibling;
                if (trigger) trigger.setAttribute('aria-expanded', 'false');
            }
        });
    });

    // Keyboard delegation for dropdown menus
    document.addEventListener('keydown', (e) => {
        const active = document.activeElement;
        if (!active) return;

        // Handle trigger button keyboard activation
        const menu = active.nextElementSibling;
        if (menu && menu.classList.contains('hidden') && isDropdownMenu(menu)) {
            if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                openDropdown(active, menu);
                return;
            }
        }

        // Handle navigation inside open menu
        const openMenu = active.closest('.dropdown-menu-open');
        if (openMenu) {
            handleMenuKeydown(e, openMenu);
        }
    });
}

function isDropdownMenu(el) {
    // Matches the common pattern: absolute positioned menu after trigger
    return el.classList.contains('absolute') || el.dataset.dropdownMenu !== undefined;
}

function openDropdown(trigger, menu) {
    menu.classList.remove('hidden');
    menu.classList.add('dropdown-menu-open');
    trigger.setAttribute('aria-expanded', 'true');
    // Focus first menu item
    const items = getMenuItems(menu);
    if (items.length > 0) items[0].focus();
}

function closeDropdown(menu) {
    menu.classList.add('hidden');
    menu.classList.remove('dropdown-menu-open');
    const trigger = menu.previousElementSibling;
    if (trigger) {
        trigger.setAttribute('aria-expanded', 'false');
        trigger.focus();
    }
}

function getMenuItems(menu) {
    return [...menu.querySelectorAll('button, a[href], [role="menuitem"]')].filter(
        el => el.offsetParent !== null
    );
}

function handleMenuKeydown(e, menu) {
    const items = getMenuItems(menu);
    const idx = items.indexOf(document.activeElement);

    switch (e.key) {
        case 'ArrowDown':
            e.preventDefault();
            if (idx < items.length - 1) items[idx + 1].focus();
            else items[0].focus(); // wrap
            break;

        case 'ArrowUp':
            e.preventDefault();
            if (idx > 0) items[idx - 1].focus();
            else items[items.length - 1].focus(); // wrap
            break;

        case 'Escape':
            e.preventDefault();
            e.stopPropagation(); // Don't close parent modal
            closeDropdown(menu);
            break;

        case 'Home':
            e.preventDefault();
            items[0]?.focus();
            break;

        case 'End':
            e.preventDefault();
            items[items.length - 1]?.focus();
            break;
    }
}
