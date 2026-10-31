# ─────────────────────────────────────────────────────────────────
# MatriSetu — 3D visual theme (UI only)
# Adds a CSS + JS layer to the HTML pages after they are rendered.
# It does not change any route, form, query or data.
# To switch the theme off, set THEME_3D_ENABLED = False.
# ─────────────────────────────────────────────────────────────────
import re
from flask import request

THEME_3D_ENABLED = True
APP_PATHS = ("/dashboard", "/supervisor", "/bmo", "/admin")
LOGIN_PATHS = ("/login", "/admin/login")
MARK = "matrisetu3d.css"
VER = "2"


def _mode(path):
    if path == "/":
        return "m3-landing"
    if path in LOGIN_PATHS:
        return "m3-login"
    if path.startswith(APP_PATHS):
        return "m3-app"
    return None


def apply_theme(resp):
    try:
        if not THEME_3D_ENABLED:
            return resp
        if resp.status_code != 200 or resp.mimetype != "text/html" or resp.direct_passthrough:
            return resp
        mode = _mode(request.path)
        if not mode:
            return resp
        html = resp.get_data(as_text=True)
        if MARK in html or "</body>" not in html:
            return resp
        tags = (
            '<link rel="stylesheet" href="/static/matrisetu3d.css?v=%s">'
            "<script>document.documentElement.classList.add('%s')</script>"
            '<script defer src="/static/matrisetu3d.js?v=%s"></script>' % (VER, mode, VER)
        )
        m = re.search(r"<head[^>]*>", html, re.I)
        if m:
            html = html[: m.end()] + tags + html[m.end():]
        else:
            i = html.rfind("</body>")
            html = html[:i] + tags + html[i:]
        resp.set_data(html)
    except Exception:
        pass  # never break a page because of styling
    return resp
