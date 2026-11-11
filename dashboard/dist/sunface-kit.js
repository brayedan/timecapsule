/* ============================================================================
   Sunface UI Kit — reusable helpers for Hermes dashboard tabs.
   Version 1.0.0. Vanilla JS, no framework, no external requests.
   Defines window.SunfaceKit. Every helper is defensive: it never throws
   on bad input, and it never touches the DOM at load time.

   Adoption: copy sunface-kit.js + sunface-kit.css into your plugin's
   dashboard/dist/. In your dist/index.js, load this file (see the
   plugin's index.js for the loader pattern) and call the helpers with
   local fallbacks — the tab must work if the kit file is not served.
   See sunface-ui-kit/README.md.
   ========================================================================== */
(function () {
  "use strict";

  var MS = { s: 1000, m: 60 * 1000, h: 3600 * 1000, d: 86400 * 1000 };

  /* Split a millisecond delta into the two largest whole units:
     "2d 4h", "45m", "30s". Always returns at least the smallest unit. */
  function parts(ms) {
    var abs = Math.abs(ms);
    var d = Math.floor(abs / MS.d);
    var h = Math.floor((abs % MS.d) / MS.h);
    var m = Math.floor((abs % MS.h) / MS.m);
    var s = Math.floor((abs % MS.m) / MS.s);
    if (d > 0) return d + "d" + (h > 0 ? " " + h + "h" : "");
    if (h > 0) return h + "h" + (m > 0 ? " " + m + "m" : "");
    if (m > 0) return m + "m" + (s > 0 ? " " + s + "s" : "");
    return s + "s";
  }

  function parseIso(iso) {
    if (!iso) return null;
    var t = Date.parse(iso);
    if (isNaN(t)) return null;
    return t;
  }

  var Kit = {
    version: "1.0.0",

    /* Relative time: "just now", "2m ago", "3h ago", "2d ago", "in 3h".
       nowMs is injectable (tests / the tab's 1s tick); defaults to now. */
    relTime: function (iso, nowMs) {
      var t = parseIso(iso);
      if (t === null) return iso || "?";
      var now = (typeof nowMs === "number") ? nowMs : Date.now();
      var diff = now - t;
      if (Math.abs(diff) < 5000) return "just now";
      if (diff >= 0) return parts(diff) + " ago";
      return "in " + parts(diff);
    },

    /* Live countdown for a deadline: {text, state, className} or null.
       text is "in 2d 4h" while the deadline is ahead, and "expired 3h
       ago" (or pastWord + " 3h ago", e.g. "arrived") once it has passed.
       state is "future" | "past" | "soon" (< 1h left) | "none".
       Returns null for a missing or unparseable date — the caller shows
       nothing rather than a wrong countdown. */
    countdown: function (iso, pastWord, nowMs) {
      var t = parseIso(iso);
      if (t === null) return null;
      var now = (typeof nowMs === "number") ? nowMs : Date.now();
      var diff = t - now;
      var word = pastWord || "expired";
      if (diff >= 0) {
        var state = diff < MS.h ? "soon" : "future";
        return {
          text: "in " + parts(diff),
          state: state,
          className: state === "soon" ? "sf-countdown sf-countdown-soon" : "sf-countdown"
        };
      }
      return {
        text: word + " " + parts(diff) + " ago",
        state: "past",
        className: "sf-countdown sf-countdown-expired"
      };
    },

    /* Status pill: one mapping for every status string the Forge uses.
       Accepts a string ("sealed", "open", "grace", "pending-deletion",
       "live", "unreachable", "open-unhandshaken", "unknown", "dead")
       or a boolean/null (true = live, false = unreachable,
       null = unknown — the device-fleet reachable field). Unknown
       strings fall through to the unknown pill with the raw word, so a
       new status never renders as nothing. */
    pill: function (status) {
      var s = status;
      if (s === true) s = "live";
      if (s === false) s = "unreachable";
      if (s === null || s === undefined || s === "") s = "unknown";
      var map = {
        "sealed": ["sealed", "sf-pill sf-pill-sealed"],
        "open": ["open", "sf-pill sf-pill-open"],
        "grace": ["in grace", "sf-pill sf-pill-grace"],
        "pending-deletion": ["pending deletion", "sf-pill sf-pill-dead"],
        "live": ["live", "sf-pill sf-pill-live"],
        "reachable": ["live", "sf-pill sf-pill-live"],
        "unreachable": ["unreachable", "sf-pill sf-pill-unreachable"],
        "open-unhandshaken": ["open, unhandshaken", "sf-pill sf-pill-unhandshaken"],
        "unknown": ["unknown", "sf-pill sf-pill-unknown"],
        "dead": ["dead", "sf-pill sf-pill-dead"]
      };
      var hit = map[String(s)];
      if (hit) return { text: hit[0], className: hit[1] };
      return { text: String(s), className: "sf-pill sf-pill-unknown" };
    },

    /* Toast: a transient notice stacked in the corner. kind is
       "success" | "error" | "notice" (default). The message is set as
       text, never HTML — no injection surface. Auto-dismisses after
       4s. Returns the element, or null when there is no document. */
    toast: function (message, kind) {
      if (typeof document === "undefined" || !document.body) return null;
      var k = (kind === "success" || kind === "error" || kind === "notice") ? kind : "notice";
      var stack = document.querySelector(".sf-toast-stack");
      if (!stack) {
        stack = document.createElement("div");
        stack.className = "sf-toast-stack";
        document.body.appendChild(stack);
      }
      var toast = document.createElement("div");
      toast.className = "sf-toast sf-toast-" + k;
      toast.textContent = String(message == null ? "" : message);
      stack.appendChild(toast);
      setTimeout(function () {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
        if (stack.parentNode && !stack.hasChildNodes()) stack.parentNode.removeChild(stack);
      }, 4000);
      return toast;
    },

    /* Field validator: returns an error string, or null when the value
       passes. rules: {required, maxLength, minLength, min, max,
       pattern (RegExp or string), label}. The first failing rule wins,
       so the message is always the most specific one. */
    validateField: function (value, rules) {
      var r = rules || {};
      var label = r.label || "This field";
      var str = (value === null || value === undefined) ? "" : String(value);
      var trimmed = str.trim();
      if (r.required && trimmed === "") return label + " is required.";
      if (r.maxLength != null && str.length > r.maxLength) {
        return label + " must be \u2264 " + r.maxLength + " characters (now " + str.length + ").";
      }
      if (r.minLength != null && trimmed.length > 0 && trimmed.length < r.minLength) {
        return label + " must be \u2265 " + r.minLength + " characters.";
      }
      if (r.min != null || r.max != null) {
        var n = Number(trimmed);
        if (trimmed === "" || isNaN(n)) return label + " must be a number.";
        if (r.min != null && n < r.min) return label + " must be \u2265 " + r.min + ".";
        if (r.max != null && n > r.max) return label + " must be \u2264 " + r.max + ".";
      }
      if (r.pattern) {
        var re = r.pattern instanceof RegExp ? r.pattern : new RegExp(r.pattern);
        if (trimmed !== "" && !re.test(trimmed)) return label + " has an invalid format.";
      }
      return null;
    }
  };

  window.SunfaceKit = Kit;
})();
