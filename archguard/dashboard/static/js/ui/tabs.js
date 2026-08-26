export function moveTabIndicator(tab, animate = true) {
    const list = tab.closest('.tablist');
    if (!list) return;
    // On the first placement there is nothing to travel from, so skip
    // the transition for one frame — otherwise the marker flies in
    // from the left edge on page load.
    if (!animate) list.classList.add('no-anim');
    // Measure with rects, not offsetLeft/offsetWidth: those round to
    // whole pixels, which leaves the thumb a pixel or two narrower
    // than the label it is supposed to sit under.
    const listRect = list.getBoundingClientRect();
    const tabRect = tab.getBoundingClientRect();
    list.style.setProperty('--tab-x', `${tabRect.left - listRect.left + list.scrollLeft}px`);
    list.style.setProperty('--tab-w', `${tabRect.width}px`);
    list.style.setProperty('--tab-o', '1');
    if (!animate) {
        // Force a reflow so the values above are committed with the
        // transition still disabled, then re-enable it.
        void list.offsetWidth;
        list.classList.remove('no-anim');
    }
}


export function initTabChrome() {
    const list = document.querySelector('.page-dashboard .tablist');
    if (!list) return;

    initTabKeyboard(list);
    initRovingTabindex(list);
    labelPanels(list);

    const reposition = (animate) => {
        const current = list.querySelector('.tab[aria-selected="true"]');
        if (current) moveTabIndicator(current, animate);
    };

    reposition(false);

    // The label is measured before the web font loads, so its width is
    // the fallback face's. When the real font swaps in the text
    // reflows and the thumb is left mis-sized — re-measure once fonts
    // have settled.
    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(() => reposition(false));
    }

    // Re-measure on resize: tab widths change with the font size and
    // the wrapping of the bar above them.
    let raf = 0;
    window.addEventListener('resize', () => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => reposition(false));
    });

    // Lift the control off the page only while content is actually
    // passing beneath it. A sentinel is cheaper and smoother than a
    // scroll handler, which would run every frame to recompute the
    // same boolean. The root is inset by the control's own sticky
    // offset so the shadow appears exactly as it pins, rather than a
    // few pixels late.
    const stickyTop = parseFloat(getComputedStyle(list).top) || 0;
    const sentinel = document.createElement('div');
    sentinel.setAttribute('aria-hidden', 'true');
    sentinel.style.cssText = 'height:1px;margin-bottom:-1px;';
    list.parentNode.insertBefore(sentinel, list);
    new IntersectionObserver(
        ([entry]) => list.classList.toggle('is-stuck', !entry.isIntersecting),
        { threshold: 0, rootMargin: `-${stickyTop}px 0px 0px 0px` }
    ).observe(sentinel);
}


export function switchTab(name) {
    document.querySelectorAll('.tab').forEach(t => {
        t.setAttribute('aria-selected', 'false');
        // Roving tabindex: only the selected tab is in the page tab order, so
        // Tab reaches the tablist once and the arrows move within it. With all
        // four tabbable, crossing the bar cost a keyboard user four stops.
        t.setAttribute('tabindex', '-1');
    });
    document.querySelectorAll('.tab-panel-main').forEach(p => p.classList.remove('active'));

    const targetTab = document.querySelector(`.tab[aria-controls="${name}"]`);
    if (targetTab) {
        targetTab.setAttribute('aria-selected', 'true');
        targetTab.setAttribute('tabindex', '0');
        moveTabIndicator(targetTab);
    }
    const targetPanel = document.getElementById(name);
    if (targetPanel) {
        targetPanel.classList.add('active');
    }

    // replaceState, not pushState: a tab is a view of the same page, and
    // pushing one entry per click meant Back walked the user through
    // every tab they had opened instead of leaving the page.
    history.replaceState({}, '', window.location.search + `#${name}`);

    // Lazy-load each tab's data here, not in the click handler: tabs are
    // also opened by URL hash on load and by hashchange, and those paths
    // would otherwise leave the panel stuck on its "Loading..." row.
    //
    // Announced rather than called. This module used to reach directly into
    // the dependency graph and the suppressions table, which made the tab
    // chrome depend on two feature modules that both depend on it back. main
    // registers what each tab should load; tabs only knows a tab was opened.
    const activate = activations.get(name);
    if (activate) activate();
}


/**
 * Arrow-key movement across the tablist, per the ARIA tabs pattern.
 *
 * There was no key handling at all: the only way to change tab was a click,
 * and all four tabs sat in the page tab order. Selection follows focus here,
 * which is the right choice for panels that are already loaded or load
 * cheaply -- the alternative (move focus, activate on Enter) exists for tabs
 * whose panels are expensive, and these are not.
 */
function initTabKeyboard(list) {
    const tabs = () => [...list.querySelectorAll('[role="tab"]')];

    list.addEventListener('keydown', (e) => {
        const all = tabs();
        const index = all.indexOf(document.activeElement);
        if (index === -1) return;

        let next = null;
        if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
            next = all[(index + 1) % all.length];
        } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
            next = all[(index - 1 + all.length) % all.length];
        } else if (e.key === 'Home') {
            next = all[0];
        } else if (e.key === 'End') {
            next = all[all.length - 1];
        } else {
            // Anything else belongs to the page. Swallowing every key here
            // would break typing anywhere the tablist happens to have focus.
            return;
        }

        e.preventDefault();
        const name = next.getAttribute('aria-controls');
        if (name) switchTab(name);
        next.focus();
    });
}


/**
 * Put the initially-selected tab into the page tab order and the rest out of it.
 *
 * `switchTab` maintains this from then on, but the first selection comes from
 * the template's `aria-selected`, so without this the page loads with no
 * tabbable tab at all -- Tab skips the tablist entirely.
 */
function initRovingTabindex(list) {
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    if (tabs.length === 0) return;
    const selected = tabs.find((t) => t.getAttribute('aria-selected') === 'true') || tabs[0];
    for (const tab of tabs) {
        tab.setAttribute('tabindex', tab === selected ? '0' : '-1');
    }
}


/**
 * Point each panel back at the tab that controls it.
 *
 * Done here rather than in the template so the two cannot drift: the
 * relationship is already declared once, by aria-controls.
 */
function labelPanels(list) {
    for (const tab of list.querySelectorAll('[role="tab"]')) {
        const panel = document.getElementById(tab.getAttribute('aria-controls'));
        if (panel && !panel.getAttribute('aria-labelledby')) {
            panel.setAttribute('aria-labelledby', tab.id);
        }
    }
}


/** Tab name -> what to load the first time it is shown. Wired by main. */
export const activations = new Map();

export function onTabActivated(name, handler) {
    activations.set(name, handler);
}


export function initNavigation() {
    // Quick links
    const fitnessLink = document.getElementById('chip-fitness');
    if (fitnessLink) fitnessLink.addEventListener('click', () => document.getElementById('fitness-container').scrollIntoView({behavior: 'smooth'}));

    const advisorLink = document.getElementById('chip-advisor');
    if (advisorLink) advisorLink.addEventListener('click', () => document.getElementById('advisor-panel').scrollIntoView({behavior: 'smooth'}));

    const evolutionLink = document.getElementById('chip-evolution');
    if (evolutionLink) evolutionLink.addEventListener('click', () => document.getElementById('evolution-trends-grid').scrollIntoView({behavior: 'smooth'}));

    // Tabs
    const overviewTab = document.getElementById('tab-overview');
    if (overviewTab) overviewTab.addEventListener('click', () => switchTab('overview'));

    const violationsTab = document.getElementById('tab-violations');
    if (violationsTab) violationsTab.addEventListener('click', () => switchTab('violations'));

    const depsTab = document.getElementById('tab-dependencies');
    if (depsTab) depsTab.addEventListener('click', () => switchTab('dependencies'));

    const suppressTab = document.getElementById('tab-suppressions');
    if (suppressTab) suppressTab.addEventListener('click', () => switchTab('suppressions'));
}
