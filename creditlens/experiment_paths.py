"""Keep local reproductions separate from frozen/public experiment evidence.

Validation is a preflight guard against accidental overwrites, including existing
filesystem aliases. It is not a sandbox against concurrent filesystem changes.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


EXPERIMENTS = frozenset({"document-ai", "document-ai-words", "visual-robustness"})
REPORT_NAMES = {"document-ai": "document-ai-report.json",
                "document-ai-words": "document-ai-word-report.json",
                "visual-robustness": "visual-robustness-report.json"}


def default_local_dir(root: Path, experiment: str) -> Path:
    if experiment not in EXPERIMENTS:
        raise ValueError("Unknown local experiment")
    return Path(root)/".run/reproductions"/experiment


def default_report_path(root: Path, experiment: str) -> Path:
    if experiment not in EXPERIMENTS:
        raise ValueError("Unknown local experiment")
    return Path(root)/".run/reproductions"/REPORT_NAMES[experiment]


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(Path(path).expanduser()))


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError("Cannot resolve experiment output path: "+str(path)) from exc


def validate_write_path(root: Path, path: Path, *, directory: bool = False) -> Path:
    """Resolve a destination without writing, rejecting public paths and aliases.

    Repository outputs belong under .run; explicitly selected paths outside the
    repository are supported. A directory containing the repository is never a
    valid output root. Existing multiply-linked files are rejected, because their
    other names could be frozen evidence even when this name looks private.
    """
    lexical_root = _absolute(root)
    resolved_root = _resolve(Path(root).expanduser())
    lexical = _absolute(path)
    # Resolve the original spelling too: collapsing "link/.." first can
    # hide the path that the filesystem would actually follow.
    resolved = _resolve(Path(path).expanduser())
    roots = {lexical_root, resolved_root}
    protected = {base/name for base in roots for name in ("artifacts", "configs", "data")}
    protected |= {_resolve(p) for p in tuple(protected)}
    for candidate in (lexical, resolved):
        if any(candidate.is_relative_to(p) for p in protected):
            raise ValueError("Experiment output cannot overwrite frozen/public evidence: "+str(path))
        for base in roots:
            if candidate.is_relative_to(base) and not candidate.is_relative_to(base/".run"):
                raise ValueError("Repository experiment outputs must be inside .run: "+str(path))
            if directory and base.is_relative_to(candidate):
                raise ValueError("Experiment directory cannot contain the repository: "+str(path))
    if resolved.exists():
        if directory and not resolved.is_dir():
            raise ValueError("Experiment local directory is not a directory: "+str(path))
        if not directory:
            if not resolved.is_file():
                raise ValueError("Experiment report/output is not a regular file: "+str(path))
            if resolved.stat().st_nlink > 1:
                raise ValueError("Multiply-linked experiment outputs are not safe to overwrite: "+str(path))
    for parent in resolved.parents:
        if parent.exists() and not parent.is_dir():
            raise ValueError("Experiment output parent is not a directory: "+str(parent))
    return resolved


@dataclass(frozen=True)
class ExperimentPaths:
    output: Path
    local_dir: Path


def validate_experiment_paths(root: Path, output: Path, local_dir: Path, *,
                              read_only_dirs: tuple[Path, ...] = (),
                              allow_registered_preview: bool = False) -> ExperimentPaths:
    """Validate all existing local descendants before importing OCR or imaging.

    Checking only the directory root misses a nested symlink/junction into a
    public directory, or an existing hard-linked output file.
    """
    report = validate_write_path(root, output)
    local = validate_write_path(root, local_dir, directory=True)
    if report.is_relative_to(local) or local.is_relative_to(report):
        raise ValueError("Report file and local output directory conflict")
    preview = _resolve(Path(root).expanduser()/".run/document-ai")
    if report.is_relative_to(preview) or preview.is_relative_to(report):
        raise ValueError("Report output cannot overwrite registered document previews")
    if local.is_relative_to(preview) or preview.is_relative_to(local):
        if not allow_registered_preview or local != preview:
            raise ValueError("Local output cannot overwrite registered document previews")
    transfer = _resolve(Path(root).expanduser()/".run/document-transfer")
    if (report.is_relative_to(transfer) or transfer.is_relative_to(report) or
        local.is_relative_to(transfer) or transfer.is_relative_to(local)):
        raise ValueError("Experiment outputs cannot overwrite registered transfer evidence")
    for source in read_only_dirs:
        source = _resolve(Path(source).expanduser())
        if (report.is_relative_to(source) or source.is_relative_to(report) or
            local.is_relative_to(source) or source.is_relative_to(local)):
            raise ValueError("Experiment outputs must be separate from the read-only input directory")
    if local.exists():
        for base, directories, files in os.walk(local, followlinks=False):
            for name in directories:
                child = validate_write_path(root, Path(base)/name, directory=True)
                if not child.is_relative_to(local):
                    raise ValueError("Local output directory alias escapes its root: "+str(Path(base)/name))
                if (Path(base)/name).is_symlink() or (hasattr(Path(base)/name, "is_junction") and
                                                    (Path(base)/name).is_junction()):
                    # Do not leave unvisited alias descendants that may hide
                    # further redirects or hard links.
                    raise ValueError("Nested directory aliases are not valid local outputs: "+str(Path(base)/name))
            for name in files:
                child = validate_write_path(root, Path(base)/name)
                if not child.is_relative_to(local):
                    raise ValueError("Local output file alias escapes its root: "+str(Path(base)/name))
    return ExperimentPaths(output=report, local_dir=local)
