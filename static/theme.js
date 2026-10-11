/* Applies the saved look before the page paints. Settings page changes it: PRC.set('dark'|'light'|'microsoft'). */
(function () {
  var KEY = 'prc_theme', ok = ['dark', 'light', 'microsoft'];
  function get() { try { var t = localStorage.getItem(KEY); return ok.indexOf(t) >= 0 ? t : 'dark'; } catch (e) { return 'dark'; } }
  function apply(t) { document.documentElement.setAttribute('data-theme', t); }
  apply(get());
  window.PRC = {
    themes: ok, get: get,
    set: function (t) { if (ok.indexOf(t) < 0) return; try { localStorage.setItem(KEY, t); } catch (e) {} apply(t); },
    pref: function (k, d) { try { var v = localStorage.getItem('prc_' + k); return v === null ? d : v; } catch (e) { return d; } },
    setPref: function (k, v) { try { localStorage.setItem('prc_' + k, v); } catch (e) {} }
  };
})();
