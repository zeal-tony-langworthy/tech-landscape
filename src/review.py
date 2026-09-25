import sys
import re
import json
import helpers
from collections import defaultdict
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from constants import EXTRACT_DIR, REVIEW_FILE
from taxonomy import TAXONOMY, render_taxonomy, is_valid


# ---------------------------------------------------------------------------
# Step 2: review
#
# Classifies every skill, both the ones already in data.yml and the new ones
# found in the PDFs, into the taxonomy in taxonomy.py. Nothing in data.yml or
# guide.yml changes here; `apply` does that after a human reviews the file.
# ---------------------------------------------------------------------------

CLASSIFY_BATCH_SIZE = 50

CLASSIFY_TOOL = {
    "name": "classify_skills",
    "description": "Record the classification of each input skill.",
    "input_schema": {
        "type": "object",
        "properties": {
            "skills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": "The skill exactly as given."},
                        "skill": {"type": "string", "description": "Normalized, official name."},
                        "category": {"type": "string"},
                        "subcategory": {"type": "string"},
                        "secondary": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Other fits, as 'Category > Subcategory'.",
                        },
                        "new_subcategory": {"type": "boolean"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "homepage_url": {"type": "string"},
                        "skip": {"type": "boolean"},
                        "same_as": {
                            "type": "string",
                            "description": "Exact name of the existing item this is the same "
                                           "technology as; empty if none.",
                        },
                        "note": {"type": "string"},
                    },
                    "required": ["input", "skill", "category", "subcategory",
                                 "new_subcategory", "confidence", "skip"],
                },
            }
        },
        "required": ["skills"],
    },
}

# The system prompt holds only things that are the same for every batch
# (taxonomy, rules, examples), so it's cached across calls. The skills
# themselves go in the user message; see SKILLS_MESSAGE below.
CLASSIFY_SYSTEM = """You are a technology taxonomist. Your job is to classify technology skills into a
category and subcategory from a fixed taxonomy, so that results are consistent
across many batches.

<taxonomy>
{taxonomy}
</taxonomy>

<existing_items>
{existing_items}
</existing_items>

<rules>
1. Assign exactly one category and one subcategory from the taxonomy, based on the
   tool's PRIMARY use. If a tool spans several areas, pick the one a hiring manager
   would most associate it with, and list the others in "secondary".
2. Normalize the skill name to its official spelling (e.g., "VueJS" -> "Vue.js",
   "AirFlow" -> "Apache Airflow", ".Net" -> ".NET", "DynaTrace" -> "Dynatrace").
   Keep the original input in "input", exactly as given.
3. If nothing in the taxonomy fits, set "category" to the closest match, set
   "subcategory" to a proposed new subcategory name, and set "new_subcategory": true.
   Do not invent new top-level categories.
4. If you don't recognize the skill or it's ambiguous (e.g., "Spark" could be Apache
   Spark or something else), give your best guess, set "confidence" to "low", and
   explain in "note".
5. Otherwise set "confidence" to "high" or "medium" and leave "note" empty.
6. Give the official homepage URL of the technology in "homepage_url".
7. Set "skip": true for anything that isn't a specific, named technology, such as
   generic concepts ("REST APIs", "microservices"), methodologies, or soft skills.
   Explain briefly in "note". Still fill in the other fields with your best guess.
8. If the input is the same technology as one of the existing items, even under a
   different name, abbreviation, or vendor prefix (e.g., "Apache Kafka" and "Kafka",
   "K8s" and "Kubernetes", "Postgres" and "PostgreSQL"), set "same_as" to that existing
   item's name exactly as written. Otherwise leave "same_as" empty. An input that is
   itself an existing item is not "same_as" anything.
</rules>

<output_format>
Record your answer with the classify_skills tool: one entry per input skill, in the
same order as the input.
</output_format>

<examples>
Input: Redis
Output: {{"input":"Redis","skill":"Redis","category":"Databases & Data Stores",
"subcategory":"Key-Value & In-Memory","secondary":["Data Engineering > Streaming & Messaging"],
"new_subcategory":false,"confidence":"high","homepage_url":"https://redis.io/",
"skip":false,"note":""}}

Input: Puppet
Output: {{"input":"Puppet","skill":"Puppet","category":"DevOps & CI/CD",
"subcategory":"Configuration Management","secondary":[],"new_subcategory":false,
"confidence":"high","homepage_url":"https://puppet.com/","skip":false,"note":""}}

Input: Snyk
Output: {{"input":"Snyk","skill":"Snyk","category":"Security",
"subcategory":"Application Security & SCA","secondary":["DevOps & CI/CD > CI/CD Pipelines"],
"new_subcategory":false,"confidence":"high","homepage_url":"https://snyk.io/",
"skip":false,"note":""}}
</examples>"""

SKILLS_MESSAGE = """Classify these skills:
<skills>
{skills_list}
</skills>"""


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("#", "sharp").replace("+", "plus"))
    return slug.strip("-")


def skill_names(data: dict, path) -> list[str]:
    """Skill names from one extraction file, tolerating the shapes Claude sometimes returns:
    a list of {"name": ...} objects (expected), a list of plain strings, or the whole
    list encoded as a JSON string."""
    skills = data.get("skills", [])
    if isinstance(skills, str):
        try:
            skills = json.loads(skills)
        except json.JSONDecodeError:
            print(f"  ! {path.name}: 'skills' isn't valid JSON; skipped")
            return []
    names = []
    for skill in skills if isinstance(skills, list) else []:
        if isinstance(skill, dict) and skill.get("name"):
            names.append(skill["name"])
        elif isinstance(skill, str):
            names.append(skill)
    return names


def count_pdf_skills() -> tuple[dict[str, set[str]], dict[str, str]]:
    """Which documents mention each skill, by normalized name. Sets rather than counts,
    so merging two spellings of one skill doesn't count a document twice."""
    files = sorted(EXTRACT_DIR.glob("*.json"))
    if not files:
        sys.exit("No extractions found. Run `extract` first.")

    docs: dict[str, set[str]] = defaultdict(set)
    display: dict[str, str] = {}
    for path in files:
        for name in skill_names(json.loads(path.read_text()), path):
            key = helpers.normalize(name)
            if key:
                docs[key].add(path.name)
                display.setdefault(key, name)
    print(f"{len(files)} documents, {len(docs)} distinct skill names.")
    return docs, display


def classify(names: list[str], existing_names: list[str]) -> dict[str, dict]:
    """Classify names in batches. Returns results keyed by normalized input name."""
    client = helpers.claude_client()
    system = CLASSIFY_SYSTEM.format(taxonomy=render_taxonomy(),
                                    existing_items="\n".join(sorted(existing_names)))
    results: dict[str, dict] = {}
    for i in range(0, len(names), CLASSIFY_BATCH_SIZE):
        batch = names[i:i + CLASSIFY_BATCH_SIZE]
        print(f"Classifying {i + 1}-{i + len(batch)} of {len(names)}...")
        out = helpers.call_tool(
            client, system,
            [{"type": "text", "text": SKILLS_MESSAGE.format(skills_list="\n".join(batch))}],
            CLASSIFY_TOOL,
        )
        for r in out.get("skills", []):
            results[helpers.normalize(r["input"])] = r
    return results


def check(r: dict, is_new: bool) -> tuple[bool, list[str]]:
    """Decide the default approve value and collect notes for the reviewer."""
    notes = [r["note"]] if r.get("note") else []
    if not r:
        return False, ["Claude returned no classification; place manually."]
    if r.get("skip"):
        return False, notes or ["Claude suggests skipping this."]

    approve = True
    category, subcategory = r.get("category"), r.get("subcategory")
    if category not in TAXONOMY:
        notes.append(f"Category '{category}' isn't in the taxonomy.")
        approve = False
    elif r.get("new_subcategory") or not is_valid(category, subcategory):
        notes.append(f"Proposed new subcategory '{subcategory}'.")
        approve = False
    if r.get("confidence") == "low":
        if not notes:
            notes.append("Low confidence.")
        approve = False
    if is_new and not r.get("homepage_url"):
        notes.append("No homepage_url.")
        approve = False
    return approve, notes


def sort_key(entry) -> tuple:
    """Order entries the way they'll appear on the page; unplaceable ones last."""
    cats = list(TAXONOMY)
    cat, sub = entry["category"], entry["subcategory"]
    cat_i = cats.index(cat) if cat in TAXONOMY else len(cats)
    subs = list(TAXONOMY.get(cat, {}))
    sub_i = subs.index(sub) if sub in subs else len(subs)
    return (cat_i, sub_i, str(sub or ""), entry["name"].lower())


def cmd_review(args):
    landscape = helpers.load_landscape(args.data)
    existing = helpers.existing_items(landscape)  # normalized -> name in data.yml
    docs, display = count_pdf_skills()

    # Existing items already placed in a taxonomy category keep their placement
    # and aren't sent to Claude, so reviewed decisions stay put across runs.
    placement = {
        helpers.normalize(item["name"]): (cat["name"], sub["name"])
        for cat in landscape["landscape"]
        for sub in cat.get("subcategories") or []
        for item in sub.get("items") or []
    }
    reclassify = getattr(args, "reclassify", False)
    kept = {k for k, (cat, _) in placement.items() if cat in TAXONOMY and not reclassify}
    to_classify = [name for k, name in existing.items() if k not in kept]

    # All new names are classified, and --min-docs is applied after merging, so
    # "K8s" in one PDF and "Kubernetes" in another count as two documents.
    new_keys = sorted((k for k in docs if k not in existing),
                      key=lambda k: (-len(docs[k]), display[k].lower()))
    print(f"{len(existing)} skills already in data.yml ({len(kept)} already placed, "
          f"{len(to_classify)} to classify), {len(new_keys)} new names from the PDFs.")

    names = to_classify + [display[k] for k in new_keys]
    results = classify(names, list(existing.values())) if names else {}

    # --- Merge: every new name resolves to one skill, existing or new ---------
    extra_docs: dict[str, set[str]] = defaultdict(set)   # existing key -> merged docs
    variants: dict[str, list[str]] = defaultdict(list)   # target key -> other spellings
    new_groups: dict[str, dict] = {}                      # canonical key -> merged new skill

    for key in new_keys:
        r = results.get(key, {})
        same_as = helpers.normalize(r.get("same_as") or "")
        skill_key = helpers.normalize(r.get("skill") or "")
        target = same_as if same_as in existing else skill_key if skill_key in existing else None

        if target:
            extra_docs[target] |= docs[key]
            variants[target].append(display[key])
            continue

        canonical = skill_key or key
        group = new_groups.get(canonical)
        if group is None:
            new_groups[canonical] = {"r": r, "key": key, "docs": set(docs[key])}
        else:
            group["docs"] |= docs[key]
        variants[canonical].append(display[key])

    merged = len(new_keys) - len(new_groups)
    if merged:
        print(f"Merged {merged} alternate spellings into other entries.")

    def seen_as(key: str, final_name: str) -> str | None:
        """Note listing the other spellings merged into this entry, if any."""
        names = sorted({v for v in variants.get(key, [])
                        if helpers.normalize(v) != helpers.normalize(final_name)})
        return f"Also in PDFs as: {', '.join(names)}." if names else None

    entries = []

    for key, current_name in existing.items():
        documents = len(docs.get(key, set()) | extra_docs.get(key, set()))
        if key in kept:
            category, subcategory = placement[key]
            notes = ["Current placement kept."]
            entry = CommentedMap()
            entry["status"] = "existing"
            entry["approve"] = True
            entry["name"] = current_name
            entry["current_name"] = current_name
            entry["category"] = category
            entry["subcategory"] = subcategory
            entry["new_subcategory"] = not is_valid(category, subcategory)
        else:
            r = results.get(key, {})
            approve, notes = check(r, is_new=False)
            name = r.get("skill") or current_name
            if name != current_name:
                notes.append(f"Will be renamed from '{current_name}'.")
            dup = helpers.normalize(r.get("same_as") or "")
            if dup in existing and dup != key:
                notes.append(f"Looks like a duplicate of existing item '{existing[dup]}'; "
                             "remove one of them from data.yml.")
            entry = CommentedMap()
            entry["status"] = "existing"
            entry["approve"] = approve
            entry["name"] = name
            entry["current_name"] = current_name
            entry["category"] = r.get("category")
            entry["subcategory"] = r.get("subcategory")
            entry["new_subcategory"] = bool(r.get("new_subcategory"))
            entry["confidence"] = r.get("confidence")
            entry["secondary"] = CommentedSeq(r.get("secondary") or [])
        entry["documents"] = documents
        if seen_as(key, entry["name"]):
            notes.append(seen_as(key, entry["name"]))
        if notes:
            entry["note"] = " ".join(notes)
        entries.append(entry)

    for canonical, group in new_groups.items():
        if len(group["docs"]) < args.min_docs:
            continue
        r, key = group["r"], group["key"]
        approve, notes = check(r, is_new=True)
        name = r.get("skill") or display[key]
        if seen_as(canonical, name):
            notes.append(seen_as(canonical, name))

        logo = f"{slugify(name)}.svg"
        entry = CommentedMap()
        entry["status"] = "new"
        entry["approve"] = approve
        entry["name"] = name
        entry["category"] = r.get("category")
        entry["subcategory"] = r.get("subcategory")
        entry["new_subcategory"] = bool(r.get("new_subcategory"))
        entry["confidence"] = r.get("confidence")
        entry["secondary"] = CommentedSeq(r.get("secondary") or [])
        entry["homepage_url"] = r.get("homepage_url")
        entry["logo"] = logo
        entry["logo_exists"] = (args.logos / logo).exists()
        entry["documents"] = len(group["docs"])
        if notes:
            entry["note"] = " ".join(notes)
        entries.append(entry)

    entries.sort(key=sort_key)

    yaml = helpers.make_yaml()
    with REVIEW_FILE.open("w") as fh:
        fh.write(
            "# Review before running `apply`.\n"
            "#\n"
            "# status: existing = already in data.yml; it will be moved into this category.\n"
            "#   Every existing entry must be approve: true (fix the category/subcategory\n"
            "#   if needed), or `apply` will stop, so nothing drops off the page.\n"
            "#   Its URL, logo, and other fields are kept from data.yml.\n"
            "# status: new = found in the PDFs; approve: false leaves it out.\n"
            "#\n"
            "# Each skill appears once; other spellings found in the PDFs are merged into\n"
            "# it and listed in the note.\n"
            "#\n"
            "# approve defaults to false for low confidence, proposed new subcategories,\n"
            "# and skip suggestions. Edit any field as needed.\n"
            "# documents = how many PDFs mention the skill.\n"
        )
        yaml.dump({"skills": entries}, fh)

    for status in ("existing", "new"):
        group = [e for e in entries if e["status"] == status]
        flagged = sum(not e["approve"] for e in group)
        print(f"{status}: {len(group)} entries, {flagged} flagged for review")
    print(f"\nWrote {REVIEW_FILE}.")