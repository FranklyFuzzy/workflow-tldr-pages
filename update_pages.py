#!/usr/bin/env python3
"""Refresh the TLDR Pages Alfred workflow's local Markdown cache."""

import io
import shutil
import sys
import tempfile
import time
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen

# Official English archive recommended by the TLDR Pages client specification.
ARCHIVE_URL = (
    "https://github.com/tldr-pages/tldr/"
    "releases/latest/download/tldr-pages.zip"
)
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_PAGE_BYTES = 150 * 1024 * 1024
MAX_SINGLE_PAGE_BYTES = 2 * 1024 * 1024
MIN_PAGE_COUNT = 100


def remove_path(path: Path) -> None:
    """Remove a directory, file, or symlink safely."""
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "Usage: update_pages.py PAGES_DIR SUCCESS_MARKER LOCK_PATH",
            file=sys.stderr,
        )
        return 2

    pages_dir = Path(sys.argv[1]).resolve()
    success_marker = Path(sys.argv[2]).resolve()
    lock_path = Path(sys.argv[3]).resolve()
    workflow_dir = pages_dir.parent
    backup_dir = workflow_dir / ".pages-backup"
    stage_root = None
    error_log = workflow_dir / ".tldr-pages-update-error.log"

    try:
        # Recover the previous cache if an earlier run was interrupted mid-swap.
        if not pages_dir.exists() and backup_dir.exists():
            backup_dir.replace(pages_dir)

        request = Request(
            ARCHIVE_URL,
            headers={"User-Agent": "Alfred-TLDR-Pages-Updater/1.0"},
        )
        with urlopen(request, timeout=45) as response:
            archive_data = response.read(MAX_ARCHIVE_BYTES + 1)

        if len(archive_data) > MAX_ARCHIVE_BYTES:
            raise RuntimeError("The downloaded ZIP exceeds the 100 MiB safety limit.")

        stage_root = Path(
            tempfile.mkdtemp(prefix=".tldr-pages-stage-", dir=str(workflow_dir))
        )
        new_pages = stage_root / "pages"
        new_pages.mkdir()

        with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
            safe_markdown = []
            common_counts = Counter()

            for member in archive.infolist():
                if member.is_dir():
                    continue
                path = PurePosixPath(member.filename)
                parts = path.parts
                if (
                    path.is_absolute()
                    or ".." in parts
                    or path.suffix.lower() != ".md"
                    or len(parts) < 2
                ):
                    continue
                if member.file_size > MAX_SINGLE_PAGE_BYTES:
                    raise RuntimeError(
                        f"Refusing unusually large Markdown file: {member.filename}"
                    )
                safe_markdown.append((member, parts))
                if parts[-2] == "common":
                    common_counts[parts[:-2]] += 1

            if not common_counts:
                raise RuntimeError(
                    "Could not find the expected common/*.md pages in the archive."
                )

            # The official English archive may put platform folders at the ZIP root,
            # inside pages/, or inside a pages.en/ directory. Anchor on common/ to
            # discover the correct root without assuming a single archive layout.
            prefix, common_count = common_counts.most_common(1)[0]
            if common_count < MIN_PAGE_COUNT:
                raise RuntimeError(
                    f"Only found {common_count} common pages; refusing to update."
                )

            candidates = []
            total_bytes = 0
            for member, parts in safe_markdown:
                if parts[: len(prefix)] != prefix:
                    continue
                if len(parts) != len(prefix) + 2:
                    continue
                relative_parts = parts[len(prefix) :]
                if len(relative_parts) != 2:
                    continue
                candidates.append((member, relative_parts))
                total_bytes += member.file_size

            if len(candidates) < MIN_PAGE_COUNT:
                raise RuntimeError(
                    f"Only found {len(candidates)} pages; refusing to replace cache."
                )
            if total_bytes > MAX_TOTAL_PAGE_BYTES:
                raise RuntimeError("The extracted pages exceed the 150 MiB safety limit.")

            for member, relative_parts in candidates:
                platform, filename = relative_parts
                # Ensure platform/file are ordinary relative components.
                if platform in ("", ".", "..") or filename in ("", ".", ".."):
                    continue
                destination = new_pages / platform / filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(member))

        # Validate the staged cache before touching the current one.
        installed_count = sum(1 for _ in new_pages.glob("*/*.md"))
        if installed_count < MIN_PAGE_COUNT or not list((new_pages / "common").glob("*.md")):
            raise RuntimeError(
                f"Staged cache validation failed ({installed_count} pages found)."
            )

        # Replace the cache only after download, extraction, and validation succeeded.
        remove_path(backup_dir)
        if pages_dir.exists() or pages_dir.is_symlink():
            pages_dir.replace(backup_dir)

        try:
            new_pages.replace(pages_dir)
        except Exception:
            if backup_dir.exists() and not pages_dir.exists():
                backup_dir.replace(pages_dir)
            raise

        remove_path(backup_dir)
        success_marker.write_text(str(time.time()), encoding="utf-8")
        error_log.unlink(missing_ok=True)
        return 0

    except Exception as exc:
        error_log.write_text(
            f"{time.ctime()}: {type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
        return 1

    finally:
        if stage_root is not None:
            remove_path(stage_root)
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
