"""PDF generation plugin using WeasyPrint."""

import html
import posixpath
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from utils.files import sanitize_filename

from .base import Plugin


def _patch_macos_homebrew_dylib_loading():
    """Prefer real Homebrew .dylib paths for libraries loaded by WeasyPrint."""
    if sys.platform != "darwin":
        return

    import cffi

    original_dlopen = cffi.FFI.dlopen
    if getattr(original_dlopen, "_oreilly_homebrew_dylib_patch", False):
        return

    brew_lib_dirs = (Path("/opt/homebrew/lib"), Path("/usr/local/lib"))
    aliases = {
        "libpango-1.0-0": ("libpango-1.0.dylib", "libpango-1.0.0.dylib"),
        "pango-1.0-0": ("libpango-1.0.dylib", "libpango-1.0.0.dylib"),
        "pango-1.0": ("libpango-1.0.dylib", "libpango-1.0.0.dylib"),
        "libpangoft2-1.0-0": (
            "libpangoft2-1.0.dylib",
            "libpangoft2-1.0.0.dylib",
        ),
        "pangoft2-1.0-0": (
            "libpangoft2-1.0.dylib",
            "libpangoft2-1.0.0.dylib",
        ),
        "pangoft2-1.0": (
            "libpangoft2-1.0.dylib",
            "libpangoft2-1.0.0.dylib",
        ),
        "libgobject-2.0-0": ("libgobject-2.0.dylib", "libgobject-2.0.0.dylib"),
        "gobject-2.0-0": ("libgobject-2.0.dylib", "libgobject-2.0.0.dylib"),
        "gobject-2.0": ("libgobject-2.0.dylib", "libgobject-2.0.0.dylib"),
        "libharfbuzz-0": ("libharfbuzz.dylib", "libharfbuzz.0.dylib"),
        "harfbuzz-0.0": ("libharfbuzz.dylib", "libharfbuzz.0.dylib"),
        "libharfbuzz-subset-0": (
            "libharfbuzz-subset.dylib",
            "libharfbuzz-subset.0.dylib",
        ),
        "harfbuzz-subset-0.0": (
            "libharfbuzz-subset.dylib",
            "libharfbuzz-subset.0.dylib",
        ),
        "libfontconfig-1": ("libfontconfig.dylib", "libfontconfig.1.dylib"),
        "fontconfig-1": ("libfontconfig.dylib", "libfontconfig.1.dylib"),
    }

    def dlopen(self, name, flags=0):
        if isinstance(name, str) and name in aliases:
            for lib_dir in brew_lib_dirs:
                for filename in aliases[name]:
                    dylib_path = lib_dir / filename
                    if dylib_path.exists():
                        return original_dlopen(self, str(dylib_path), flags)

        return original_dlopen(self, name, flags)

    dlopen._oreilly_homebrew_dylib_patch = True
    cffi.FFI.dlopen = dlopen


class PdfPlugin(Plugin):
    """Generates PDF from downloaded book content using WeasyPrint."""

    def __init__(self):
        self._weasyprint = None

    @property
    def weasyprint(self):
        """Lazy import WeasyPrint to avoid import errors if not installed."""
        if self._weasyprint is None:
            try:
                _patch_macos_homebrew_dylib_loading()
                import weasyprint
                self._weasyprint = weasyprint
            except ImportError as e:
                raise ImportError(
                    "WeasyPrint is required for PDF generation. "
                    "Install with: pip install weasyprint\n"
                    "System dependencies (macOS): brew install pango\n"
                    "System dependencies (Ubuntu): apt install libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0"
                ) from e
        return self._weasyprint

    def generate(
        self,
        book_info: dict,
        chapters: list[dict],
        toc: list[dict],
        output_dir: Path,
        css_files: list[str],
        cover_image: str | None = None,
    ) -> Path:
        """
        Generate a single PDF containing all chapters.

        Args:
            book_info: Book metadata (title, authors, isbn, publisher)
            chapters: List of chapter dicts with filename, title, order
            toc: Nested table of contents structure
            output_dir: Path to output/{book_id}/ directory
            css_files: List of CSS filenames in OEBPS/Styles/
            cover_image: Optional cover image filename in OEBPS/Images/

        Returns:
            Path to generated PDF file
        """
        output_dir = Path(output_dir)
        oebps = output_dir / "OEBPS"

        html_content = self._build_combined_html(
            book_info=book_info,
            chapters=chapters,
            toc=toc,
            oebps=oebps,
            css_files=css_files,
            cover_image=cover_image,
        )

        title = book_info.get("title", "book")
        safe_title = sanitize_filename(title)
        pdf_path = output_dir / f"{safe_title}.pdf"

        self._write_pdf(html_content, oebps, pdf_path)

        return pdf_path

    def _write_pdf(self, html_content: str, base_dir: Path, output_path: Path) -> None:
        temporary_path = output_path.with_name(f".{output_path.name}.tmp")

        try:
            self.weasyprint.HTML(
                string=html_content,
                base_url=str(base_dir),
            ).write_pdf(str(temporary_path))
        except Exception as weasyprint_error:
            if temporary_path.exists():
                temporary_path.unlink()
            print(
                "[pdf] WeasyPrint could not render this PDF; "
                f"trying the fpdf2 fallback ({weasyprint_error})"
            )
            try:
                self._write_fpdf_pdf(html_content, base_dir, temporary_path)
            except Exception as fallback_error:
                if temporary_path.exists():
                    temporary_path.unlink()
                raise RuntimeError(
                    "PDF generation failed with WeasyPrint "
                    f"({weasyprint_error}) and fpdf2 ({fallback_error})"
                ) from fallback_error

        temporary_path.replace(output_path)

    @staticmethod
    def _write_fpdf_pdf(html_content: str, base_dir: Path, output_path: Path) -> None:
        from fpdf import FPDF

        soup = BeautifulSoup(html_content, "lxml")
        for element in soup(["head", "style", "script", "link", "meta", "svg", "noscript"]):
            element.decompose()

        for image in soup.find_all("img"):
            source = urlsplit(image.get("src", ""))
            if source.scheme or source.netloc:
                image.replace_with(f"[Image: {image.get('alt')}]" if image.get("alt") else "")
                continue

            image_path = (base_dir / unquote(source.path)).resolve()
            try:
                image_path.relative_to(base_dir.resolve())
            except ValueError:
                image_path = None

            if image_path and image_path.is_file():
                image["src"] = str(image_path)
            else:
                image.replace_with(f"[Image: {image.get('alt')}]" if image.get("alt") else "")

        for element in soup.find_all(True):
            if element.name in {
                "html", "body", "main", "article", "section", "div",
                "header", "footer", "figure", "figcaption",
            }:
                element.unwrap()
                continue
            element.attrs = {
                key: value
                for key, value in element.attrs.items()
                if key in {"href", "src", "alt", "title", "colspan", "rowspan", "width", "height"}
            }

        pdf = FPDF(format="A4")
        pdf.set_margins(18, 18, 18)
        pdf.set_auto_page_break(auto=True, margin=18)
        font_files = PdfPlugin._fallback_font_files()
        if font_files:
            for style in ("", "B", "I", "BI"):
                pdf.add_font("BookSans", style=style, fname=str(font_files.get(style, font_files[""])))
            pdf.set_font("BookSans", size=10)
        else:
            pdf.set_font("helvetica", size=10)
        pdf.add_page()

        body = soup.body
        body_html = "".join(str(child) for child in body.contents) if body else str(soup)
        pdf.write_html(body_html)
        pdf.output(str(output_path))

    @staticmethod
    def _fallback_font_files() -> dict[str, Path]:
        font_sets = (
            {
                "": Path(r"C:\Windows\Fonts\arial.ttf"),
                "B": Path(r"C:\Windows\Fonts\arialbd.ttf"),
                "I": Path(r"C:\Windows\Fonts\ariali.ttf"),
                "BI": Path(r"C:\Windows\Fonts\arialbi.ttf"),
            },
            {
                "": Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                "B": Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                "I": Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"),
                "BI": Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf"),
            },
            {
                "": Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                "B": Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
                "I": Path("/System/Library/Fonts/Supplemental/Arial Italic.ttf"),
                "BI": Path("/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"),
            },
        )
        for font_files in font_sets:
            if font_files[""].is_file():
                return {
                    style: path if path.is_file() else font_files[""]
                    for style, path in font_files.items()
                }
        return {}

    def generate_chapters(
        self,
        book_info: dict,
        chapters: list[dict],
        output_dir: Path,
        css_files: list[str],
        skip_existing: bool = False,
    ) -> list[Path]:
        """
        Generate individual PDF files for each chapter.

        Args:
            book_info: Book metadata (title, authors, isbn, publisher)
            chapters: List of chapter dicts with filename, title, order
            output_dir: Path to output/{book_id}/ directory
            css_files: List of CSS filenames in OEBPS/Styles/

        Returns:
            List of paths to generated PDF files
        """
        output_dir = Path(output_dir)
        oebps = output_dir / "OEBPS"
        pdf_dir = output_dir / "PDF"
        pdf_dir.mkdir(exist_ok=True)

        # Load CSS
        print_css = self._get_print_css()
        original_css = self._load_css_files(oebps, css_files)

        pdf_paths = []

        for i, chapter in enumerate(chapters):
            xhtml_path = oebps / chapter["filename"].replace(".html", ".xhtml")
            if not xhtml_path.exists():
                continue

            body = self._extract_chapter_body(xhtml_path, oebps)
            chapter_title = self._escape_html(chapter.get("title", f"Chapter {i+1}"))
            lang_attr, cjk_css = self._get_language_style(book_info)

            chapter_html = f'''<!DOCTYPE html>
<html lang="{lang_attr}">
<head>
    <meta charset="utf-8">
    <title>{chapter_title}</title>
    <style>{print_css}</style>
    <style>{original_css}</style>
    <style>{cjk_css}</style>
</head>
<body>
    <section class="chapter">
        <h1 class="chapter-title">{chapter_title}</h1>
        {body}
    </section>
</body>
</html>'''

            # Create filename with order prefix
            safe_title = sanitize_filename(chapter.get("title", f"chapter_{i+1}"))
            pdf_filename = f"{i+1:03d}_{safe_title}.pdf"
            pdf_path = pdf_dir / pdf_filename

            if skip_existing and self._is_pdf_file(pdf_path):
                pdf_paths.append(pdf_path)
                continue

            self._write_pdf(chapter_html, oebps, pdf_path)
            pdf_paths.append(pdf_path)

        return pdf_paths

    @staticmethod
    def _is_pdf_file(path: Path) -> bool:
        if not path.is_file():
            return False
        with path.open("rb") as pdf_file:
            return pdf_file.read(5) == b"%PDF-"

    def _build_combined_html(
        self,
        book_info: dict,
        chapters: list[dict],
        toc: list[dict],
        oebps: Path,
        css_files: list[str],
        cover_image: str | None,
    ) -> str:
        """Build single HTML document combining all chapters."""
        print_css = self._get_print_css()
        original_css = self._load_css_files(oebps, css_files)

        cover_html = self._generate_cover_html(book_info, cover_image)
        toc_html = self._generate_toc_html(toc, chapters)

        chapters_html_parts = []

        for chapter in chapters:
            xhtml_path = oebps / chapter["filename"].replace(".html", ".xhtml")
            if not xhtml_path.exists():
                continue

            body = self._extract_chapter_body(xhtml_path, oebps)
            chapter_id = Path(chapter["filename"]).stem
            chapter_title = self._escape_html(chapter.get("title", ""))

            chapters_html_parts.append(f'''
    <section class="chapter" id="{chapter_id}">
        <h1 class="chapter-title">{chapter_title}</h1>
        {body}
    </section>''')

        chapters_html = "\n".join(chapters_html_parts)
        title = self._escape_html(book_info.get("title", "Untitled"))
        lang_attr, cjk_css = self._get_language_style(book_info)

        return f'''<!DOCTYPE html>
<html lang="{lang_attr}">
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    <style>{print_css}</style>
    <style>{original_css}</style>
    <style>{cjk_css}</style>
</head>
<body>
    {cover_html}
    {toc_html}
    {chapters_html}
</body>
</html>'''

    def _generate_cover_html(
        self,
        book_info: dict,
        cover_image: str | None,
    ) -> str:
        """Generate HTML for cover page."""
        title = self._escape_html(book_info.get("title", "Untitled"))
        authors = ", ".join(book_info.get("authors", []))
        authors = self._escape_html(authors) if authors else ""
        publishers = ", ".join(book_info.get("publishers", []))
        publishers = self._escape_html(publishers) if publishers else ""

        cover_img = ""
        if cover_image:
            cover_img = f'<img src="Images/{cover_image}" alt="Cover">'

        author_html = f'<p class="authors">{authors}</p>' if authors else ""
        publisher_html = f'<p class="publisher">{publishers}</p>' if publishers else ""

        return f'''
    <section class="cover-page">
        {cover_img}
        <h1>{title}</h1>
        {author_html}
        {publisher_html}
    </section>'''

    def _generate_toc_html(self, toc: list[dict], chapters: list[dict]) -> str:
        """Generate HTML table of contents with internal links."""
        if not toc:
            return ""

        def render_item(item: dict) -> str:
            title = self._escape_html(item.get("title", "Untitled"))

            # Get href from reference_id and convert to chapter ID
            href = item.get("reference_id", "")
            if href:
                # Extract filename from reference like "urn:orm:book:123/-/ch001.html"
                if "-/" in href:
                    href = href.split("-/")[-1]
                chapter_id = Path(href).stem
                link = f'<a href="#{chapter_id}">{title}</a>'
            else:
                link = title

            children_html = ""
            if item.get("children"):
                children_items = "".join(
                    f"<li>{render_item(child)}</li>"
                    for child in item["children"]
                )
                children_html = f"<ul>{children_items}</ul>"

            return f"{link}{children_html}"

        toc_items = "".join(f"<li>{render_item(item)}</li>" for item in toc)

        return f'''
    <section class="toc-page">
        <h2>Table of Contents</h2>
        <ul>{toc_items}</ul>
    </section>'''

    def _extract_chapter_body(self, xhtml_path: Path, oebps: Path) -> str:
        """Extract body content from XHTML file, with URLs relative to OEBPS."""
        content = xhtml_path.read_text(encoding="utf-8")

        # Find <body> content
        body_match = re.search(
            r'<body[^>]*>(.*?)</body>',
            content,
            re.DOTALL | re.IGNORECASE,
        )

        body = body_match.group(1) if body_match else content

        # Chapters may live in a subdirectory (e.g. OEBPS/Text/) and reference
        # assets as "../Images/x.png". The combined document is rendered with
        # OEBPS as its base, so re-anchor those references to OEBPS.
        chapter_dir = xhtml_path.parent.relative_to(oebps).as_posix()
        return re.sub(
            r'(<img\b[^>]*?\ssrc=)(["\'])(.*?)\2',
            lambda m: f"{m.group(1)}{m.group(2)}{self._rebase_url(m.group(3), chapter_dir)}{m.group(2)}",
            body,
            flags=re.DOTALL | re.IGNORECASE,
        )

    def _rebase_url(self, url: str, from_dir: str) -> str:
        """Rewrite a URL relative to OEBPS/from_dir so it is relative to OEBPS."""
        if from_dir in ("", ".") or re.match(r'^([a-z][a-z0-9+.-]*:|/|#)', url, re.IGNORECASE):
            return url
        return posixpath.normpath(posixpath.join(from_dir, url))

    def _load_css_files(self, oebps: Path, css_files: list[str]) -> str:
        """Load and concatenate CSS files."""
        css_parts = []
        styles_dir = oebps / "Styles"

        for i, _ in enumerate(css_files):
            css_path = styles_dir / f"Style{i:02d}.css"
            if css_path.exists():
                # The CSS is inlined into the document, so url() references
                # relative to Styles/ must be re-anchored to OEBPS.
                css_parts.append(re.sub(
                    r'url\(\s*(["\']?)([^)"\']+)\1\s*\)',
                    lambda m: f'url("{self._rebase_url(m.group(2).strip(), "Styles")}")',
                    css_path.read_text(encoding="utf-8"),
                ))

        return "\n".join(css_parts)

    def _get_language_style(self, book_info: dict) -> tuple[str, str]:
        """Return (html lang attribute, extra CSS) for CJK book languages.

        O'Reilly book stylesheets assume Latin fonts; Japanese/Chinese/Korean
        editions would render tofu in the generated PDF without an explicit
        fallback stack. The CSS is appended after the book's own styles so it
        wins the cascade where it matters.
        """
        lang = (book_info.get("language") or "")[:2].lower()
        if lang not in ("ja", "zh", "ko"):
            return lang or "", ""

        stacks = {
            "ja": '"Hiragino Mincho ProN", "Hiragino Sans", "Yu Mincho", '
                  '"Noto Serif CJK JP", "Noto Sans CJK JP", '
                  '"Source Han Serif JP", "Source Han Sans JP", "MS PMincho"',
            "zh": '"Songti SC", "Hiragino Sans GB", "Noto Serif CJK SC", '
                  '"Noto Sans CJK SC", "Source Han Serif SC", "SimSun"',
            "ko": '"Apple SD Gothic Neo", "Nanum Myeongjo", '
                  '"Noto Serif CJK KR", "Noto Sans CJK KR", "Malgun Gothic"',
        }
        css = (
            f'body, .chapter, .chapter-title {{ font-family: {stacks[lang]}, serif; }}\n'
            f':lang({lang}) {{ word-break: normal; overflow-wrap: anywhere; }}'
        )
        return lang, css

    def _get_print_css(self) -> str:
        """Return print-specific CSS for PDF generation."""
        css_path = Path(__file__).parent / "pdf_styles" / "print.css"
        if css_path.exists():
            return css_path.read_text(encoding="utf-8")

        # Fallback inline CSS if file doesn't exist
        return self._get_fallback_print_css()

    def _get_fallback_print_css(self) -> str:
        """Inline fallback CSS for print styling."""
        return '''
@page {
    size: Letter;
    margin: 1in 0.75in;
    @bottom-center { content: counter(page); font-size: 9pt; }
}
@page :first {
    @bottom-center { content: none; }
}
.cover-page {
    page-break-after: always;
    text-align: center;
    padding-top: 2in;
}
.cover-page img { max-width: 100%; max-height: 6in; }
.cover-page h1 { font-size: 24pt; margin-top: 1in; }
.cover-page .authors { font-size: 14pt; color: #333; margin-top: 0.5in; }
.toc-page { page-break-after: always; }
.toc-page h2 { font-size: 18pt; margin-bottom: 1em; }
.toc-page ul { list-style: none; padding: 0; }
.toc-page li { margin: 0.5em 0; }
.toc-page a { color: #000; text-decoration: none; }
.chapter { page-break-before: always; }
.chapter:first-of-type { page-break-before: auto; }
.chapter-title {
    font-size: 20pt;
    margin-bottom: 1em;
    bookmark-level: 1;
    bookmark-label: content();
}
img { max-width: 100%; height: auto; }
figure { page-break-inside: avoid; }
pre, code {
    font-family: "Courier New", monospace;
    font-size: 9pt;
    background: #f5f5f5;
    padding: 0.5em;
    page-break-inside: avoid;
}
table { page-break-inside: avoid; border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #ddd; padding: 0.5em; }
p { orphans: 3; widows: 3; }
'''

    def _escape_html(self, text: str) -> str:
        """Escape HTML special characters."""
        return html.escape(str(text)) if text else ""
