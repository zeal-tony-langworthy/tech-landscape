import sys
from pathlib import Path
import helpers
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, LiteralScalarString
from constants import REVIEW_FILE
from review import slugify
from taxonomy import TAXONOMY
from logos import ensure_logos


# ---------------------------------------------------------------------------
# Step 3: apply
#
# Rebuilds data.yml and guide.yml from the reviewed file:
# - existing items move to their reviewed category/subcategory, keeping all of
#   their fields (URL, logo, repo_url, description, ...);
# - approved new items are added;
# - categories/subcategories follow the order in taxonomy.py, and empty ones
#   are left out.
# Nothing is written if any existing item isn't approved and placed, so no
# item can silently drop off the page.
# ---------------------------------------------------------------------------


def load_review(yaml) -> list:
    if not REVIEW_FILE.exists():
        sys.exit(f"{REVIEW_FILE} not found. Run `review` first.")
    entries = yaml.load(REVIEW_FILE.read_text()).get("skills") or []
    if not entries:
        sys.exit(f"{REVIEW_FILE} has no entries.")
    return entries


def placement_error(e) -> str | None:
    """Why this entry's category/subcategory can't be used, or None if it can."""
    if e.get("category") not in TAXONOMY:
        return f"category '{e.get('category')}' isn't in taxonomy.py"
    if not e.get("subcategory"):
        return "no subcategory"
    return None


def set_description(item, description):
    """Write a reviewed description to an existing item, right after its name."""
    if not description or item.get("description") == description:
        return
    if "description" in item:
        item["description"] = description
    else:
        item.insert(list(item.keys()).index("name") + 1, "description", description)


def build_structure(entries, landscape, args):
    """Group items by (category, subcategory). Stops on problems with existing items."""
    # If data.yml lists the same skill twice, keep the first one and drop the rest.
    items_by_name = {}
    for cat in landscape["landscape"]:
        for sub in cat.get("subcategories") or []:
            for item in sub.get("items") or []:
                key = helpers.normalize(item["name"])
                if key in items_by_name:
                    print(f"Removing duplicate '{item['name']}' from "
                          f"{cat['name']} / {sub['name']} (keeping the first one).")
                    continue
                items_by_name[key] = item
    reviewed_existing = set()
    groups: dict[tuple[str, str], list] = {}
    errors, skipped, added = [], [], []

    for e in entries:
        name = e.get("name")
        status = e.get("status")

        if status == "existing":
            key = helpers.normalize(e.get("current_name") or "")
            item = items_by_name.get(key)
            if item is None:
                errors.append(f"{e.get('current_name')}: listed as existing but not in data.yml")
                continue
            reviewed_existing.add(key)
            if not e.get("approve"):
                errors.append(f"{name}: existing item not approved")
                continue
            problem = placement_error(e)
            if problem:
                errors.append(f"{name}: {problem}")
                continue
            item["name"] = name  # may be a reviewed rename
            set_description(item, e.get("description"))
            groups.setdefault((e["category"], e["subcategory"]), []).append(item)

        elif status == "new":
            if not e.get("approve"):
                continue
            problem = placement_error(e) or (None if e.get("homepage_url") else "no homepage_url")
            if problem:
                skipped.append(f"{name}: {problem}")
                continue
            logo = e.get("logo") or f"{slugify(name)}.svg"
            item = CommentedMap()
            item["item"] = None  # Landscape2's "- item:" marker
            item["name"] = name
            if e.get("description"):
                item["description"] = e["description"]
            item["homepage_url"] = e["homepage_url"]
            item["logo"] = logo
            groups.setdefault((e["category"], e["subcategory"]), []).append(item)
            added.append(f"{name}  ->  {e['category']} / {e['subcategory']}")

        else:
            errors.append(f"{name}: unknown status '{status}'")

    for key, item in items_by_name.items():
        if key not in reviewed_existing:
            errors.append(f"{item['name']}: in data.yml but not in {REVIEW_FILE} (rerun `review`)")

    # Two entries can't end up with the same name.
    seen = {}
    for (cat, sub), items in groups.items():
        for item in items:
            norm = helpers.normalize(item["name"])
            if norm in seen:
                errors.append(f"{item['name']}: appears twice ({seen[norm]} and {cat} / {sub})")
            seen[norm] = f"{cat} / {sub}"

    if errors:
        print("Nothing was written. Fix these in the review file and rerun `apply`:")
        for err in errors:
            print(f"  ! {err}")
        sys.exit(1)

    return groups, added, skipped


def ordered_layout(groups) -> list[tuple[str, list[str]]]:
    """Categories and subcategories in taxonomy order, then any approved new subcategories."""
    layout = []
    for category, subs in TAXONOMY.items():
        names = [s for s in subs if (category, s) in groups]
        names += sorted({s for (c, s) in groups if c == category and s not in subs})
        if names:
            layout.append((category, names))
    return layout


def write_landscape(landscape, groups, layout, args, yaml):
    categories = CommentedSeq()
    for category, sub_names in layout:
        subcategories = CommentedSeq()
        for sub_name in sub_names:
            sub = CommentedMap()
            sub["subcategory"] = None
            sub["name"] = sub_name
            sub["items"] = CommentedSeq(
                sorted(groups[(category, sub_name)], key=lambda i: i["name"].lower())
            )
            subcategories.append(sub)
        cat = CommentedMap()
        cat["category"] = None
        cat["name"] = category
        cat["subcategories"] = subcategories
        categories.append(cat)

    landscape["landscape"] = categories
    with args.data.open("w") as fh:
        yaml.dump(landscape, fh)


def write_guide(guide_path: Path, layout, yaml):
    """Mirror the new structure in guide.yml, keeping content for names that still exist."""
    if not guide_path.exists():
        print(f"\n{guide_path} not found; skipped the guide.")
        return

    guide = yaml.load(guide_path.read_text())
    old = {}
    for cat in guide.get("categories") or []:
        old[(cat.get("category"), None)] = cat.get("content")
        for sub in cat.get("subcategories") or []:
            old[(cat.get("category"), sub.get("subcategory"))] = sub.get("content")

    def content(category, subcategory=None):
        text = old.get((category, subcategory))
        return text if text else LiteralScalarString(f"TODO: describe {subcategory or category}.\n")

    categories = CommentedSeq()
    todo = 0
    for category, sub_names in layout:
        cat = CommentedMap()
        cat["category"] = DoubleQuotedScalarString(category)
        cat["content"] = content(category)
        todo += not old.get((category, None))
        subs = CommentedSeq()
        for sub_name in sub_names:
            sub = CommentedMap()
            sub["subcategory"] = DoubleQuotedScalarString(sub_name)
            sub["content"] = content(category, sub_name)
            todo += not old.get((category, sub_name))
            subs.append(sub)
        cat["subcategories"] = subs
        categories.append(cat)

    guide["categories"] = categories
    with guide_path.open("w") as fh:
        yaml.dump(guide, fh)
    print(f"\nUpdated {guide_path}: {todo} sections have TODO placeholder content.")


def cmd_apply(args):
    yaml = helpers.make_yaml()
    entries = load_review(yaml)
    landscape = helpers.load_landscape(args.data)

    groups, added, skipped = build_structure(entries, landscape, args)
    layout = ordered_layout(groups)

    # Download any logos that are missing from the logos directory. This runs
    # before data.yml is written, so a placeholder substitution is saved too.
    all_items = [item for items in groups.values() for item in items]
    if getattr(args, "fetch_logos", True):
        print("Checking logos...")
        downloaded, missing = ensure_logos(all_items, args.logos, getattr(args, "placeholder_logo", None))
    else:
        downloaded = []
        missing = [f"{i['name']}: add {i['logo']}" for i in all_items
                   if not (args.logos / i["logo"]).exists()]

    write_landscape(landscape, groups, layout, args, yaml)
    moved = len(all_items) - len(added)
    print(f"Updated {args.data}: {len(layout)} categories, "
          f"{moved} existing items placed, {len(added)} new items added.")
    for line in added:
        print(f"  + {line}")
    if skipped:
        print("\nApproved but skipped:")
        for s in skipped:
            print(f"  ! {s}")
    if downloaded:
        print(f"\nDownloaded {len(downloaded)} logos to {args.logos}/:")
        for line in downloaded:
            print(f"  + {line}")
    if missing:
        print(f"\nLogos still needed in {args.logos}/ before building:")
        for line in missing:
            print(f"  - {line}")
    if getattr(args, "placeholder_logo", None) and not (args.logos / args.placeholder_logo).exists():
        print(f"\nNote: the placeholder {args.placeholder_logo} itself isn't in {args.logos}/.")

    write_guide(getattr(args, "guide", Path("guide.yml")), layout, yaml)