"""Export committed sources without local configuration or repository history.

Policy JSON is provided outside the delivery tree: {"deny_words": ["..."]}.
Only Python's standard library is used. No application code is executed.
"""
import argparse
import io
import json
from pathlib import Path
import subprocess
import zipfile


def scan_archive(data, words):
    findings = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            content = archive.read(entry)
            for word in words:
                if word.casefold() in entry.filename.casefold():
                    findings.append(entry.filename + " (path)")
                    break
                # Include binary assets and common wide-character encodings.
                if any(word.encode(encoding).lower() in content.lower()
                       for encoding in ("utf-8", "utf-16-le", "utf-16-be")):
                    findings.append(entry.filename + " (content)")
                    break
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    policy = json.loads(args.policy_file.read_text(encoding="utf-8-sig"))
    words = policy.get("deny_words")
    if not isinstance(words, list) or not words or any(
        not isinstance(word, str) or not word.strip() for word in words
    ):
        parser.error("policy must contain a nonempty deny_words string list")
    root = Path(__file__).resolve().parents[1]
    if args.output.exists():
        parser.error("output already exists; choose a new filename")
    state = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root
    )
    if state.strip():
        parser.error("commit tracked changes before exporting")
    data = subprocess.check_output(["git", "archive", "--format=zip", "HEAD"], cwd=root)
    findings = scan_archive(data, words)
    if findings:
        raise SystemExit("Delivery policy failed:\n" + "\n".join(findings))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True
    ).strip()
    print("Exported commit " + revision + " to " + str(args.output.resolve()))


if __name__ == "__main__":
    main()
