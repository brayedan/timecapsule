/* Time Capsule dashboard tab — the human layer for sealed messages.
   Plain IIFE, no build step. React comes from the Hermes plugin SDK.
   Reads the same store.json the agent tools use, via
   /api/plugins/timecapsule/. The seal rule, enforced by the backend:
   this tab NEVER shows the content of a sealed capsule — metadata
   only, until the capsule is open. Actions go through the engine's
   own methods; no button is offered for anything the capsule's state
   does not allow.

   Presentation uses the UI Kit (dist/ui-kit.js +
   dist/ui-kit.css), loaded best-effort at the bottom of this
   file. Every kit call has a local fallback, so the tab renders
   correctly with or without the kit — the kit is an upgrade, not a
   dependency. A 1-second tick keeps the countdowns live and picks up
   the kit helpers as soon as they arrive. */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  var React = SDK.React;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;
  var C = SDK.components;
  var API = "/api/plugins/timecapsule";
  var PLUGIN_NAME = "timecapsule";

  function el(type, props) {
    var children = Array.prototype.slice.call(arguments, 2);
    return React.createElement.apply(React, [type, props].concat(children));
  }

  /* ---- UI Kit, best-effort ------------------------------------
     Derive the dist/ base URL from this entry script's own URL and
     inject the kit script + stylesheet. If the host loads the entry in
     a way that hides its URL, nothing loads and the local fallbacks
     below render instead. The tab never hard-fails on a missing kit. */
  function loadKit() {
    try {
      var base = null;
      var cs = (typeof document !== "undefined") ? document.currentScript : null;
      if (cs && cs.src) base = cs.src.slice(0, cs.src.lastIndexOf("/") + 1);
      if (!base && typeof document !== "undefined" && document.scripts) {
        for (var i = 0; i < document.scripts.length; i++) {
          var src = document.scripts[i].src || "";
          if (src.indexOf(PLUGIN_NAME) !== -1 && src.slice(-9) === "index.js") {
            base = src.slice(0, src.lastIndexOf("/") + 1);
            break;
          }
        }
      }
      if (!base) return;
      if (!window.UiKit) {
        var s = document.createElement("script");
        s.src = base + "ui-kit.js";
        s.async = true;
        document.head.appendChild(s);
      }
      var link = document.createElement("link");
      link.rel = "stylesheet";
      link.href = base + "ui-kit.css";
      document.head.appendChild(link);
    } catch (e) { /* the kit is an upgrade, not a dependency */ }
  }

  function kit() { return (typeof window !== "undefined" && window.UiKit) || null; }

  function timeAgo(iso) {
    try { return SDK.utils.isoTimeAgo(iso); } catch (e) { return iso || "?"; }
  }

  /* Relative time: the kit's renderer when available (works without the
     SDK), else the SDK's isoTimeAgo. */
  function rel(iso, nowMs) {
    var K = kit();
    if (K) return K.relTime(iso, nowMs);
    return timeAgo(iso);
  }

  /* Status pill: the kit's mapping when available, else the tab's own
     dot rendering. Both return {text, className}. */
  function pillFor(status) {
    var K = kit();
    if (K) return K.pill(status);
    var text = status === "open" ? "open"
      : status === "grace" ? "in grace"
      : status === "pending-deletion" ? "pending deletion"
      : "sealed";
    var cls = status === "open" ? "timecapsule-dot-open"
      : status === "grace" ? "timecapsule-dot-grace"
      : status === "pending-deletion" ? "timecapsule-dot-dead"
      : "timecapsule-dot-sealed";
    return { text: "● " + text, className: cls };
  }

  /* Live countdown for a deadline: the kit's renderer when available
     (ticks every second via the now state), else a plain relative time.
     Returns {text, className} or null when there is no date. */
  function countdownFor(iso, pastWord, nowMs) {
    if (!iso) return null;
    var K = kit();
    if (K) {
      var c = K.countdown(iso, pastWord, nowMs);
      if (!c) return null;
      return { text: c.text, className: c.className };
    }
    return { text: rel(iso, nowMs), className: "sf-countdown" };
  }

  /* Field validation: the kit's validator when available, else a small
     local equivalent (required / maxLength / numeric range). Returns an
     error string or null. */
  function fieldError(value, rules) {
    var K = kit();
    if (K) return K.validateField(value, rules);
    var label = rules.label || "This field";
    var str = (value === null || value === undefined) ? "" : String(value);
    if (rules.required && str.trim() === "") return label + " is required.";
    if (rules.maxLength != null && str.length > rules.maxLength) {
      return label + " must be ≤ " + rules.maxLength + " characters (now " + str.length + ").";
    }
    if (rules.min != null || rules.max != null) {
      var n = Number(str.trim());
      if (str.trim() === "" || isNaN(n)) return label + " must be a number.";
      if (rules.min != null && n < rules.min) return label + " must be ≥ " + rules.min + ".";
      if (rules.max != null && n > rules.max) return label + " must be ≤ " + rules.max + ".";
    }
    return null;
  }

  function toast(message, kind) {
    var K = kit();
    if (K) K.toast(message, kind);
  }

  function statusDot(status) {
    if (status === "open") return "● open";
    if (status === "grace") return "◐ in grace";
    if (status === "pending-deletion") return "○ pending deletion";
    return "● sealed";
  }

  function statusClass(status) {
    if (status === "open") return "timecapsule-dot-open";
    if (status === "grace") return "timecapsule-dot-grace";
    if (status === "pending-deletion") return "timecapsule-dot-dead";
    return "timecapsule-dot-sealed";
  }

  function conditionText(capsule, lastActivity, nowMs) {
    var cond = capsule.condition || {};
    if (cond.type === "date") {
      var cd = countdownFor(cond.date, "arrived", nowMs);
      return "Opens " + (cd ? cd.text : "after " + (cond.date || "?")) + " — " + (cond.date || "?");
    }
    if (cond.type === "silence") {
      return "Opens after " + cond.days + " days of silence — last activity " +
        (lastActivity ? rel(lastActivity, nowMs) : "never");
    }
    if (cond.type === "manual") {
      return "Opens only when the sealer releases it" +
        (capsule.sealed_by ? " — sealed by " + capsule.sealed_by : " — no sealer recorded");
    }
    return "Unknown condition";
  }

  function CapsulePage() {
    var _a = useState(null), list = _a[0], setList = _a[1];
    var _b = useState(null), detail = _b[0], setDetail = _b[1];
    var _c = useState(null), statusData = _c[0], setStatusData = _c[1];
    var _d = useState(""), error = _d[0], setError = _d[1];
    var _e = useState(""), notice = _e[0], setNotice = _e[1];
    var _f = useState(false), busy = _f[0], setBusy = _f[1];
    var _g = useState(false), composing = _g[0], setComposing = _g[1];
    var _h = useState(""), author = _h[0], setAuthor = _h[1];
    var _i = useState(""), sealLabel = _i[0], setSealLabel = _i[1];
    var _j = useState(""), sealMessage = _j[0], setSealMessage = _j[1];
    var _k = useState("date"), sealCondition = _k[0], setSealCondition = _k[1];
    var _l = useState(""), sealDate = _l[0], setSealDate = _l[1];
    var _m = useState("7"), sealSilence = _m[0], setSealSilence = _m[1];
    var _n = useState("3"), sealGrace = _n[0], setSealGrace = _n[1];
    var _o = useState("7"), sealDelete = _o[0], setSealDelete = _o[1];
    var _p = useState(""), sealAuthor = _p[0], setSealAuthor = _p[1];
    /* 1-second tick: keeps the countdowns live and picks up the kit
       helpers as soon as the kit script arrives. */
    var _q = useState(Date.now()), nowMs = _q[0], setNowMs = _q[1];

    var loadAll = useCallback(function () {
      SDK.fetchJSON(API + "/capsules")
        .then(function (d) { if (d.error) { setError(d.error); } else { setList(d); setError(""); } })
        .catch(function (e) { setError(String(e && e.message || e)); });
      SDK.fetchJSON(API + "/status")
        .then(function (d) { if (!d.error) { setStatusData(d); } })
        .catch(function () {});
    }, []);

    useEffect(function () { loadAll(); loadKit(); }, [loadAll]);
    useEffect(function () {
      var t = setInterval(loadAll, 30000);
      return function () { clearInterval(t); };
    }, [loadAll]);
    useEffect(function () {
      var t = setInterval(function () { setNowMs(Date.now()); }, 1000);
      return function () { clearInterval(t); };
    }, []);

    function openDetail(capsuleId) {
      SDK.fetchJSON(API + "/capsules/" + encodeURIComponent(capsuleId))
        .then(function (d) {
          if (d.error) { setError(d.error); }
          else { setDetail(d); setError(""); setNotice(""); }
        })
        .catch(function (e) { setError(String(e && e.message || e)); });
    }

    function refreshDetail(capsuleId) {
      SDK.fetchJSON(API + "/capsules/" + encodeURIComponent(capsuleId))
        .then(function (d) { if (!d.error) { setDetail(d); } else { setDetail(null); } })
        .catch(function () {});
    }

    /* An action result is a refusal (shown as an error) unless it is the
       manual-release transition into grace, which the engine reports as
       a CapsuleError even though the release succeeded — that one is a
       notice, not a failure. Every outcome also fires a toast. */
    function handleActionResult(d, capsuleId, okLine) {
      setBusy(false);
      if (d.error) {
        if (d.error.indexOf("released into grace") !== -1) {
          setNotice(d.error); setError(""); toast(d.error, "notice");
        } else {
          setError(d.error); setNotice(""); toast(d.error, "error");
        }
      } else {
        var line = okLine || (d.note || "Done.");
        setNotice(line); setError(""); toast(line, "success");
      }
      loadAll();
      if (capsuleId) { refreshDetail(capsuleId); }
    }

    function postAction(path, body, capsuleId, okLine) {
      if (busy) return;
      setBusy(true);
      setError(""); setNotice("");
      SDK.fetchJSON(API + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      })
        .then(function (d) { handleActionResult(d, capsuleId, okLine); })
        .catch(function (e) { setBusy(false); setError(String(e && e.message || e)); toast(String(e && e.message || e), "error"); });
    }

    /* Live validation for the seal form, computed on every render from
       the current field values. The Seal button is disabled while any
       error exists, so the form cannot be submitted invalid. */
    var sealErrors = {};
    sealErrors.label = fieldError(sealLabel, { maxLength: 100, label: "Label" });
    sealErrors.message = fieldError(sealMessage, { required: true, maxLength: 20000, label: "Message" });
    if (sealCondition === "date") {
      sealErrors.date = fieldError(sealDate, { required: true, label: "Release date" });
    }
    if (sealCondition === "silence") {
      sealErrors.silence = fieldError(sealSilence, { min: 1, max: 365, label: "Silence days" });
    }
    sealErrors.grace = fieldError(sealGrace, { min: 0, max: 30, label: "Grace days" });
    sealErrors.delete = fieldError(sealDelete, { min: 1, max: 30, label: "Delete countdown" });
    var sealInvalid = Object.keys(sealErrors).some(function (k) { return sealErrors[k]; });

    function submitSeal() {
      if (!sealMessage.trim() || busy || sealInvalid) return;
      var payload = {
        message: sealMessage,
        release_condition: sealCondition,
        grace_days: parseInt(sealGrace, 10) || 0,
        delete_days: parseInt(sealDelete, 10) || 7,
        author: sealAuthor.trim(),
        label: sealLabel.trim(),
      };
      if (sealCondition === "date") { payload.release_date = sealDate.trim() || undefined; }
      if (sealCondition === "silence") { payload.silence_days = parseInt(sealSilence, 10) || 7; }
      setBusy(true);
      setError(""); setNotice("");
      SDK.fetchJSON(API + "/seal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
        .then(function (d) {
          setBusy(false);
          if (d.error) { setError(d.error); setNotice(""); toast(d.error, "error"); return; }
          var line = "Sealed " + d.id + " (" + d.status + ").";
          setNotice(line); setError(""); toast(line, "success");
          setSealMessage(""); setSealLabel(""); setSealDate("");
          setComposing(false);
          loadAll();
        })
        .catch(function (e) { setBusy(false); setError(String(e && e.message || e)); toast(String(e && e.message || e), "error"); });
    }

    var capsules = (list && list.capsules) || [];
    var counts = (statusData && statusData.counts) || {};
    var deletions = (statusData && statusData.deletions) || [];
    var lastActivity = statusData && statusData.last_activity_at;

    function pillEl(status) {
      var p = pillFor(status);
      return el("span", { className: p.className }, p.text);
    }

    function countdownEl(iso, pastWord) {
      var c = countdownFor(iso, pastWord, nowMs);
      if (!c) return null;
      return el("span", { className: c.className }, c.text);
    }

    return el("div", { className: "timecapsule-page" },
      el(C.Card, null,
        el(C.CardHeader, null,
          el(C.CardTitle, null, "☀️ Time Capsule"),
          el("p", { className: "text-sm text-muted-foreground" },
            "Sealed messages for later — metadata only until a capsule opens. " +
            "The seal is access control, not cryptography: the content sits in plaintext in store.json, " +
            "and this tab never shows the content of a sealed capsule. Seal intentions, messages, and plans — not credentials.")
        ),
        el(C.CardContent, null,
          error ? el("p", { className: "timecapsule-error" }, error) : null,
          notice ? el("p", { className: "timecapsule-notice" }, notice) : null,
          el("div", { className: "timecapsule-row" },
            el(C.Button, { onClick: function () { setDetail(null); loadAll(); } }, "Capsules"),
            el(C.Button, { onClick: function () { setComposing(true); setDetail(null); } }, "Seal a capsule"),
            el(C.Button, { onClick: loadAll }, "Refresh")
          ),
          el("div", { className: "timecapsule-row" },
            el(C.Input, {
              value: author, placeholder: "Acting as (author, e.g. hyperion)…",
              onChange: function (e) { setAuthor(e.target.value); },
            }),
            el("span", { className: "text-sm text-muted-foreground" },
              "Sealed: " + (counts.sealed || 0) + " · Grace: " + (counts.grace || 0) +
              " · Open: " + (counts.open || 0) + " · Pending deletion: " + (counts["pending-deletion"] || 0) +
              " · Deletions announced: " + (statusData ? statusData.deletions_announced : 0))
          ),
          detail
            ? el("div", null,
                el(C.Button, { onClick: function () { setDetail(null); } }, "← Back to capsules"),
                el("h2", { className: "timecapsule-detail-title" }, detail.label || detail.id),
                el("p", { className: "text-sm text-muted-foreground" },
                  detail.id + " · sealed " + rel(detail.sealed_at, nowMs) +
                  (detail.sealed_by ? " by " + detail.sealed_by : "")),
                el("div", { className: "timecapsule-detail-grid" },
                  el("div", null, el("strong", null, "Status: "), pillEl(detail.status)),
                  el("div", null, el("strong", null, "Condition: "), conditionText(detail, lastActivity, nowMs)),
                  detail.grace_ends_at
                    ? el("div", null, el("strong", null, "Grace ends: "),
                        countdownEl(detail.grace_ends_at, "expired"),
                        el("span", { className: "text-sm text-muted-foreground" }, " (" + detail.grace_ends_at + ")"))
                    : null,
                  detail.delete_after
                    ? el("div", null, el("strong", null, "Deleted after: "),
                        countdownEl(detail.delete_after, "expired"),
                        el("span", { className: "text-sm text-muted-foreground" }, " (" + detail.delete_after + ")"))
                    : null,
                  detail.opened_at
                    ? el("div", null, el("strong", null, "Opened: "), rel(detail.opened_at, nowMs))
                    : null,
                  el("div", null, el("strong", null, "Grace: "), detail.grace_days + " day(s)")
                ),
                detail.content
                  ? el("div", { className: "timecapsule-content" },
                      el("h3", null, "Contents — released"),
                      el("p", null, detail.content))
                  : el("p", { className: "text-sm text-muted-foreground" },
                      "Sealed — the content is not shown until this capsule opens."),
                el("div", { className: "timecapsule-row" },
                  detail.status === "sealed" && detail.condition && detail.condition.type === "manual"
                    ? el(C.Button, {
                        onClick: function () {
                          postAction("/capsules/" + encodeURIComponent(detail.id) + "/open",
                            { author: author.trim() }, detail.id,
                            "Released " + detail.id + ".");
                        },
                        disabled: busy,
                      }, busy ? "Releasing…" : "Release (as sealer)")
                    : null,
                  detail.status === "grace"
                    ? el(C.Button, {
                        onClick: function () {
                          postAction("/capsules/" + encodeURIComponent(detail.id) + "/reseal",
                            { author: author.trim() }, detail.id,
                            "Re-sealed " + detail.id + ".");
                        },
                        disabled: busy,
                      }, busy ? "Re-sealing…" : "Re-seal")
                    : null,
                  detail.status === "pending-deletion"
                    ? el(C.Button, {
                        onClick: function () {
                          postAction("/capsules/" + encodeURIComponent(detail.id) + "/restore",
                            {}, detail.id, "Restored " + detail.id + ".");
                        },
                        disabled: busy,
                      }, busy ? "Restoring…" : "Restore")
                    : null,
                  detail.status !== "pending-deletion"
                    ? el(C.Button, {
                        onClick: function () {
                          postAction("/capsules/" + encodeURIComponent(detail.id) + "/delete",
                            {}, detail.id, "Deletion countdown started for " + detail.id + ".");
                        },
                        disabled: busy,
                      }, busy ? "Deleting…" : "Delete (slow, announced)")
                    : null
                )
              )
            : el("div", null,
                composing
                  ? el("div", { className: "timecapsule-form" },
                      el("div", null,
                        el(C.Input, {
                          value: sealLabel, placeholder: "Label (≤100 chars)…",
                          onChange: function (e) { setSealLabel(e.target.value); },
                          className: sealErrors.label ? "timecapsule-draft-invalid" : undefined,
                        }),
                        sealErrors.label ? el("p", { className: "timecapsule-field-error" }, sealErrors.label) : null
                      ),
                      el("div", null,
                        el("textarea", {
                          className: "timecapsule-draft" + (sealErrors.message ? " timecapsule-draft-invalid" : ""),
                          rows: 4, value: sealMessage,
                          placeholder: "The message for later…",
                          onChange: function (e) { setSealMessage(e.target.value); },
                        }),
                        sealErrors.message ? el("p", { className: "timecapsule-field-error" }, sealErrors.message) : null
                      ),
                      el("div", { className: "timecapsule-row" },
                        el("select", {
                          className: "timecapsule-select", value: sealCondition,
                          onChange: function (e) { setSealCondition(e.target.value); },
                        },
                          el("option", { value: "date" }, "Release on a date"),
                          el("option", { value: "silence" }, "Release after silence"),
                          el("option", { value: "manual" }, "Release manually (sealer only)")
                        ),
                        sealCondition === "date"
                          ? el("div", null,
                              el(C.Input, {
                                type: "datetime-local", value: sealDate,
                                onChange: function (e) { setSealDate(e.target.value); },
                                className: sealErrors.date ? "timecapsule-draft-invalid" : undefined,
                              }),
                              sealErrors.date ? el("p", { className: "timecapsule-field-error" }, sealErrors.date) : null
                            )
                          : null,
                        sealCondition === "silence"
                          ? el("div", null,
                              el(C.Input, {
                                type: "number", value: sealSilence, min: 1, max: 365,
                                onChange: function (e) { setSealSilence(e.target.value); },
                                className: sealErrors.silence ? "timecapsule-draft-invalid" : undefined,
                              }),
                              sealErrors.silence ? el("p", { className: "timecapsule-field-error" }, sealErrors.silence) : null
                            )
                          : null
                      ),
                      el("div", { className: "timecapsule-row" },
                        el("label", { className: "text-sm text-muted-foreground" }, "Grace days"),
                        el("div", null,
                          el(C.Input, {
                            type: "number", value: sealGrace, min: 0, max: 30,
                            onChange: function (e) { setSealGrace(e.target.value); },
                            className: sealErrors.grace ? "timecapsule-draft-invalid" : undefined,
                          }),
                          sealErrors.grace ? el("p", { className: "timecapsule-field-error" }, sealErrors.grace) : null
                        ),
                        el("label", { className: "text-sm text-muted-foreground" }, "Delete countdown (days)"),
                        el("div", null,
                          el(C.Input, {
                            type: "number", value: sealDelete, min: 1, max: 30,
                            onChange: function (e) { setSealDelete(e.target.value); },
                            className: sealErrors.delete ? "timecapsule-draft-invalid" : undefined,
                          }),
                          sealErrors.delete ? el("p", { className: "timecapsule-field-error" }, sealErrors.delete) : null
                        ),
                        el(C.Input, {
                          value: sealAuthor, placeholder: "Sealed by (author)…",
                          onChange: function (e) { setSealAuthor(e.target.value); },
                        })
                      ),
                      el("div", { className: "timecapsule-row" },
                        el(C.Button, { onClick: submitSeal, disabled: busy || sealInvalid }, busy ? "Sealing…" : "Seal it"),
                        el(C.Button, { onClick: function () { setComposing(false); } }, "Cancel")
                      )
                    )
                  : null,
                el("div", { className: "timecapsule-grid" },
                  capsules.length === 0
                    ? el("p", { className: "text-sm text-muted-foreground" }, "No capsules yet. Seal one — the future will thank you.")
                    : capsules.map(function (c) {
                        var graceCd = c.grace_ends_at ? countdownFor(c.grace_ends_at, "expired", nowMs) : null;
                        var deleteCd = c.delete_after ? countdownFor(c.delete_after, "expired", nowMs) : null;
                        return el("div", {
                          key: c.id, className: "timecapsule-card",
                          onClick: function () { openDetail(c.id); },
                        },
                          el("div", { className: "timecapsule-card-head" },
                            el("strong", null, c.label || c.id),
                            el(C.Badge, null, c.id)
                          ),
                          el("div", { className: "timecapsule-card-line" }, pillEl(c.status)),
                          el("div", { className: "timecapsule-card-line text-sm text-muted-foreground" },
                            conditionText(c, lastActivity, nowMs)
                          ),
                          el("div", { className: "timecapsule-card-line text-sm text-muted-foreground" },
                            "Sealed: " + rel(c.sealed_at, nowMs) + (c.sealed_by ? " by " + c.sealed_by : "")
                          ),
                          graceCd
                            ? el("div", { className: "timecapsule-card-line" },
                                el("span", { className: "text-sm text-muted-foreground" }, "Grace ends "),
                                el("span", { className: graceCd.className }, graceCd.text))
                            : null,
                          deleteCd
                            ? el("div", { className: "timecapsule-card-line" },
                                el("span", { className: "text-sm text-muted-foreground" }, "Deleted "),
                                el("span", { className: deleteCd.className }, deleteCd.text))
                            : null
                        );
                      })
                )
              ),
          el("div", { className: "timecapsule-log" },
            el(C.Card, null,
              el(C.CardHeader, null, el(C.CardTitle, null, "Deletions announced")),
              el(C.CardContent, null,
                deletions.length === 0
                  ? el("p", { className: "text-sm text-muted-foreground" }, "Nothing deleted yet. When a deletion countdown ends, the announcement lives here permanently.")
                  : deletions.slice().reverse().map(function (entry) {
                      return el("div", { key: entry.id, className: "timecapsule-log-entry" },
                        el("span", null, entry.id + " · " + (entry.label || "(no label)")),
                        el("span", { className: "text-sm text-muted-foreground" },
                          " deleted " + rel(entry.deleted_at, nowMs) + " · " + entry.reason)
                      );
                    })
              )
            )
          ),
          el("p", { className: "text-sm text-muted-foreground" },
            "No action button appears for anything a capsule's state does not allow: a sealed date or silence capsule has no Release button until its condition arrives; a capsule in grace can be re-sealed, not opened early. The tab reads the same store.json the tools use — there is no second copy of the state.")
        )
      )
    );
  }

  window.__HERMES_PLUGINS__.register("timecapsule", CapsulePage);
})();
