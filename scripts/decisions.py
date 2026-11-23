"""The audit trail of a request: `state/features/<id>/decisions.md`, one line per event, append-only.

It is the request's history (what the page already shows) written to a file that outlives the card, with a short digest of
each artifact at the moments that matter (an approval, a commit), so «which version was approved» can be answered later.
It never decides anything and a failure to write it never stops a request."""
import hashlib
from pathlib import Path

FILE = "decisions.md"
SKIP = frozenset({"worker launched"})                                  # noise: the history keeps it, the trail does not
HASHED = frozenset({"approved", "approved automatically", "committed", "changes requested"})
ARTIFACTS = ("spec.md", "plan.md", "plan-review.md", "report.md", "review.md")


def digest(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return ""


def line(at: str, stage: str, event: str, note: str = "", hashes: dict | None = None) -> str:
    text = f"- {at} · {stage} · {event}" + (f": {' '.join(str(note).split())}" if note else "")
    if hashes:
        text += " [" + ", ".join(f"{k} {v}" for k, v in hashes.items()) + "]"
    return text + "\n"


def append(folder, title: str, entry: dict) -> bool:
    """Add one history entry ({at, event, stage, note}) to the request's trail. False when nothing was written."""
    folder = Path(folder)
    event = entry.get("event", "")
    if not folder.is_dir() or event in SKIP:
        return False
    hashes = {n: h for n in ARTIFACTS if event in HASHED and (h := digest(folder / n))}
    path = folder / FILE
    try:
        head = "" if path.exists() else f"# Decisions: {' '.join(str(title).split())}\n\n"
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(head + line(entry.get("at", ""), entry.get("stage", ""), event, entry.get("note", ""), hashes))
        return True
    except OSError:
        return False
