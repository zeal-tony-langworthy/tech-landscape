import sys
import os
import re
import io
from pathlib import Path
from ruamel.yaml import YAML

# ---------------------------------------------------------------------------
# Claude helpers
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "claude-sonnet-5"

def claude_client():
    import anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set.")
    return anthropic.Anthropic(max_retries=5)


def call_tool(client, system: str, content: list, tool: dict) -> dict:
    """Call Claude, force it to use `tool`, and return the tool input as a dict."""
    response = client.messages.create(
        model=os.environ.get("CLAUDE_MODEL", DEFAULT_MODEL),
        max_tokens=8000,
        # Cache the system prompt: it's identical for every PDF.
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": content}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError(f"No tool call in response (stop_reason={response.stop_reason})")


EXTRACT_TOOL = {
    "name": "record_skills",
    "description": "Record the technical skills found in the document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "skills": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Exact existing landscape name if it matches one, "
                                           "otherwise the official product name.",
                        },
                        "evidence": {
                            "type": "string",
                            "description": "A few words from the document showing the skill.",
                        },
                    },
                    "required": ["name", "evidence"],
                },
            }
        },
        "required": ["skills"],
    },
}

EXTRACT_SYSTEM = """You extract technical developer skills from documents such as resumes and skill profiles.

Include: programming languages, frameworks, libraries, databases, cloud platforms and services, \
CI/CD and infrastructure tools, observability tools, data tools, and other named technologies.
Exclude: soft skills, methodologies (e.g. Agile, Scrum), job titles, employers, certifications, \
and spoken languages.

Only record skills the document actually mentions. Do not infer skills that aren't named.

Naming rules:
- If a skill matches one of the existing landscape items below (including variants such as \
"SpringBoot" for "Spring Boot" or "Postgres" for "PostgreSQL"), use the existing name exactly.
- Otherwise use the official product name with its standard capitalization.
- List each skill once.

Existing landscape items:
{items}"""

# ---------------------------------------------------------------------------
# YAML helpers (round-trip mode keeps formatting, key order, and comments)
# ---------------------------------------------------------------------------

def make_yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)  # matches the existing data.yml
    yaml.width = 4096  # never wrap long URLs
    return yaml


def load_landscape(path: Path):
    return make_yaml().load(path.read_text())


def normalize(name: str) -> str:
    """Loose key for matching name variants: 'Next.js' == 'nextjs' == 'Next JS'."""
    return re.sub(r"[\s.\-_]", "", name).lower()

def existing_items(landscape) -> dict[str, str]:
    """Map normalized name -> exact name for every item already in data.yml."""
    names = {}
    for cat in landscape["landscape"]:
        for sub in cat.get("subcategories") or []:
            for item in sub.get("items") or []:
                names[normalize(item["name"])] = item["name"]
    return names

# ---------------------------------------------------------------------------
# Google Drive Helpers
# ---------------------------------------------------------------------------
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
TOKEN_FILE = Path("token.json")

def drive_service():
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=drive_credentials(), cache_discovery=False)

def cmd_check_drive(args):
    """Confirm Drive access works and show what extract would process."""
    from googleapiclient.errors import HttpError

    folder_id = os.environ.get("DRIVE_FOLDER_ID")
    if not folder_id:
        sys.exit("DRIVE_FOLDER_ID is not set.")
    service = drive_service()
    try:
        folder = service.files().get(fileId=folder_id, fields="name",
                                     supportsAllDrives=True).execute()
        pdfs = list_pdfs(service, folder_id)
    except HttpError as e:
        sys.exit(f"Drive request failed ({e.status_code}): {e.reason}\n"
                 "404 usually means this identity can't see the folder; "
                 "403 usually means the Drive API or this app is blocked.")
    print(f"Folder: {folder['name']}")
    print(f"{len(pdfs)} PDFs found")
    for f in pdfs[:10]:
        print(f"  - {f['name']}")
    if len(pdfs) > 10:
        print(f"  ... and {len(pdfs) - 10} more")


def drive_credentials():
    """Service account if GOOGLE_SERVICE_ACCOUNT_FILE is set, otherwise your own
    Google account via OAuth (browser sign-in once, then a cached token)."""
    key_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if key_file:
        from google.oauth2 import service_account

        return service_account.Credentials.from_service_account_file(key_file, scopes=DRIVE_SCOPES)

    client_file = os.environ.get("GOOGLE_OAUTH_CLIENT_FILE")
    if not client_file:
        sys.exit("Set GOOGLE_OAUTH_CLIENT_FILE (sign in as yourself) or "
                 "GOOGLE_SERVICE_ACCOUNT_FILE (service account).")

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), DRIVE_SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None  # refresh token revoked or expired; sign in again
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(client_file, DRIVE_SCOPES)
        creds = flow.run_local_server(port=0)  # opens a browser for sign-in
    TOKEN_FILE.write_text(creds.to_json())
    TOKEN_FILE.chmod(0o600)
    return creds


def list_pdfs(service, folder_id: str) -> list[dict]:
    files, token = [], None
    query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    while True:
        resp = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, md5Checksum)",
            pageSize=100,
            pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        files.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            return files

def download_pdf(service, file_id: str) -> bytes:
    from googleapiclient.http import MediaIoBaseDownload

    buffer = io.BytesIO()
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()

