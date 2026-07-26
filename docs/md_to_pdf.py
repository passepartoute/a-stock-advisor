#!/usr/bin/env python3
"""Render Mermaid diagrams from a Markdown file and convert to PDF via Chrome."""
import base64
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME_EXE = Path("C:/Program Files/Google/Chrome/Application/chrome.exe")
MMDC_EXE = Path("C:/Users/Administrator/AppData/Local/Temp/md2pdf/node_modules/.bin/mmdc.cmd")


def run_mmdc(input_path: Path, output_path: Path) -> None:
    """Call mermaid-cli (mmdc) using system Chrome."""
    env = dict(subprocess.os.environ)
    env["PUPPETEER_EXECUTABLE_PATH"] = str(CHROME_EXE)
    bin_dir = str(MMDC_EXE.parent)
    env["PATH"] = bin_dir + ";" + env.get("PATH", "")
    cmd = [
        str(MMDC_EXE),
        "-i", str(input_path),
        "-o", str(output_path),
        "-b", "white",
        "-s", "2",  # scale factor for better resolution
    ]
    subprocess.run(cmd, check=True, env=env, cwd=str(input_path.parent))


def process_markdown(md_path: Path, output_dir: Path) -> tuple[Path, list[Path]]:
    """Replace mermaid blocks with image references, return new markdown and images."""
    text = md_path.read_text(encoding="utf-8")
    images: list[Path] = []

    def replacer(match: re.Match) -> str:
        diagram = match.group(1).strip()
        idx = len(images) + 1
        mmd_path = output_dir / f"diagram_{idx:03d}.mmd"
        img_path = output_dir / f"diagram_{idx:03d}.png"
        mmd_path.write_text(diagram, encoding="utf-8")
        print(f"Rendering diagram {idx}...", file=sys.stderr)
        run_mmdc(mmd_path, img_path)
        images.append(img_path)
        return f"![Diagram {idx}]({img_path.name})"

    new_text = re.sub(r"```mermaid\n(.*?)\n```", replacer, text, flags=re.DOTALL)
    new_md = output_dir / md_path.name
    new_md.write_text(new_text, encoding="utf-8")
    return new_md, images


def md_to_html(md_path: Path, html_path: Path, image_dir: Path) -> None:
    """Use pandoc to convert markdown to standalone HTML."""
    cmd = [
        "pandoc",
        str(md_path),
        "-o", str(html_path),
        "--standalone",
        "--embed-resources",
        "--metadata", "title=A-Stock-Advisor 选股策略分析",
    ]
    subprocess.run(cmd, check=True, cwd=str(image_dir))


def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    """Use Chrome headless to print HTML to PDF."""
    cmd = [
        str(CHROME_EXE),
        "--headless",
        "--disable-gpu",
        "--run-all-compositor-stages-before-draw",
        "--print-to-pdf-no-header",
        f"--print-to-pdf={pdf_path}",
        f"file:///{html_path.as_posix()}",
    ]
    subprocess.run(cmd, check=True, cwd=str(html_path.parent))


def main() -> int:
    md_path = Path(sys.argv[1]).resolve()
    pdf_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else md_path.with_suffix(".pdf")

    # Avoid UNC cwd issues on Windows by switching to a local temp directory.
    os.chdir(tempfile.gettempdir())

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        new_md, images = process_markdown(md_path, tmp)
        html_path = tmp / "output.html"
        md_to_html(new_md, html_path, tmp)
        html_to_pdf(html_path, pdf_path)

    print(f"PDF generated: {pdf_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
