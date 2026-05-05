from __future__ import annotations

import html
from string import Template
from typing import Any

from .config import TEMPLATE_DIR


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def render_template(template_name: str, **context: Any) -> str:
    template = Template((TEMPLATE_DIR / template_name).read_text(encoding="utf-8"))
    values = {key: "" if value is None else str(value) for key, value in context.items()}
    return template.safe_substitute(values)


def render_page(title: str, user: Any, body: str, flash: str = "") -> bytes:
    admin_link = ""
    nav = ""
    if user:
        if user["role"] == "admin":
            admin_link = '<a href="/admin/users">Accounts</a>'
        nav = render_template(
            "partials/nav.html",
            admin_link=admin_link,
            display_name=esc(user["display_name"]),
        )
    flash_html = f'<p class="flash">{esc(flash)}</p>' if flash else ""
    html_doc = render_template(
        "base.html",
        title=esc(title),
        nav=nav,
        flash=flash_html,
        body=body,
    )
    return html_doc.encode("utf-8")
