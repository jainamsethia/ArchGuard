/**
 * The dashboard's shared mutable state, in one place.
 *
 * This lived on `window`: `window.latestRun`, `window.currentViolationsPage`,
 * `window.violationsPerPage`, `window.remediationSelectedKeys`,
 * `window.recentRuns`, `window._visNet` -- six globals written from a dozen
 * places across 2,174 lines, with nothing naming who owned them or when they
 * changed. Any script on the page, including a browser extension, could read
 * or overwrite any of them.
 *
 * A plain exported object rather than a framework store: every reader already
 * reads synchronously and re-renders on its own schedule, so subscriptions
 * would be machinery nothing asked for. What this buys is a single definition,
 * an obvious place to look, and state that tests can set and inspect without
 * standing up a page.
 */

export const state = {
  /** The most recent run from /api/v1/runs/latest, or null before first load. */
  latestRun: null,

  /** Runs behind the compare pickers and the trend chart. */
  recentRuns: [],

  /** Violations table paging. 1-based, to match what the buttons display. */
  currentViolationsPage: 1,
  violationsPerPage: 20,

  /**
   * Finding keys the remediation endpoint said it would actually send to the
   * model. The table marks these rows, so a user can see which of their
   * findings a plan covers.
   */
  remediationSelectedKeys: new Set(),

  /**
   * The vis-network instance, once the Dependencies tab has been opened. Held
   * so the graph is redrawn rather than rebuilt on every tab switch, and so
   * the 628 KB library is fetched at most once.
   */
  visNetwork: null,
};

/**
 * Reset to first load. Exists for tests: without it each one inherits whatever
 * the last left behind, and a paging test passes or fails depending on which
 * test ran before it.
 */
export function resetState() {
  state.latestRun = null;
  state.recentRuns = [];
  state.currentViolationsPage = 1;
  state.violationsPerPage = 20;
  state.remediationSelectedKeys = new Set();
  state.visNetwork = null;
}
