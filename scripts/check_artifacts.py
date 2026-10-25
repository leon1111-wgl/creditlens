"""Check local documentation links and extract browser JS for a syntax check."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
broken = []
for path in [ROOT / "README.md", *list((ROOT / "docs").rglob("*.md"))]:
    content = path.read_text(encoding="utf-8-sig")
    for target in re.findall(r"\]\(([^)]+)\)", content):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) or target.startswith("#"):
            continue
        target = target.split("#", 1)[0]
        if not (path.parent / target).exists():
            broken.append(f"{path.name}: {target}")
if broken:
    raise SystemExit("\n".join(broken))
for name in ["workbench", "review", "research", "documents", "experiments", "transfer"]:
    html = (ROOT / f"creditlens/{name}.html").read_text(encoding="utf-8")
    script = re.search(r"<script>(.*?)</script>", html, re.S)
    if not script:
        raise SystemExit("No browser script: " + name)
    output = ROOT / f"artifacts/{name}-syntax.js"
    output.write_text(script.group(1), encoding="utf-8")
print("Local Markdown file links valid; browser JS extracted for node --check.")
