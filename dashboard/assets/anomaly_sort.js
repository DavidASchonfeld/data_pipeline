/**
 * anomaly_sort.js — single-click column sorting for both anomaly tables.
 *
 * Dash clientside callbacks run inside the browser, so there are no server
 * round-trips needed for column sorting or clearing the sort.
 *
 * Registered as window.dash_clientside.anomaly_sort so Dash can call these
 * functions via ClientsideFunction(namespace, function_name).
 */

"use strict";

/**
 * Core sort-state updater used by both tables.
 *
 * @param {IArguments} args       - all n_clicks inputs followed by current sortState
 * @param {string}     prefix     - column-ID prefix, e.g. "anom-col-"
 * @param {string}     clearBtnId - ID of the × clear button for this table
 * @returns new sortState object, or dash_clientside.no_update when nothing changed
 */
function _handleSort(args, prefix, clearBtnId) {
    var ctx = window.dash_clientside.callback_context;
    if (!ctx || !ctx.triggered || !ctx.triggered.length) {
        return window.dash_clientside.no_update;
    }

    /* Current sort state (default: no sort, ascending direction) */
    var sortState   = args[args.length - 1] || { column: null, direction: "asc" };

    /* Which element was clicked, e.g. "anom-col-ticker" or "anom-sort-clear-btn" */
    var triggeredId = ctx.triggered[0].prop_id.split(".")[0];

    /* × clear button was clicked — reset sort immediately */
    if (triggeredId === clearBtnId) {
        return { column: null, direction: "asc" };
    }

    /* Which column was clicked, e.g. "anom-col-ticker" → "ticker" */
    var clickedCol  = triggeredId.replace(prefix, "");

    if (clickedCol === sortState.column) {
        /* Active column — single-click flips sort direction (asc ↔ desc) */
        return Object.assign({}, sortState, {
            direction: sortState.direction === "asc" ? "desc" : "asc"
        });
    }

    /* Inactive column — single-click makes it the new sort column (ascending) */
    return { column: clickedCol, direction: "asc" };
}

/* Register under window.dash_clientside so Dash can call via ClientsideFunction */
window.dash_clientside = Object.assign({}, window.dash_clientside, {
    anomaly_sort: {
        /* Stocks table — 6 column n_clicks + clear button n_clicks + sortState */
        handleStocksSort: function () {
            return _handleSort(arguments, "anom-col-", "anom-sort-clear-btn");
        },
        /* Weather table — 6 column n_clicks + clear button n_clicks + sortState */
        handleWeatherSort: function () {
            return _handleSort(arguments, "wanom-col-", "wanom-sort-clear-btn");
        }
    }
});
