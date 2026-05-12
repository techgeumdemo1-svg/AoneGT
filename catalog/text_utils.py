import html
import re

from django.utils.html import strip_tags


_BR_RE = re.compile(r'<br\s*/?>', re.IGNORECASE)
_BLOCK_CLOSE_RE = re.compile(r'</(?:div|p|li|h[1-6])\s*>', re.IGNORECASE)
_BLOCK_OPEN_RE = re.compile(r'<(?:div|p|li|h[1-6])\b[^>]*>', re.IGNORECASE)


def html_to_plain_text(value) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''

    raw = _BR_RE.sub('\n', raw)
    raw = _BLOCK_CLOSE_RE.sub('\n', raw)
    raw = _BLOCK_OPEN_RE.sub('', raw)
    text = html.unescape(strip_tags(raw))
    lines = (' '.join(line.split()) for line in text.splitlines())
    return '\n'.join(line for line in lines if line)
