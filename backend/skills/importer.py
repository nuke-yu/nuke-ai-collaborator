"""External skill import pipeline (Plan B §4).

Given a LOCAL repo directory (the git clone is a thin wrapper, clone_and_import),
find every `<name>/SKILL.md`, validate it, copy valid skills into the target
pool with symlink-escape protection (reusing SkillStore.copy), and write an
external_skills registry row per imported skill. Imported skills are untrusted:
inline shell is already inert (DFT-022); we additionally record high-privilege
tool hits for the operator UI.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import constants as C
from . import registry
from .metadata import parse_skill_meta, _is_safe_name
from .store import SkillStore
from .lifecycle import file_lock
from workspace import layout

# Host allowlist for git import. Default allows github.com plus private/internal
# hosts; tighten in production via NUKE_SKILL_IMPORT_HOSTS (comma-separated).
_DEFAULT_ALLOWED_HOSTS = {"github.com"}
_CLONE_TIMEOUT_SECONDS = 120


class _DirScope:
    """Minimal scope shim for SkillStore.copy: `dir()` returns a fixed dir."""
    def __init__(self, d: Path):
        self._d = d

    def dir(self) -> Path:
        return self._d


def classify_platforms(meta: dict) -> str:
    return meta.get("platforms") or "pure"


def scan_high_privilege(skill_dir: Path) -> str:
    """Comma-joined HIGH_PRIVILEGE_TOOLS mentioned anywhere in the skill's text."""
    texts = []
    for p in skill_dir.rglob("*.md"):
        try:
            texts.append(p.read_text(encoding="utf-8").lower())
        except Exception:
            continue
    blob = "\n".join(texts)
    return ",".join(tool for tool in C.HIGH_PRIVILEGE_TOOLS if tool in blob)


def _safe_dest(name: str) -> None:
    if not _is_safe_name(name):
        raise ValueError(f"unsafe skill name: {name!r}")


def _repo_name_of(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # Remove branch/tree segments if any
    for sep in ("/tree/", "/src/", "/blob/"):
        if sep in url:
            url = url.split(sep, 1)[0]
    parts = url.split("/")
    return parts[-1]


def _pool_dir(scope_kind: str, group_id: int) -> Path:
    if scope_kind == "global":
        return layout.external_global_skills_dir()
    return layout.group_external_skills_dir(group_id)


async def import_from_dir(repo_dir, scope_kind: str, group_id: int,
                          source_url: str, ref: str, commit_sha: str,
                          imported_by: int | None, subdir: str = "") -> dict:
    repo_dir = Path(repo_dir)
    target_dir = repo_dir
    if subdir:
        target_dir = (repo_dir / subdir).resolve()
        repo_dir_resolved = repo_dir.resolve()
        if not target_dir.is_relative_to(repo_dir_resolved):
            raise ValueError("unsafe subdirectory path")
        if not target_dir.exists():
            raise ValueError(f"subdirectory not found in repository: {subdir}")

    pool = _pool_dir(scope_kind, group_id)
    store = SkillStore()
    dst_scope = _DirScope(pool)

    imported, rejected = [], []
    # Handle single file target (e.g. if URL pointed directly to a SKILL.md file)
    if target_dir.is_file():
        if target_dir.name == "SKILL.md":
            skill_mds = [target_dir]
        else:
            skill_mds = []
    else:
        skill_mds = sorted(target_dir.rglob("SKILL.md"))

    # Every directory holding a SKILL.md is one skill.
    for skill_md in skill_mds:
        skill_dir = skill_md.parent
        
        # If SKILL.md is at the root of the repository, the skill's name
        # should be the repository name instead of the temp directory name.
        is_root_skill = (skill_dir.resolve() == repo_dir.resolve())
        dest_name = _repo_name_of(source_url) if is_root_skill else skill_dir.name

        try:
            _safe_dest(dest_name)
        except ValueError as e:
            rejected.append({"path": str(skill_dir), "reason": str(e)})
            continue

        # parse_skill_meta surfaces description from frontmatter
        meta = parse_skill_meta(skill_md)
        if not meta.get("description"):
            rejected.append({"path": str(skill_dir), "reason": "missing description"})
            continue

        platforms = classify_platforms(meta)
        high_priv = scan_high_privilege(skill_dir)
        version = meta.get("version", "")

        # Copy into the pool
        try:
            if is_root_skill:
                dst_folder = dst_scope.dir() / dest_name
                # Check for escaping symlinks
                for p in skill_dir.rglob("*"):
                    if p.is_symlink():
                        resolved = p.resolve()
                        if not resolved.is_relative_to(skill_dir.resolve()):
                            raise ValueError(f"symlink escapes scope directory: {p} -> {resolved}")
                with file_lock(dst_folder / "SKILL.md"):
                    if dst_folder.exists():
                        shutil.rmtree(dst_folder)
                    shutil.copytree(skill_dir, dst_folder, symlinks=False)
            else:
                src_scope = _DirScope(skill_dir.parent)
                store.copy(src_scope, skill_dir.name, dst_scope)
        except (ValueError, FileNotFoundError) as e:
            rejected.append({"path": str(skill_dir), "reason": f"copy failed: {e}"})
            continue

        try:
            rid = await registry.register(
                dest_name, scope_kind, group_id, source_url, ref, commit_sha,
                version, platforms, high_priv, imported_by,
            )
        except ValueError:
            rejected.append({"path": str(skill_dir), "reason": "duplicate name in scope"})
            continue

        imported.append({"id": rid, "name": dest_name, "version": version,
                         "platforms": platforms, "high_privilege": high_priv})

    return {"imported": imported, "rejected": rejected}


def _allowed_hosts() -> set[str]:
    env = os.environ.get("NUKE_SKILL_IMPORT_HOSTS", "")
    extra = {h.strip().lower() for h in env.split(",") if h.strip()}
    return _DEFAULT_ALLOWED_HOSTS | extra


def _host_of(url: str) -> str:
    from urllib.parse import urlparse
    # Handle SCP-like SSH syntax: [user@]host:path/to/repo
    if "://" not in url:
        first_colon = url.find(":")
        first_slash = url.find("/")
        if first_colon != -1 and (first_slash == -1 or first_colon < first_slash):
            host_part = url[:first_colon]
            if "@" in host_part:
                return host_part.split("@")[-1].lower()
            return host_part.lower()
    return (urlparse(url).hostname or "").lower()


def _is_private_host(host: str) -> bool:
    # Internal hosts (no dot, .local, or RFC1918-looking) are allowed by default.
    if not host or "." not in host or host.endswith(".local"):
        return True
    return bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[0-1])\.)", host))


def _git_clone(url: str, ref: str, dst: str) -> str:
    """Clone shallow and return the commit sha. Raises on failure/timeout."""
    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += ["--", url, dst]
    subprocess.run(cmd, check=True, capture_output=True, timeout=_CLONE_TIMEOUT_SECONDS)
    sha = subprocess.run(["git", "-C", dst, "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True,
                         timeout=30).stdout.strip()
    return sha


def parse_git_url_subdir(url: str, ref: str = "") -> tuple[str, str, str]:
    """Parse git URL to extract clone URL, ref/branch, and subdirectory.

    Supports web URLs pointing to a branch and subdirectory.
    Examples:
    - https://github.com/owner/repo/tree/main/subdir -> clone_url='https://github.com/owner/repo.git', ref='main', subdir='subdir'
    - https://github.com/owner/repo/tree/dev -> clone_url='https://github.com/owner/repo.git', ref='dev', subdir=''
    - https://github.com/owner/repo -> clone_url='https://github.com/owner/repo.git', ref=ref, subdir=''
    """
    url = url.strip()
    if not url:
        return "", "", ""

    # Prepend https:// if it is a schemeless web URL (e.g. github.com/owner/repo)
    if "://" not in url and not url.startswith("git@"):
        url = "https://" + url

    # Handle SSH URL format
    if "://" not in url and "@" in url:
        return url, ref, ""

    for sep in ("/tree/", "/src/", "/blob/"):
        if sep in url:
            base_part, rest = url.split(sep, 1)
            # Remove GitLab/Gitea "/-" helper if present
            base_part = base_part.rstrip("/")
            if base_part.endswith("/-"):
                base_part = base_part[:-2]

            clone_url = base_part
            if not clone_url.endswith(".git"):
                clone_url += ".git"

            # rest has the form: branch_name/subdir_path
            # If the user passed an explicit ref, check if rest starts with it
            if ref and rest.startswith(ref + "/"):
                subdir = rest[len(ref) + 1:]
                final_ref = ref
            else:
                parts = rest.split("/")
                final_ref = parts[0]
                subdir = "/".join(parts[1:])
            return clone_url, final_ref, subdir

    clone_url = url
    if "github.com" in url or "gitlab.com" in url or "gitee.com" in url:
        if not clone_url.endswith(".git") and not clone_url.endswith("/"):
            clone_url = clone_url + ".git"
    return clone_url, ref, ""


async def clone_and_import(git_url: str, ref: str, scope_kind: str, group_id: int,
                           imported_by: int | None, *, _clone=None) -> dict:
    """Host-checked git clone → import_from_dir. `_clone(url, ref, dst)->sha` is
    injectable for tests (no network)."""
    clone_url, parsed_ref, subdir = parse_git_url_subdir(git_url, ref)

    host = _host_of(clone_url)
    if not (host in _allowed_hosts() or _is_private_host(host)):
        raise ValueError(f"host not allowed for skill import: {host!r}")

    clone = _clone or _git_clone
    tmp = tempfile.mkdtemp(prefix="nuke_skill_import_")
    try:
        try:
            commit_sha = clone(clone_url, parsed_ref or "", tmp) or ""
        except subprocess.CalledProcessError as e:
            stderr_msg = e.stderr.decode(errors="replace") if e.stderr else str(e)
            raise ValueError(f"git clone failed: {stderr_msg.strip()}")
        return await import_from_dir(
            tmp, scope_kind, group_id, git_url, parsed_ref or "", commit_sha, imported_by,
            subdir=subdir
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
