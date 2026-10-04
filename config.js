/* VoiceStock - backend address. Edit BACKEND if the server address ever changes. */
(function () {
  var BACKEND = "https://voicestock-abinash.duckdns.org";
  var HOST = BACKEND.replace(/^https?:\/\//, "");
  var onBackend = location.host === HOST;
  window.API_BASE = onBackend ? "" : BACKEND;
  if (onBackend) return;                       // running on the server itself: nothing to change

  var realFetch = window.fetch;
  window.fetch = function (input, init) {
    if (typeof input === "string") {
      if (input.charAt(0) === "/" && input.charAt(1) !== "/") input = BACKEND + input;
      else if (input.indexOf(location.origin + "/") === 0) input = BACKEND + input.slice(location.origin.length);
    }
    return realFetch.call(this, input, init);
  };

  var RealWS = window.WebSocket;
  function fixWs(u) {
    if (typeof u !== "string") return u;
    if (u.charAt(0) === "/" && u.charAt(1) !== "/") return "wss://" + HOST + u;
    var m = u.match(/^wss?:\/\/([^\/]+)(\/.*)?$/);
    if (m && m[1] === location.host) return "wss://" + HOST + (m[2] || "");
    return u;
  }
  window.WebSocket = class extends RealWS {
    constructor(url, protocols) { super(fixWs(url), protocols); }
  };
})();
