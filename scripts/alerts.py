"""Things that need the user and would otherwise sit unnoticed: a worker stopped on a permission prompt, a request whose worker failed.
An alert shows only after it has lasted `after_s` (a prompt answered at once, or a worker still starting, is not worth a banner)."""
import time

AFTER_S = 180


class Alerts:
    def __init__(self, after_s: int = AFTER_S):
        self.after_s, self.seen, self.told = after_s, {}, set()

    @staticmethod
    def items(workers: list, features: list) -> list:
        """The candidate alerts of this moment: (key, kind, project, title, detail)."""
        out = []
        for w in workers:
            if w.get("state") == "blocked":
                out.append((f"blocked:{w.get('id')}", "blocked", w.get("project", ""), w.get("name") or w.get("label") or str(w.get("id")),
                            w.get("waiting_for") or "waiting for a permission or an answer"))
        for f in features:
            if f.get("status") == "failed":
                out.append((f"failed:{f.get('id')}", "failed", f.get("project", ""), f.get("title", ""), "the worker stopped without the result"))
        return out

    def update(self, candidates: list, now: float | None = None) -> tuple:
        """(alerts to show, the ones that just crossed the threshold). An alert that goes away is forgotten, so it can come back."""
        now = time.time() if now is None else now
        keys = {c[0] for c in candidates}
        self.seen = {k: v for k, v in self.seen.items() if k in keys}
        self.told &= keys
        shown, fresh = [], []
        for key, kind, project, title, detail in candidates:
            since = self.seen.setdefault(key, now)
            if now - since < self.after_s:
                continue
            a = {"key": key, "kind": kind, "project": project, "title": title, "detail": detail, "for_s": round(now - since)}
            shown.append(a)
            if key not in self.told:
                self.told.add(key)
                fresh.append(a)
        return shown, fresh
