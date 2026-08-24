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
    // aria-selected and tabindex move together. Leaving tabindex behind is
    // what put all four tabs in the page's tab order: role="tablist" tells a
    // screen-reader user the Arrow keys move between tabs and one Tab press
    // leaves the set, so four independent tab stops contradict the role the
    // markup is already claiming.
    document.querySelectorAll('.tab').forEach(t => {
        t.setAttribute('aria-selected', 'false');
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


/** Tab name -> what to load the first time it is shown. Wired by main. */
export const activations = new Map();

export function onTabActivated(name, handler) {
    activations.set(name, handler);
}


/**
 * The keyboard half of the tablist contract.
 *
 * Announcing a widget as a tablist tells assistive technology that the Arrow
 * keys move between tabs and that one Tab press leaves the whole set. The
 * roles were already in the markup; this is what makes them true.
 *
 * Automatic activation (selection follows focus), which is the ARIA APG
 * default and matches the existing click behaviour -- every panel's content is
 * already in the page, so moving selection costs nothing a user would notice.
 *
 * Delegated to the list rather than bound per tab: the tabs are static, but a
 * single listener keeps the roving tabindex and the key handling in one place.
 *
 * https://www.w3.org/WAI/ARIA/apg/patterns/tabs/
 */
export function initTabKeyboard() {
    const list = document.querySelector('.page-dashboard .tablist');
    if (!list) return;

    list.addEventListener('keydown', (e) => {
        const tabs = Array.from(list.querySelectorAll('.tab'));
        const from = tabs.indexOf(e.target.closest('.tab'));
        if (from === -1) return;

        let to;
        switch (e.key) {
            case 'ArrowRight': to = (from + 1) % tabs.length; break;
            case 'ArrowLeft':  to = (from - 1 + tabs.length) % tabs.length; break;
            case 'Home':       to = 0; break;
            case 'End':        to = tabs.length - 1; break;
            default: return;
        }

        // Only after a key we actually handle: preventing the default for
        // every keystroke would swallow typing anywhere the event bubbles
        // through, and Home/End would stop scrolling the page.
        e.preventDefault();
        const target = tabs[to];
        switchTab(target.getAttribute('aria-controls'));
        // switchTab deliberately does not move focus -- it also runs on load
        // from the URL fragment, where stealing focus would drop the user into
        // the tablist instead of the top of the page. Arrow-key navigation is
        // the one path where focus must follow.
        target.focus();
    });
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

    initTabKeyboard();
}
