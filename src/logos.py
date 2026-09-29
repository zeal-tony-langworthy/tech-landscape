"""
Find SVG logos for landscape items.

Two open icon sets are searched, in this order:
  1. Devicon (https://devicon.dev, MIT): full-color logos for developer tools.
  2. Simple Icons (https://simpleicons.org, CC0): single-color brand icons,
     colored here with the brand's official hex color.

Each set publishes an index of its icons, so items are matched by name (and
by the aliases listed in the index) rather than by guessing file URLs. Only
exact name matches are used; anything else is reported for you to add by hand.

Note: the icon files are openly licensed, but the logos themselves are still
their owners' trademarks.
"""

import json
import re
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

DEVICON_INDEX = "https://raw.githubusercontent.com/devicons/devicon/master/devicon.json"
DEVICON_SVG = "https://raw.githubusercontent.com/devicons/devicon/master/icons/{name}/{name}-{version}.svg"
SIMPLE_ICONS_INDEX = "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/data/simple-icons.json"
SIMPLE_ICONS_SVG = "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/icons/{slug}.svg"

TIMEOUT = 20
MAX_SVG_BYTES = 1_000_000
HEADERS = {"User-Agent": "tech-landscape-skills-sync"}


def _get(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read(MAX_SVG_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def simple_icons_slug(title: str) -> str:
    """Simple Icons' own rule for turning a title into a file name."""
    s = title.lower().replace("+", "plus").replace(".", "dot").replace("&", "and")
    s = unicodedata.normalize("NFD", s)
    return re.sub(r"[^a-z0-9]", "", s)


def match_keys(name: str) -> set[str]:
    """The ways a name might be written in an icon index: 'Next.js' -> nextjs, nextdotjs."""
    lower = name.lower().strip()
    plain = re.sub(r"[^a-z0-9]", "", lower.replace("#", "sharp").replace("+", "plus"))
    # "#" -> "sharp" first, or "C#" would reduce to "c" and match the C language.
    keys = {plain, simple_icons_slug(lower.replace("#", "sharp"))}
    if lower.startswith("."):          # ".NET" -> "dotnet"
        keys.add("dot" + plain)
    return {k for k in keys if k}


def is_safe_svg(data: bytes) -> bool:
    """Reject anything that isn't a plain SVG file, including SVGs with scripts."""
    if not data or len(data) > MAX_SVG_BYTES:
        return False
    text = data.decode("utf-8", errors="ignore")
    head = text.lstrip()[:500].lower()
    if not (head.startswith("<svg") or (head.startswith("<?xml") and "<svg" in text.lower())):
        return False
    lowered = text.lower()
    return "<script" not in lowered and not re.search(r"\son[a-z]+\s*=", lowered) \
        and "javascript:" not in lowered


class LogoFinder:
    """Downloads each icon index once, then looks up items by name."""

    def __init__(self):
        self._devicon = None
        self._simple = None

    def _devicon_index(self) -> dict:
        if self._devicon is None:
            self._devicon = {}
            data = _get(DEVICON_INDEX)
            for icon in json.loads(data) if data else []:
                svgs = icon.get("versions", {}).get("svg", [])
                version = next((v for v in ("original", "plain") if v in svgs), None)
                if not version:
                    continue
                for alias in [icon["name"], *icon.get("altnames", [])]:
                    for key in match_keys(alias):
                        self._devicon.setdefault(key, (icon["name"], version))
        return self._devicon

    def _simple_index(self) -> dict:
        if self._simple is None:
            self._simple = {}
            data = _get(SIMPLE_ICONS_INDEX)
            icons = json.loads(data) if data else []
            if isinstance(icons, dict):  # older releases wrapped the list
                icons = icons.get("icons", [])
            for icon in icons:
                slug = icon.get("slug") or simple_icons_slug(icon["title"])
                names = [icon["title"], *icon.get("aliases", {}).get("aka", [])]
                for alias in names:
                    for key in match_keys(alias):
                        self._simple.setdefault(key, (slug, icon.get("hex")))
        return self._simple

    def find(self, name: str, hints: list[str] = ()) -> tuple[bytes, str] | None:
        """Return (svg_bytes, source) for the first match, or None.

        `hints` are extra names to try, such as the stem of the logo filename
        ("kafka.svg" -> "kafka"). Names are also tried with an "Apache" prefix,
        since both icon sets list e.g. Kafka and Spark as "Apache Kafka"/"Apache Spark"."""
        keys = []
        for n in [name, *hints]:
            for k in sorted(match_keys(n)):
                if k not in keys:
                    keys.append(k)
        keys += [f"apache{k}" for k in list(keys) if not k.startswith("apache")]

        for key in keys:
            hit = self._devicon_index().get(key)
            if hit:
                icon, version = hit
                data = _get(DEVICON_SVG.format(name=icon, version=version))
                if is_safe_svg(data):
                    return data, f"Devicon ({icon})"

        for key in keys:
            hit = self._simple_index().get(key)
            if hit:
                slug, hex_color = hit
                data = _get(SIMPLE_ICONS_SVG.format(slug=slug))
                if is_safe_svg(data):
                    if hex_color and b"fill=" not in data[:300]:
                        # Simple Icons are black by default; apply the brand color.
                        data = data.replace(b"<svg ", f'<svg fill="#{hex_color}" '.encode(), 1)
                    return data, f"Simple Icons ({slug})"
        return None


def ensure_logos(items, logos_dir: Path, placeholder: str | None = None):
    """For each item whose logo file is missing, download one if possible.

    Returns (downloaded, missing): lists of human-readable lines."""
    logos_dir.mkdir(parents=True, exist_ok=True)
    finder = LogoFinder()
    downloaded, missing = [], []

    for item in items:
        logo = item.get("logo")
        if logo and (logos_dir / logo).exists():
            continue

        if not logo:
            logo = re.sub(r"[^a-z0-9]+", "-", item["name"].lower()).strip("-") + ".svg"
            item["logo"] = logo

        stem = Path(logo).stem
        found = finder.find(item["name"], hints=[stem])
        if found:
            data, source = found
            if not logo.lower().endswith(".svg"):
                # The item points at a missing PNG or similar; switch it to the SVG.
                logo = f"{stem}.svg"
                item["logo"] = logo
            (logos_dir / logo).write_bytes(data)
            downloaded.append(f"{logo}  <-  {source}")
        elif placeholder:
            item["logo"] = placeholder
            missing.append(f"{item['name']}: no logo found; using {placeholder}")
        else:
            missing.append(f"{item['name']}: add {logo}")

    return downloaded, missing