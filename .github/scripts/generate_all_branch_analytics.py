#!/usr/bin/env python3
"""Generate profile analytics across every branch of every public owned repository.

Commit counts are unique by SHA across all scanned branches/repositories.
Language bytes are measured from each branch's latest tree; a file present in
multiple branch snapshots is counted once per branch snapshot.
"""
from __future__ import annotations

import html
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

OWNER = os.environ.get("GITHUB_OWNER", "marufallammahim")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
API = "https://api.github.com"
ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {
    ".git", ".dart_tool", ".next", ".nuxt", ".venv", "__pycache__",
    "build", "coverage", "dist", "node_modules", "out", "Pods",
    "target", "vendor", "venv",
}
SKIP_SUFFIXES = (".g.dart", ".freezed.dart", ".min.js", ".min.css")
SKIP_FILES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pubspec.lock",
    "cargo.lock", "poetry.lock", "pipfile.lock", "composer.lock",
}
EXTENSIONS = {
    ".py": "Python", ".pyw": "Python",
    ".dart": "Dart",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".c": "C", ".h": "C", ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS", ".sass": "SCSS",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".swift": "Swift", ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".ps1": "PowerShell", ".lua": "Lua", ".sql": "SQL",
    ".xml": "XML", ".gradle": "Groovy", ".groovy": "Groovy",
    ".yml": "YAML", ".yaml": "YAML", ".json": "JSON",
    ".md": "Markdown", ".mdx": "MDX", ".r": "R", ".R": "R",
    ".ipynb": "Jupyter Notebook", ".vue": "Vue", ".svelte": "Svelte",
    ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".hrl": "Erlang",
    ".hs": "Haskell", ".lhs": "Haskell", ".scala": "Scala",
    ".clj": "Clojure", ".cljs": "Clojure", ".pl": "Perl",
    ".tf": "HCL", ".toml": "TOML", ".ini": "INI",
}
SPECIAL_FILES = {
    "dockerfile": "Dockerfile", "makefile": "Makefile",
    "cmakelists.txt": "CMake", "gemfile": "Ruby",
    "rakefile": "Ruby", "justfile": "Makefile",
}

def api_get(url: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-all-branch-analytics",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API request failed ({exc.code}) for {url}: {body[:500]}") from exc

def list_public_repositories():
    repositories = []
    page = 1
    while True:
        url = f"{API}/users/{urllib.parse.quote(OWNER)}/repos?type=owner&per_page=100&page={page}&sort=updated"
        batch = api_get(url)
        if not batch:
            break
        repositories.extend(repo for repo in batch if not repo.get("private", True))
        if len(batch) < 100:
            break
        page += 1
    return repositories

def run_git(args, cwd=None, capture=True):
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
        text=True,
    )
    return result.stdout.strip() if capture else ""

def language_for(path: str):
    parts = path.replace("\\", "/").split("/")
    if any(part.lower() in SKIP_DIRS for part in parts[:-1]):
        return None
    filename = parts[-1]
    lower = filename.lower()
    if lower in SKIP_FILES or lower.endswith(SKIP_SUFFIXES):
        return None
    if lower in SPECIAL_FILES:
        return SPECIAL_FILES[lower]
    suffix = Path(lower).suffix
    return EXTENSIONS.get(suffix)

def scan_repository(repo, workdir, all_commit_shas, language_bytes):
    full_name = repo["full_name"]
    clone_dir = Path(workdir) / (str(repo["id"]) + ".git")
    clone_url = repo["clone_url"]
    print(f"Scanning {full_name}", flush=True)
    run_git(["clone", "--mirror", "--quiet", clone_url, str(clone_dir)])
    refs = run_git([
        "for-each-ref", "--format=%(refname)", "refs/heads"
    ], cwd=clone_dir)
    branches = [line.strip() for line in refs.splitlines() if line.strip()]
    if not branches:
        return 0, 0

    commits = run_git(["rev-list", "--branches"], cwd=clone_dir)
    commit_set = set(commits.splitlines()) if commits else set()
    all_commit_shas.update(commit_set)

    for branch_ref in branches:
        tree = run_git(["ls-tree", "-rl", "--full-tree", branch_ref], cwd=clone_dir)
        for line in tree.splitlines():
            try:
                metadata, file_path = line.split("\t", 1)
                fields = metadata.split()
                if len(fields) < 4 or fields[1] != "blob":
                    continue
                size = int(fields[3])
            except (ValueError, IndexError):
                continue
            language = language_for(file_path)
            if language and size > 0:
                language_bytes[language] += size

    return len(branches), len(commit_set)

def esc(value):
    return html.escape(str(value), quote=True)

def write_stats(repo_count, branch_count, commit_count, stars, fork_count):
    width, height = 520, 210
    metrics = [
        ("UNIQUE COMMITS", f"{commit_count:,}"),
        ("PUBLIC REPOSITORIES", f"{repo_count:,}"),
        ("BRANCHES SCANNED", f"{branch_count:,}"),
        ("STARS / FORKS", f"{stars:,} / {fork_count:,}"),
    ]
    positions = [(28, 82), (280, 82), (28, 145), (280, 145)]
    groups = []
    for (label, value), (x, y) in zip(metrics, positions):
        groups.append(
            f'<g><text x="{x}" y="{y}" class="label">{esc(label)}</text>'
            f'<text x="{x}" y="{y + 29}" class="value">{esc(value)}</text></g>'
        )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">GitHub All-Branch Statistics</title>
<desc id="desc">{repo_count} public repositories, {branch_count} branches and {commit_count} unique commits scanned.</desc>
<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#161B22"/><stop offset="1" stop-color="#0D1117"/></linearGradient></defs>
<rect x="1" y="1" width="{width-2}" height="{height-2}" rx="12" fill="url(#bg)" stroke="#30363D"/>
<text x="28" y="35" class="heading">GitHub All-Branch Analytics</text>
<text x="28" y="54" class="sub">All public repositories · every branch · unique commit SHAs</text>
{"".join(groups)}
<style>
.heading{{font:600 18px 'Segoe UI',Ubuntu,sans-serif;fill:#E6A8AF}}
.sub{{font:11px 'Segoe UI',Ubuntu,sans-serif;fill:#8B949E}}
.label{{font:600 10px 'Segoe UI',Ubuntu,sans-serif;fill:#8B949E;letter-spacing:.6px}}
.value{{font:700 22px 'Segoe UI',Ubuntu,sans-serif;fill:#C9D1D9}}
</style></svg>'''
    (ROOT / "profile" / "stats.svg").write_text(svg, encoding="utf-8")

def write_languages(language_bytes):
    total = sum(language_bytes.values())
    ranked = sorted(language_bytes.items(), key=lambda item: item[1], reverse=True)[:8]
    width, row_height, height = 380, 29, 92 + max(1, len(ranked)) * 29
    colors = ["#E34C26", "#3572A5", "#F1E05A", "#563D7C", "#DA5B0B", "#00ADD8", "#A97BFF", "#89E051"]
    rows = []
    if not ranked or total == 0:
        rows.append('<text x="24" y="112" class="empty">No source-language files detected.</text>')
    else:
        for index, (name, size) in enumerate(ranked):
            y = 91 + index * row_height
            pct = size * 100 / total
            bar_width = 190 * pct / max((ranked[0][1] * 100 / total), 1)
            color = colors[index % len(colors)]
            rows.append(
                f'<text x="24" y="{y}" class="lang">{esc(name)}</text>'
                f'<rect x="124" y="{y-11}" width="190" height="8" rx="4" fill="#30363D"/>'
                f'<rect x="124" y="{y-11}" width="{bar_width:.2f}" height="8" rx="4" fill="{color}"/>'
                f'<text x="354" y="{y}" class="pct" text-anchor="end">{pct:.1f}%</text>'
            )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">Top Languages Across All Branches</title>
<desc id="desc">Language byte distribution across the latest snapshots of every branch in all public repositories.</desc>
<rect x="1" y="1" width="{width-2}" height="{height-2}" rx="12" fill="#0D1117" stroke="#30363D"/>
<text x="24" y="34" class="heading">Top Languages</text>
<text x="24" y="54" class="sub">All public repositories · all branch snapshots</text>
{"".join(rows)}
<style>
.heading{{font:600 18px 'Segoe UI',Ubuntu,sans-serif;fill:#E6A8AF}}
.sub{{font:11px 'Segoe UI',Ubuntu,sans-serif;fill:#8B949E}}
.lang{{font:12px 'Segoe UI',Ubuntu,sans-serif;fill:#C9D1D9}}
.pct{{font:11px 'Segoe UI',Ubuntu,sans-serif;fill:#8B949E}}
.empty{{font:12px 'Segoe UI',Ubuntu,sans-serif;fill:#8B949E}}
</style></svg>'''
    (ROOT / "profile" / "top-langs.svg").write_text(svg, encoding="utf-8")

def main():
    repositories = list_public_repositories()
    all_commit_shas = set()
    language_bytes = Counter()
    total_branches = 0
    total_stars = 0
    total_forks = 0

    with tempfile.TemporaryDirectory(prefix="profile-analytics-") as workdir:
        for repo in repositories:
            branches, _ = scan_repository(repo, workdir, all_commit_shas, language_bytes)
            total_branches += branches
            total_stars += int(repo.get("stargazers_count", 0))
            total_forks += int(repo.get("forks_count", 0))

    (ROOT / "profile").mkdir(parents=True, exist_ok=True)
    write_stats(len(repositories), total_branches, len(all_commit_shas), total_stars, total_forks)
    write_languages(language_bytes)
    print(
        f"Completed: {len(repositories)} public repositories, {total_branches} branches, "
        f"{len(all_commit_shas)} unique commits, {len(language_bytes)} languages.",
        flush=True,
    )

if __name__ == "__main__":
    main()
