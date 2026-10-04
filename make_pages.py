"""
VoiceStock - make_pages.py

Turns the pages that run on the server (login.html, voice.html, wholesaler.html,
delivery.html) into a version that works from GitHub Pages while the backend stays on
https://voicestock-abinash.duckdns.org.

Usage (from the repo folder):
    python make_pages.py site_src .

  site_src : folder that holds the html files copied from the server
  .        : where to write the result (the folder GitHub Pages serves)

Safe to run again and again. Only .html files in the source folder are processed, so you can
also point it at a folder that holds just one updated page.

What it changes in each page:
  1. adds <script src="config.js"> first, which sends every backend call
     (fetch + the voice websocket) to the backend when the page is not on the backend itself
  2. page links like  location.href = "/login"  become  "login.html"
  3. "/static/..." (images) point at the backend
  4. product photos get the backend address in front
login.html is also saved as index.html so the GitHub address opens the login page.
"""
import re
import sys
from pathlib import Path

BACKEND = "https://voicestock-abinash.duckdns.org"

CONFIG_JS = """/* VoiceStock - backend address. Edit BACKEND if the server address ever changes. */
(function () {
  var BACKEND = "%(backend)s";
  var HOST = BACKEND.replace(/^https?:\\/\\//, "");
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
    var m = u.match(/^wss?:\\/\\/([^\\/]+)(\\/.*)?$/);
    if (m && m[1] === location.host) return "wss://" + HOST + (m[2] || "");
    return u;
  }
  window.WebSocket = class extends RealWS {
    constructor(url, protocols) { super(fixWs(url), protocols); }
  };
})();
""" % {"backend": BACKEND}


def convert(html, stems):
    notes = []

    # 1. config.js first thing in <head>
    if 'src="config.js"' not in html:
        tag = '<script src="config.js"></script>'
        m = re.search(r"<head[^>]*>", html, re.I)
        html = html[:m.end()] + "\n" + tag + html[m.end():] if m else tag + "\n" + html

    # 2. page links -> relative html files
    pages = {s: s + ".html" for s in stems}
    n = 0

    def page_sub(m):
        nonlocal n
        n += 1
        return m.group(1) + m.group(2) + pages[m.group(3)] + m.group(2)

    names = "|".join(sorted(map(re.escape, stems), key=len, reverse=True))
    # location.href = "/login"   location.replace("/login")   location.assign("/login")
    html = re.sub(r"""(location(?:\.href\s*=\s*|\.(?:replace|assign)\(\s*))(["'`])/(%s)\2""" % names, page_sub, html)
    # <a href="/login">
    html = re.sub(r"""(\bhref\s*=\s*)(["'])/(%s)\2""" % names, page_sub, html)
    # exact page paths that are never API calls (role -> page tables etc.)
    for stem in stems:
        if stem == "login":
            continue          # "/login" is also an API path, handled only in the cases above
        def sub2(m, stem=stem):
            nonlocal n
            n += 1
            return m.group(1) + pages[stem] + m.group(1)
        html = re.sub(r"""(["'`])/%s\1""" % re.escape(stem), sub2, html)
    notes.append(f"{n} page link(s) changed")

    # 3. /static/... points at the backend
    html, k = re.subn(r"""(["'(])/static/""", r"\1" + BACKEND + "/static/", html)
    notes.append(f"{k} /static path(s) changed")

    # 4. product photos from the API:  src="${p.image_url}"
    html, k = re.subn(r"""src="\$\{(\w+)\.image_url\}\"""", r'src="${(window.API_BASE||"")+\1.image_url}"', html)
    notes.append(f"{k} product photo(s) fixed")

    # anything left that still looks like a server page link
    left = sorted(set(re.findall(r"""["'`]/(?:%s)["'`]""" % names, html)))
    if left:
        notes.append("STILL CONTAINS (check these): " + ", ".join(left))
    return html, notes


def main():
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "site_src")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else ".")
    files = sorted(src.glob("*.html"))
    if not files:
        sys.exit(f"No .html files found in {src}. Copy the pages from the server first.")
    out.mkdir(parents=True, exist_ok=True)
    stems = sorted({f.stem for f in files} | {"login", "voice", "wholesaler", "delivery"})

    for f in files:
        if f.stem == "index":
            continue
        html, notes = convert(f.read_text(encoding="utf-8"), stems)
        (out / f.name).write_text(html, encoding="utf-8", newline="\n")
        print(f"{f.name}: " + "; ".join(notes))
        if f.stem == "login":
            (out / "index.html").write_text(html, encoding="utf-8", newline="\n")
            print("index.html: copy of login.html (opens first)")

    (out / "config.js").write_text(CONFIG_JS, encoding="utf-8", newline="\n")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    print("config.js, .nojekyll written")
    if not (src / "login.html").exists():
        print("WARNING: login.html was not in the source folder, so index.html was not created.")


if __name__ == "__main__":
    main()
