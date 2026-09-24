import io
import os
import sys
import json
import base64
from pathlib import Path
from constants import EXTRACT_DIR
import helpers

# ---------------------------------------------------------------------------
# Step 1: extract
# ---------------------------------------------------------------------------

def cmd_extract(args):
    folder_id = os.environ.get("DRIVE_FOLDER_ID")
    if not folder_id:
        sys.exit("DRIVE_FOLDER_ID is not set.")

    landscape = helpers.load_landscape(args.data)
    system = helpers.EXTRACT_SYSTEM.format(items="\n".join(sorted(helpers.existing_items(landscape).values())))

    service = helpers.drive_service()
    client = helpers.claude_client()
    EXTRACT_DIR.mkdir(exist_ok=True)

    pdfs = helpers.list_pdfs(service, folder_id)
    print(f"Found {len(pdfs)} PDFs in the Drive folder.")

    processed = skipped = failed = 0
    for n, f in enumerate(pdfs, 1):
        cache = EXTRACT_DIR / f"{f['id']}.json"
        if cache.exists() and not args.force:
            cached = json.loads(cache.read_text())
            if cached.get("md5") == f.get("md5Checksum"):
                skipped += 1
                continue

        print(f"[{n}/{len(pdfs)}] {f['name']}")
        try:
            pdf = helpers.download_pdf(service, f["id"])
            result = helpers.call_tool(
                client,
                system,
                [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.standard_b64encode(pdf).decode(),
                        },
                    },
                    {"type": "text", "text": "Record the technical skills in this document."},
                ],
                helpers.EXTRACT_TOOL,
            )
        except Exception as e:  # keep going; failed files are retried on the next run
            print(f"    FAILED: {e}")
            failed += 1
            continue

        cache.write_text(json.dumps(
            {"file_id": f["id"], "file_name": f["name"], "md5": f.get("md5Checksum"),
             "skills": result.get("skills", [])},
            indent=2,
        ))
        print(f"    {len(result.get('skills', []))} skills")
        processed += 1

    print(f"\nDone. Processed {processed}, unchanged {skipped}, failed {failed}.")
    if failed:
        print("Rerun `extract` to retry the failed files.")

