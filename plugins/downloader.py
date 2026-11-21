"""Download orchestration plugin."""

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from bs4 import BeautifulSoup
from plugins.base import Plugin
from plugins.chunking import ChunkConfig


@dataclass
class DownloadProgress:
    """Progress state for download operations."""

    status: str
    percentage: int = 0
    message: str = ""
    eta_seconds: int | None = None
    current_chapter: int = 0
    total_chapters: int = 0
    chapter_title: str = ""
    book_id: str = ""


@dataclass
class DownloadResult:
    """Result of a completed download."""

    book_id: str
    title: str
    output_dir: Path
    files: dict = field(default_factory=dict)  # {"epub": Path, "markdown": Path, ...}
    chapters_count: int = 0


class DownloaderPlugin(Plugin):
    """Orchestrates the complete book download workflow."""

    # Format vocabulary - discoverable by any client
    SUPPORTED_FORMATS = frozenset([
        "epub",
        "markdown",
        "markdown-chapters",
        "pdf",
        "pdf-chapters",
        "plaintext",
        "plaintext-chapters",
        "json",
        "jsonl",
        "toon",
        "chunks",
    ])

    # Aliases for user convenience (e.g., CLI shorthand)
    FORMAT_ALIASES = {
        "md": "markdown",
        "txt": "plaintext",
    }

    # Formats that only support entire book (no chapter selection)
    BOOK_ONLY_FORMATS = frozenset(["epub", "chunks"])

    @classmethod
    def parse_formats(cls, format_input: str | list[str]) -> list[str]:
        """Parse format specification into canonical format names."""
        # Handle list input
        if isinstance(format_input, list):
            raw_formats = format_input
        else:
            # Handle "all" special case
            if format_input == "all":
                return ["epub", "markdown", "pdf", "plaintext", "json", "toon", "chunks"]

            # Split comma-separated and clean
            raw_formats = [f.strip().lower() for f in format_input.split(",") if f.strip()]

        formats = []
        seen = set()

        for fmt in raw_formats:
            # Apply alias
            canonical = cls.FORMAT_ALIASES.get(fmt, fmt)

            # Handle special cases
            if canonical == "jsonl" and "json" not in seen:
                formats.append("json")
                seen.add("json")
            if canonical == "jsonl":
                formats.append("jsonl")
                seen.add("jsonl")
                continue

            # Skip invalid or duplicate
            if canonical not in cls.SUPPORTED_FORMATS or canonical in seen:
                continue

            formats.append(canonical)
            seen.add(canonical)

        return formats if formats else ["epub"]

    @classmethod
    def get_format_help(cls) -> dict[str, str]:
        """Return format descriptions for CLI help or UI display."""
        return {
            "epub": "Standard EPUB format (default)",
            "markdown": "Markdown files (alias: md)",
            "markdown-chapters": "Separate Markdown file per chapter",
            "pdf": "Single PDF file",
            "pdf-chapters": "Separate PDF per chapter",
            "plaintext": "Plain text (alias: txt)",
            "plaintext-chapters": "Separate text file per chapter",
            "json": "Structured JSON export",
            "jsonl": "JSON Lines format (includes json)",
            "toon": "Token-Oriented Object Notation (token-efficient JSON for LLMs)",
            "chunks": "Chunked content for LLM processing",
        }

    @classmethod
    def supports_chapter_selection(cls, fmt: str) -> bool:
        """Check if a format supports chapter selection."""
        canonical = cls.FORMAT_ALIASES.get(fmt, fmt)
        return canonical not in cls.BOOK_ONLY_FORMATS

    @classmethod
    def get_formats_info(cls) -> dict:
        """Return complete format information for discovery endpoints."""
        return {
            "formats": sorted(cls.SUPPORTED_FORMATS),
            "aliases": cls.FORMAT_ALIASES,
            "book_only": sorted(cls.BOOK_ONLY_FORMATS),
            "descriptions": cls.get_format_help(),
        }

    @staticmethod
    def _save_resume_state(state_path: Path, state: dict) -> None:
        temporary_path = state_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary_path.replace(state_path)

    @staticmethod
    def _load_resume_state(state_path: Path, expected: dict) -> dict | None:
        if not state_path.exists():
            return None

        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            print(f"[download] Ignoring unreadable resume state: {error}")
            return None

        if (
            not isinstance(state, dict)
            or any(state.get(key) != value for key, value in expected.items())
            or not isinstance(state.get("completed_chapters"), list)
            or not isinstance(state.get("all_css_urls"), list)
            or not all(isinstance(url, str) for url in state.get("all_css_urls", []))
            or not isinstance(state.get("all_image_urls"), list)
            or not all(isinstance(url, str) for url in state.get("all_image_urls", []))
            or not isinstance(state.get("completed_outputs"), dict)
        ):
            print("[download] Existing resume state does not match this download; starting fresh")
            return None

        return state

    @staticmethod
    def _load_processed_chapter(xhtml_path: Path) -> str:
        soup = BeautifulSoup(xhtml_path.read_text(encoding="utf-8"), "lxml")
        content = soup.find(id="sbo-rt-content")
        if content:
            return str(content)

        body = soup.find("body")
        if body:
            return "".join(str(child) for child in body.contents)

        raise ValueError(f"Cannot resume chapter without XHTML body: {xhtml_path}")

    def download(
        self,
        book_id: str,
        output_dir: Path,
        formats: list[str] | None = None,
        selected_chapters: list[int] | None = None,
        skip_images: bool = False,
        chunk_config: ChunkConfig | None = None,
        progress_callback: Callable[[DownloadProgress], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> DownloadResult:
        """Orchestrate full download pipeline for a book."""
        if formats is None:
            formats = ["epub"]

        # Helper to report progress
        def report(
            status: str,
            percentage: int = 0,
            message: str = "",
            eta_seconds: int | None = None,
            current_chapter: int = 0,
            total_chapters: int = 0,
            chapter_title: str = "",
        ):
            if progress_callback:
                progress_callback(
                    DownloadProgress(
                        status=status,
                        percentage=percentage,
                        message=message,
                        eta_seconds=eta_seconds,
                        current_chapter=current_chapter,
                        total_chapters=total_chapters,
                        chapter_title=chapter_title,
                        book_id=book_id,
                    )
                )

        # Helper to check cancellation
        def check_cancel():
            if cancel_check and cancel_check():
                return True
            return False

        # Get plugins
        book_plugin = self.kernel["book"]
        chapters_plugin = self.kernel["chapters"]
        assets_plugin = self.kernel["assets"]
        html_processor = self.kernel["html_processor"]
        output_plugin = self.kernel["output"]

        # Phase 1: Fetch metadata.
        #
        # We intentionally do NOT hard-fail here on the local JWT expiry check.
        # O'Reilly serves content for a grace period past the token's `exp`, and
        # the local clock check can lag the real session (e.g. cookies read from
        # the browser's on-disk store trail the live in-memory token). Let the
        # actual request be the source of truth — it raises a descriptive error
        # (expired vs. Akamai bot-block) if the session really is rejected.
        report("starting", 0)
        report("fetching_metadata", 5)
        book_info = book_plugin.fetch(book_id)

        # Phase 2: Fetch chapters list
        report("fetching_chapters", 10)
        all_chapters = chapters_plugin.fetch_list(book_id)
        toc = chapters_plugin.fetch_toc(book_id)
        all_chapters = chapters_plugin.reorder_by_toc(all_chapters, toc)

        # Filter chapters if selection provided
        if selected_chapters is not None:
            selected_set = set(selected_chapters)
            chapters = [ch for i, ch in enumerate(all_chapters) if i in selected_set]
        else:
            chapters = all_chapters

        # Create output directory
        book_dir = output_plugin.create_book_dir(
            output_dir=output_dir,
            book_id=book_id,
            title=book_info.get("title", ""),
            authors=book_info.get("authors"),
        )
        oebps = output_plugin.get_oebps_dir(book_dir)
        state_path = book_dir / ".download_state.json"
        expected_state = {
            "version": 1,
            "book_id": book_id,
            "chapter_signature": [
                {
                    "filename": ch["filename"],
                    "content_url": ch["content_url"],
                    "title": ch["title"],
                }
                for ch in chapters
            ],
            "selected_chapters": selected_chapters,
            "skip_images": skip_images,
        }
        state = self._load_resume_state(state_path, expected_state)
        is_resuming = state is not None
        if state is None:
            state = {
                **expected_state,
                "stage": "chapters",
                "completed_chapters": [],
                "all_css_urls": [],
                "all_image_urls": [],
                "completed_outputs": {},
                "output_request": None,
            }

        completed_chapters = {
            index
            for index in state["completed_chapters"]
            if type(index) is int
            and 0 <= index < len(chapters)
            and (oebps / chapters[index]["filename"].replace(".html", ".xhtml")).is_file()
        }
        state["completed_chapters"] = sorted(completed_chapters)
        all_css_urls = list(dict.fromkeys(state["all_css_urls"]))
        all_css_seen = set(all_css_urls)
        all_image_urls = list(dict.fromkeys(state["all_image_urls"]))
        all_image_seen = set(all_image_urls)
        chapters_data = []
        self._save_resume_state(state_path, state)
        if is_resuming:
            report(
                "resuming",
                12,
                f"Reusing {len(completed_chapters)} of {len(chapters)} saved chapters",
            )
            print(
                f"[download] Resuming {book_id}: reusing "
                f"{len(completed_chapters)} of {len(chapters)} saved chapters"
            )

        # Phase 3: Download cover
        if not skip_images:
            report("downloading_cover", 12)
            cover_url = book_info.get("cover_url")
            if cover_url:
                images_dir = output_plugin.get_images_dir(book_dir)
                images_dir.mkdir(parents=True, exist_ok=True)
                assets_plugin.download_image(cover_url, images_dir / "cover.jpg")

        # Phase 4: Process chapters, reusing completed local XHTML on retries.
        total_chapters = len(chapters)

        # ETA tracking
        chapter_times = []
        chapter_start_time = time.time()

        for i, ch in enumerate(chapters):
            if check_cancel():
                self._cleanup_on_cancel(book_dir)
                raise Exception("Download cancelled by user")

            # Calculate percentage (chapters are 15%-80% of work)
            chapter_pct = 15 + int((i / total_chapters) * 65) if total_chapters > 0 else 15

            report(
                "processing_chapters",
                chapter_pct,
                current_chapter=i + 1,
                total_chapters=total_chapters,
                chapter_title=ch.get("title", ""),
            )

            # Compute relative path prefix based on chapter depth in OEBPS
            filename = ch["filename"].replace(".html", ".xhtml")
            depth = filename.count("/")
            path_prefix = "../" * depth if depth > 0 else ""
            file_path = oebps / filename

            if i in completed_chapters:
                processed = self._load_processed_chapter(file_path)
                chapters_data.append((ch["filename"], ch["title"], processed))
                chapter_start_time = time.time()
                continue

            # Fetch and process chapter content
            raw_html = chapters_plugin.fetch_content(ch["content_url"])
            processed, images = html_processor.process(
                raw_html, book_id, skip_images=skip_images, path_prefix=path_prefix
            )

            # Collect CSS and image URLs
            for css_url in ch["stylesheets"]:
                if css_url not in all_css_seen:
                    all_css_urls.append(css_url)
                    all_css_seen.add(css_url)
            for img_url in ch["images"]:
                if img_url not in all_image_seen:
                    all_image_urls.append(img_url)
                    all_image_seen.add(img_url)
            for img_url in images:
                if img_url.startswith("http") or img_url.startswith("/"):
                    if img_url not in all_image_seen:
                        all_image_urls.append(img_url)
                        all_image_seen.add(img_url)

            # Wrap in XHTML
            css_refs = [f"{path_prefix}Styles/Style{j:02d}.css" for j in range(len(all_css_urls))]
            xhtml = html_processor.wrap_xhtml(processed, css_refs, ch["title"])

            # Write chapter file
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(xhtml, encoding='utf-8')

            chapters_data.append((ch["filename"], ch["title"], processed))
            completed_chapters.add(i)
            state["completed_chapters"] = sorted(completed_chapters)
            state["all_css_urls"] = all_css_urls
            state["all_image_urls"] = all_image_urls
            state["stage"] = "chapters"
            self._save_resume_state(state_path, state)

            # Calculate ETA based on rolling average
            chapter_time = time.time() - chapter_start_time
            chapter_times.append(chapter_time)
            chapter_start_time = time.time()

            if chapter_times:
                avg_time = sum(chapter_times[-5:]) / len(chapter_times[-5:])
                remaining = total_chapters - (i + 1)
                eta_seconds = int(avg_time * remaining)
                report(
                    "processing_chapters",
                    chapter_pct,
                    eta_seconds=eta_seconds,
                    current_chapter=i + 1,
                    total_chapters=total_chapters,
                    chapter_title=ch.get("title", ""),
                )

        # Phase 5: Download assets
        report("downloading_assets", 80, eta_seconds=None)
        state["stage"] = "assets"
        self._save_resume_state(state_path, state)

        # Normalize image URLs
        image_list = []
        for img_url in all_image_urls:
            if img_url.startswith("/"):
                img_url = f"https://learning.oreilly.com{img_url}"
            image_list.append(img_url)

        css_list = all_css_urls
        total_assets = len(css_list) + len(image_list)

        # Download CSS
        css_width = len(str(len(css_list)))

        def css_progress(completed: int, total: int):
            if total_assets > 0:
                pct = 80 + int((completed / total_assets) * 10)
                report(
                    "downloading_assets",
                    pct,
                    f"{pct:2d}% - Downloading CSS ({completed:>{css_width}}/{len(css_list)})",
                )

        assets_plugin.download_all_css(css_list, oebps, progress_callback=css_progress)

        # Download assets referenced in CSS (e.g. url() images in ::after)
        if not skip_images:
            assets_plugin.download_css_assets(css_list, oebps)
            # Inline CSS content:url() images as <img> tags (Apple Books compat)
            html_processor.inline_css_content_images(oebps)

        # Download images
        if not skip_images:
            img_width = len(str(len(image_list)))

            def image_progress(completed: int, total: int):
                if total_assets > 0:
                    pct = 80 + int(((len(css_list) + completed) / total_assets) * 10)
                    report(
                        "downloading_assets",
                        pct,
                        f"{pct:2d}% - Downloading images ({completed:>{img_width}}/{len(image_list)})",
                    )

            assets_plugin.download_all_images(
                image_list, oebps, progress_callback=image_progress
            )

        state["stage"] = "outputs"
        output_request = {
            "formats": formats,
            "chunk_config": (
                {
                    "chunk_size": chunk_config.chunk_size,
                    "overlap": chunk_config.overlap,
                    "respect_boundaries": chunk_config.respect_boundaries,
                }
                if chunk_config
                else None
            ),
        }
        resume_outputs = state.get("output_request") == output_request
        if not resume_outputs:
            state["completed_outputs"] = {}
            state["output_request"] = output_request
        self._save_resume_state(state_path, state)

        def get_completed_output(key: str):
            output = state["completed_outputs"].get(key)
            output_paths = [output] if isinstance(output, str) else output
            if not isinstance(output_paths, list) or not all(
                isinstance(path, str) for path in output_paths
            ):
                return None
            try:
                resolved_paths = [Path(path).resolve() for path in output_paths]
                for path in resolved_paths:
                    path.relative_to(book_dir.resolve())
            except (OSError, ValueError):
                return None
            if all(path.exists() for path in resolved_paths):
                return output
            return None

        def save_completed_output(key: str, output):
            state["completed_outputs"][key] = output
            self._save_resume_state(state_path, state)

        # Phase 6: Generate output formats
        result = DownloadResult(
            book_id=book_id,
            title=book_info.get("title", ""),
            output_dir=book_dir,
            chapters_count=len(chapters_data),
        )

        # EPUB
        if "epub" in formats:
            cached_epub = get_completed_output("epub")
            if cached_epub:
                result.files["epub"] = cached_epub
            else:
                report("generating_epub", 90)
                epub_plugin = self.kernel["epub"]
                epub_path = epub_plugin.generate(
                    book_info=book_info,
                    chapters=chapters,
                    toc=toc,
                    output_dir=book_dir,
                    css_files=css_list,
                    cover_image="cover.jpg",
                    clean_build_files=False,
                )
                result.files["epub"] = str(epub_path)
                save_completed_output("epub", str(epub_path))

        # Markdown
        if "markdown" in formats or "md" in formats or "markdown-chapters" in formats:
            markdown_key = "markdown-chapters" if "markdown-chapters" in formats else "markdown"
            cached_markdown = get_completed_output(markdown_key)
            if cached_markdown:
                result.files["markdown"] = cached_markdown
            else:
                report("generating_markdown", 92)
                md_plugin = self.kernel["markdown"]
                md_plugin.generate_book(book_info, chapters_data, book_dir)
                result.files["markdown"] = str(book_dir / "Markdown")
                save_completed_output(markdown_key, result.files["markdown"])

        # PDF
        if "pdf" in formats or "all" in formats or "pdf-chapters" in formats:
            pdf_plugin = self.kernel["pdf"]

            if "pdf-chapters" in formats:
                cached_pdf = get_completed_output("pdf-chapters")
                if cached_pdf is not None:
                    result.files["pdf"] = cached_pdf
                else:
                    report("generating_pdf_chapters", 95)
                    pdf_paths = pdf_plugin.generate_chapters(
                        book_info=book_info,
                        chapters=chapters,
                        output_dir=book_dir,
                        css_files=css_list,
                        skip_existing=resume_outputs,
                    )
                    result.files["pdf"] = [str(p) for p in pdf_paths]
                    save_completed_output("pdf-chapters", result.files["pdf"])
            else:
                cached_pdf = get_completed_output("pdf")
                if cached_pdf:
                    result.files["pdf"] = cached_pdf
                else:
                    report("generating_pdf", 95)
                    pdf_path = pdf_plugin.generate(
                        book_info=book_info,
                        chapters=chapters,
                        toc=toc,
                        output_dir=book_dir,
                        css_files=css_list,
                        cover_image="cover.jpg",
                    )
                    result.files["pdf"] = str(pdf_path)
                    save_completed_output("pdf", result.files["pdf"])

        # Plain text
        if "plaintext" in formats or "txt" in formats or "plaintext-chapters" in formats:
            plaintext_key = "plaintext-chapters" if "plaintext-chapters" in formats else "plaintext"
            cached_plaintext = get_completed_output(plaintext_key)
            if cached_plaintext:
                result.files["plaintext"] = cached_plaintext
            else:
                report("generating_plaintext", 96)
                plaintext_plugin = self.kernel["plaintext"]
                single_file = "plaintext-chapters" not in formats
                txt_path = plaintext_plugin.generate(
                    book_dir=book_dir,
                    book_metadata=book_info,
                    chapters_data=chapters_data,
                    single_file=single_file,
                )
                result.files["plaintext"] = str(txt_path)
                save_completed_output(plaintext_key, result.files["plaintext"])

        # JSON
        if "json" in formats:
            cached_json = get_completed_output("json")
            if cached_json:
                result.files["json"] = cached_json
            else:
                report("generating_json", 97)
                json_plugin = self.kernel["json_export"]
                json_path = json_plugin.generate(
                    book_dir=book_dir,
                    book_metadata=book_info,
                    chapters_data=chapters_data,
                    include_jsonl="jsonl" in formats,
                )
                result.files["json"] = str(json_path)
                save_completed_output("json", result.files["json"])

        # TOON
        if "toon" in formats:
            cached_toon = get_completed_output("toon")
            if cached_toon:
                result.files["toon"] = cached_toon
            else:
                report("generating_toon", 97)
                toon_plugin = self.kernel["toon_export"]
                toon_path = toon_plugin.generate(
                    book_dir=book_dir,
                    book_metadata=book_info,
                    chapters_data=chapters_data,
                )
                result.files["toon"] = str(toon_path)
                save_completed_output("toon", result.files["toon"])

        # Chunks
        if "chunks" in formats:
            cached_chunks = get_completed_output("chunks")
            if cached_chunks:
                result.files["chunks"] = cached_chunks
            else:
                report("generating_chunks", 98)
                chunking_plugin = self.kernel["chunking"]
                chunks_path = chunking_plugin.generate(
                    book_dir=book_dir,
                    book_metadata=book_info,
                    chapters_data=chapters_data,
                    config=chunk_config,
                )
                result.files["chunks"] = str(chunks_path)
                save_completed_output("chunks", result.files["chunks"])

        if "epub" in formats:
            self.kernel["epub"].cleanup_build_artifacts(book_dir)
        state["stage"] = "completed"
        self._save_resume_state(state_path, state)
        report("completed", 100)
        return result

    def _cleanup_on_cancel(self, book_dir: Path):
        """Clean up partially downloaded book on cancellation."""
        if book_dir.exists():
            shutil.rmtree(book_dir)
