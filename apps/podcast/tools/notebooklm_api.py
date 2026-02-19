#!/usr/bin/env python3
"""
NotebookLM Enterprise API - Audio Overview Generation

NOTE: This NotebookLM Enterprise API integration exists but is NOT currently in use.
The team decided against using this approach for now. [Date: 2026-02-19]

Alternative: The podcast production pipeline uses local audio generation via
notebooklm-mcp-cli instead. See apps/podcast/tasks.py::step_audio_generation
and the local_audio_worker management command for the current approach.

===== ARCHIVED IMPLEMENTATION BELOW =====

Uses the Discovery Engine API to:
1. Create a notebook
2. Upload source files (p1-brief.md, report.md, p3-briefing.md, sources.md)
3. Generate audio overview with custom episodeFocus for single-speaker long-form

Prerequisites:
- Google Cloud project with Discovery Engine API enabled
- gcloud CLI authenticated
- Environment variables:
  - GOOGLE_CLOUD_PROJECT: Your GCP project ID or number
  - NOTEBOOKLM_LOCATION: Region (default: global)

Usage:
    python notebooklm_api.py /path/to/episode/directory
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Configuration
ENDPOINT_PREFIX = "global"  # us, eu, or global
LOCATION = "global"  # Must match ENDPOINT_PREFIX
API_VERSION = "v1alpha"


def get_access_token() -> str:
    """Get OAuth access token via gcloud CLI."""
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def get_project_number() -> str:
    """Get project number from environment or gcloud."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        result = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True,
            text=True,
            check=True,
        )
        project = result.stdout.strip()

    # If it's a project ID, we need to convert to project number
    # For now, assume it's already a number or the API accepts IDs
    return project


def get_base_url() -> str:
    """Construct the Discovery Engine API base URL."""
    project = get_project_number()
    return (
        f"https://{ENDPOINT_PREFIX}-discoveryengine.googleapis.com"
        f"/{API_VERSION}/projects/{project}/locations/{LOCATION}"
    )


def api_request(
    method: str,
    endpoint: str,
    data: dict | None = None,
    headers: dict | None = None,
) -> dict:
    """Make an authenticated API request."""
    import urllib.error
    import urllib.request

    token = get_access_token()
    url = f"{get_base_url()}{endpoint}"

    req_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if headers:
        req_headers.update(headers)

    body = json.dumps(data).encode() if data else None

    request = urllib.request.Request(url, data=body, headers=req_headers, method=method)

    try:
        with urllib.request.urlopen(request) as response:  # nosec B310
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()
        print(f"API Error {e.code}: {error_body}")
        raise


def create_notebook(title: str) -> dict:
    """Create a new notebook."""
    print(f"Creating notebook: {title}")
    return api_request("POST", "/notebooks", {"title": title})


def upload_source_text(notebook_id: str, name: str, content: str) -> dict:
    """Upload text content as a source."""
    print(f"  Uploading source: {name}")
    endpoint = f"/notebooks/{notebook_id}/sources:batchCreate"
    data = {
        "requests": [
            {"source": {"textContent": {"sourceName": name, "content": content}}}
        ]
    }
    return api_request("POST", endpoint, data)


def upload_source_file(notebook_id: str, file_path: Path) -> dict:
    """Upload a file as a source using the uploadFile endpoint."""
    print(f"  Uploading file: {file_path.name}")

    token = get_access_token()
    project = get_project_number()

    # Use the upload endpoint
    url = (
        f"https://{ENDPOINT_PREFIX}-discoveryengine.googleapis.com"
        f"/upload/{API_VERSION}/projects/{project}/locations/{LOCATION}"
        f"/notebooks/{notebook_id}/sources:uploadFile"
    )

    # Determine MIME type
    suffix = file_path.suffix.lower()
    mime_types = {
        ".md": "text/markdown",
        ".txt": "text/plain",
        ".pdf": "application/pdf",
    }
    mime_type = mime_types.get(suffix, "text/plain")

    # Use curl for file upload (simpler for multipart)
    result = subprocess.run(
        [
            "curl",
            "-s",
            "-X",
            "POST",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            f"Content-Type: {mime_type}",
            "-H",
            f"X-Goog-Upload-File-Name: {file_path.name}",
            "-H",
            "X-Goog-Upload-Protocol: raw",
            "--data-binary",
            f"@{file_path}",
            url,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise Exception(f"Upload failed: {result.stderr}")

    return json.loads(result.stdout) if result.stdout else {}


def create_audio_overview(
    notebook_id: str, episode_focus: str, language_code: str = "en"
) -> dict:
    """Create an audio overview for the notebook."""
    print("Creating audio overview...")
    print(f"  Focus: {episode_focus[:100]}...")

    endpoint = f"/notebooks/{notebook_id}/audioOverviews"
    data = {"episodeFocus": episode_focus, "languageCode": language_code}
    return api_request("POST", endpoint, data)


def get_audio_overview_status(notebook_id: str) -> dict:
    """Check the status of an audio overview."""
    endpoint = f"/notebooks/{notebook_id}/audioOverviews/default"
    return api_request("GET", endpoint)


def wait_for_audio(notebook_id: str, timeout_minutes: int = 30) -> dict:
    """Poll until audio generation completes."""
    print("Waiting for audio generation...")
    start = time.time()
    timeout_seconds = timeout_minutes * 60

    while time.time() - start < timeout_seconds:
        status = get_audio_overview_status(notebook_id)
        state = status.get("audioOverview", {}).get("status", "UNKNOWN")

        print(f"  Status: {state}")

        if state == "AUDIO_OVERVIEW_STATUS_COMPLETED":
            return status
        elif state == "AUDIO_OVERVIEW_STATUS_FAILED":
            raise Exception(f"Audio generation failed: {status}")

        time.sleep(30)  # Poll every 30 seconds

    raise Exception(f"Timeout after {timeout_minutes} minutes")


def download_audio(notebook_id: str, output_path: Path) -> None:
    """Download the generated audio file."""
    print(f"Downloading audio to: {output_path}")

    token = get_access_token()
    project = get_project_number()

    # Get the audio overview to find download URL
    status = get_audio_overview_status(notebook_id)
    audio_uri = status.get("audioOverview", {}).get("audioUri")

    if not audio_uri:
        # Try direct download endpoint
        url = (
            f"https://{ENDPOINT_PREFIX}-discoveryengine.googleapis.com"
            f"/{API_VERSION}/projects/{project}/locations/{LOCATION}"
            f"/notebooks/{notebook_id}/audioOverviews/default:download"
        )
    else:
        url = audio_uri

    # Download using curl
    result = subprocess.run(
        [
            "curl",
            "-s",
            "-L",
            "-H",
            f"Authorization: Bearer {token}",
            "-o",
            str(output_path),
            url,
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise Exception(f"Download failed: {result.stderr}")

    print(f"  Downloaded: {output_path.stat().st_size / 1024 / 1024:.1f} MB")


def delete_notebook(notebook_id: str) -> None:
    """Delete a notebook (cleanup)."""
    print(f"Deleting notebook: {notebook_id}")
    endpoint = f"/notebooks/{notebook_id}"
    api_request("DELETE", endpoint)


def test_api_access() -> tuple[bool, str]:
    """
    Quick test of API access before starting heavy operations.
    Returns (success, error_message).
    """
    import urllib.error
    import urllib.request

    try:
        token = get_access_token()
        url = f"{get_base_url()}/notebooks"

        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="GET",
        )

        # Quick timeout - if API isn't accessible, fail fast
        with urllib.request.urlopen(request, timeout=10):  # nosec B310
            # 200 OK means we have access
            return True, ""

    except urllib.error.HTTPError as e:
        if e.code == 403:
            return (
                False,
                "API access denied (403). NotebookLM Enterprise subscription required.",
            )
        elif e.code == 401:
            return False, "Authentication failed (401). Run: gcloud auth login"
        elif e.code == 404:
            return (
                False,
                "API endpoint not found (404). Discovery Engine API may not be enabled.",
            )
        else:
            error_body = e.read().decode()
            return False, f"API error {e.code}: {error_body}"
    except urllib.error.URLError as e:
        return False, f"Network error: {e.reason}"
    except subprocess.CalledProcessError as e:
        return False, f"gcloud auth failed: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"


def print_manual_fallback(episode_dir: Path) -> None:
    """Print manual NotebookLM instructions when API is unavailable."""
    print(f"""
{'='*60}
API UNAVAILABLE - USE MANUAL WORKFLOW
{'='*60}

NotebookLM Enterprise API requires a paid subscription.

Generate the manual prompt with:

  cd {Path(__file__).parent}
  python notebooklm_prompt.py {episode_dir} --copy

This will:
- Verify all 5 source files are ready
- Generate the NotebookLM prompt with correct title/series
- Copy to clipboard (--copy flag)

Then paste into NotebookLM at: https://notebooklm.google.com/

{'='*60}
""")


def generate_episode_focus(episode_title: str, series_name: str = "") -> str:
    """Generate the episodeFocus prompt for NotebookLM audio generation."""
    series_intro = f" from our {series_name} series" if series_name else ""

    return f"""Create a two-host podcast episode on: {episode_title}{series_intro}

IMPORTANT: Follow the structure and guidance in content_plan.md carefully. It contains:
- The opening hook to use
- Key terms to define (with pronunciations)
- Studies to emphasize
- Three-section narrative arc (Foundation → Evidence → Application)
- Episode Structure Map with modes, durations, and transitions
- Signposting language for transitions between sections
- Closing callback and sign-off

STRUCTURAL GUIDANCE:
- Follow the Episode Structure Map in content_plan.md for section order, modes, and timing
- Use the signposting language provided for transitions: preview the structure at the start ("In this episode, we'll first explore X, then Y, and finally Z"), signal mode switches clearly, and use progress markers
- Follow the Depth Budget - allocate time to themes as specified, don't let any major theme get compressed at the end
- Build toward the resolution identified in content_plan.md - each section should raise the stakes or deepen understanding

DIALOGUE DYNAMICS - CRITICAL:
- Find the "Counterpoint Moments" section in content_plan.md
- At each counterpoint moment, speakers must TAKE POSITIONS and DISAGREE respectfully
- Speaker A should argue their assigned position; Speaker B should actively push back with their assigned counter-position
- This must be genuine disagreement, NOT collaborative exploration where both hosts present views neutrally
- Use phrases like: "Wait, but doesn't that contradict...", "I disagree because...", "I see it differently - here's why...", "Let me push back on that..."
- After debating, resolve through synthesis: "Both perspectives have merit when you consider..."
- Target 2-3 counterpoint moments throughout the episode

Brand elements:
- Host: Valor Engels
- Open with: "Welcome to Yuda Me Research{series_intro}. I'm Valor Engels..."
- Close with: "Find full research and sources at research dot yuda dot me - that's Y-U-D-A dot M-E"

EPISODE ARC:
- Opening (3-5 min): Hook with specific stat/story, define the problem, preview structure
- Middle (20-30 min): Build from foundation to evidence to application with clear mode-switching
- Closing (3-5 min): Synthesize key takeaways (state 1-3 explicitly), callback to opening hook, call-to-action

Tone: Intellectually rigorous but accessible - two experts having a genuine conversation, making complex research understandable.

Style guidelines:
- Spell out acronyms on first use: "High-Intensity Interval Training, or HIIT"
- Define technical terms before building on them
- Use specific numbers with context (sample sizes, effect sizes, percentages)
- Distinguish correlation from causation
- Make statistics meaningful through comparisons
- Include human elements when the research contains them

Avoid:
- Undefined jargon
- Fabricated examples (use only what's in the source material)
- Over-hedging that obscures findings
- Repeating context unnecessarily
- Collaborative framing of counterpoints (hosts must actually disagree)"""


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate podcast audio using NotebookLM Enterprise API"
    )
    parser.add_argument("episode_dir", type=Path, help="Path to episode directory")
    parser.add_argument(
        "--series",
        type=str,
        default="",
        help="Series name (e.g., 'Cardiovascular Health')",
    )
    parser.add_argument(
        "--title",
        type=str,
        default="",
        help="Episode title (defaults to directory name)",
    )
    parser.add_argument(
        "--cleanup", action="store_true", help="Delete notebook after generation"
    )
    parser.add_argument(
        "--timeout", type=int, default=30, help="Timeout in minutes (default: 30)"
    )

    args = parser.parse_args()

    episode_dir = args.episode_dir
    if not episode_dir.exists():
        print(f"Error: Directory not found: {episode_dir}")
        sys.exit(1)

    # Define source files to upload (5 files for NotebookLM)
    source_files = [
        episode_dir / "research" / "p1-brief.md",
        episode_dir / "report.md",
        episode_dir / "research" / "p3-briefing.md",
        episode_dir / "sources.md",
        episode_dir / "content_plan.md",
    ]

    # Check all files exist
    missing = [f for f in source_files if not f.exists()]
    if missing:
        print("Error: Missing source files:")
        for f in missing:
            print(f"  - {f}")
        sys.exit(1)

    # Extract episode info
    episode_slug = episode_dir.name
    episode_title = args.title or episode_slug.replace("-", " ").title()

    print(f"\n{'='*60}")
    print("NotebookLM Enterprise API - Audio Generation")
    print(f"{'='*60}")
    print(f"Episode: {episode_title}")
    if args.series:
        print(f"Series: {args.series}")
    print(f"Directory: {episode_dir}")
    print(f"Sources: {len(source_files)} files")
    print(f"{'='*60}\n")

    # Quick API access test before starting heavy operations
    print("Testing API access...")
    api_ok, api_error = test_api_access()
    if not api_ok:
        print(f"\n❌ API Access Failed: {api_error}")
        print_manual_fallback(episode_dir)
        sys.exit(2)  # Exit code 2 = API unavailable, use manual fallback
    print("✓ API access confirmed\n")

    notebook_id = None
    try:
        # 1. Create notebook
        notebook = create_notebook(f"Yudame Research: {episode_title}")
        notebook_id = notebook.get("notebookId")
        print(f"  Notebook ID: {notebook_id}")

        # 2. Upload source files
        print("\nUploading sources...")
        for file_path in source_files:
            if file_path.exists():
                # Try text upload first (simpler)
                content = file_path.read_text()
                upload_source_text(notebook_id, file_path.name, content)

        # 3. Generate audio overview
        print("\nGenerating audio overview...")
        episode_focus = generate_episode_focus(episode_title, args.series)
        audio_response = create_audio_overview(notebook_id, episode_focus)
        print(f"  Response: {json.dumps(audio_response, indent=2)}")

        # 4. Wait for completion
        result = wait_for_audio(notebook_id, timeout_minutes=args.timeout)
        print(f"  Audio ready: {json.dumps(result, indent=2)}")

        # 5. Download audio
        output_path = episode_dir / f"{episode_slug}.mp3"
        download_audio(notebook_id, output_path)

        # 6. Get file info
        file_size = output_path.stat().st_size
        print(f"\n{'='*60}")
        print("✅ Audio generated successfully!")
        print(f"   File: {output_path}")
        print(f"   Size: {file_size / 1024 / 1024:.1f} MB ({file_size:,} bytes)")
        print(f"{'='*60}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        raise
    finally:
        # Cleanup: optionally delete notebook
        if notebook_id and args.cleanup:
            try:
                delete_notebook(notebook_id)
                print("  Notebook cleaned up.")
            except Exception as e:
                print(f"Warning: Cleanup failed: {e}")


if __name__ == "__main__":
    main()
