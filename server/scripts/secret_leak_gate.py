from __future__ import annotations

import re
import subprocess
from pathlib import Path


PATTERNS={
    "private_key":re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/]{32,}"),
    "openai_key":re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github_token":re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "jwt":re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\b"),
    "bearer_token":re.compile(r"(?i)authorization[^\n]{0,40}bearer\s+[A-Za-z0-9._~-]{24,}"),
    "assigned_secret":re.compile(r"(?im)^[ \t]*(?:OPENAI_API_KEY|FINNHUB_API_KEY|FRED_API_KEY|BROKER_SECRET_KEY|TRADELOCKER_PASSWORD|AUTH0_CLIENT_SECRET)[ \t]*=[ \t]*\S[^\r\n]{7,}$"),
}
TEXT_SUFFIXES={".py",".ts",".tsx",".js",".json",".md",".txt",".log",".yml",".yaml",".toml",".ini",".example",".env"}


def tracked_files()->list[Path]:
    output=subprocess.run(["git","ls-files","-z"],check=True,capture_output=True).stdout
    return [Path(item.decode(errors="replace")) for item in output.split(b"\0") if item]


def main()->int:
    findings=[]
    candidates=tracked_files()
    bundle=Path("frontend/.next")
    if bundle.exists():candidates.extend(path for path in bundle.rglob("*") if path.is_file() and path.suffix in {".js",".json",".txt"})
    for path in candidates:
        if not path.is_file() or (path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile","Procfile"}):continue
        try:text=path.read_text(encoding="utf-8",errors="ignore")
        except OSError:continue
        for category,pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                value=match.group(0)
                if category=="assigned_secret" and any(marker in value.lower() for marker in ("placeholder","changeme","your-","<", "${", "openssl", "replace")):continue
                findings.append((category,path.as_posix(),text.count("\n",0,match.start())+1))
    ignored=subprocess.run(["git","check-ignore","-q",".env"]).returncode==0
    tracked_env=any(path.as_posix()==".env" for path in tracked_files())
    if not ignored or tracked_env:findings.append(("env_ignore_policy",".gitignore",1))
    history=subprocess.run(["git","log","-p","--all","--no-ext-diff"],check=True,capture_output=True).stdout.decode("utf-8",errors="ignore")
    for category in ("private_key","openai_key","github_token","jwt","bearer_token"):
        if PATTERNS[category].search(history):findings.append((f"git_history_{category}","git-history",0))
    for category,path,line in sorted(set(findings)):print(f"FAIL {category}: {path}:{line}")
    if findings:print(f"RESULT: FAIL ({len(set(findings))} categorized findings)");return 1
    print("PASS tracked files, logs, and frontend bundle contain no recognized secret material")
    print("PASS .env is ignored and untracked")
    print("RESULT: PASS");return 0


if __name__=="__main__":raise SystemExit(main())
