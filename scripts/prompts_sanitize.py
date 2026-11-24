"""The prompt of the vault audit: a read-only worker that compares copies of notes with the facts of their project, or with each other.

The worker never gets the vault: only a throw-away folder with copies (`notes/…`) and the facts (`facts/…`), and it writes one file there."""

SCHEMA_EXAMPLE = """{
  "findings": [
    {"kind": "stale",              // "stale": the note disagrees with the facts · "contradiction": two notes disagree with each other
     "severity": "medium",         // high | medium | low
     "note": "Projetos/Alfa.md",   // exactly as listed below
     "quote": "O repo não tem remoto.",   // text copied verbatim from that note (it must occur in it), at most 200 characters
     "evidence": ["facts/Alfa.json: remote is [\\"https://github.com/o/r.git\\"]"],   // 1 to 5 short sentences: the fact or the other note's line
     "replace": {"old": "O repo não tem remoto.", "new": "O repo tem o remoto origin."},   // optional: an exact substring that occurs ONCE in the note, and its replacement
     "detail": "one sentence on why it matters"}
  ],
  "unverifiable": ["claims you could not check with what you were given"]
}"""


def render(stage, jobs: list, note_lang: str = "English") -> str:
    """`jobs`: [{"mode": "freshness"|"contradictions", "project": name, "notes": [paths], "facts": "facts/<name>.json" or ""}]."""
    lines = []
    for j in jobs:
        notes = ", ".join(j["notes"])
        if j["mode"] == "freshness":
            lines.append(f"- Project «{j['project']}»: compare each of these notes with {j['facts']}: {notes}")
        else:
            lines.append(f"- Project «{j['project']}»: compare these notes with each other and report what contradicts what: {notes}")
    return f"""You audit notes of a personal knowledge vault. You work on COPIES in {stage}: the notes are in {stage}/notes/ (the path under
notes/ is the note's name), the facts of each project (read from its real folder by the Chief) are in {stage}/facts/. You are READ-ONLY:
write nothing but {stage}/findings.json, run no commands that change anything, do not look for the vault or the projects anywhere
else, and do not read or print .env*, keys, tokens or secret files.

What to check:
{chr(10).join(lines)}

A finding is a claim in a note that the facts show to be wrong or out of date, or that another note contradicts. Rules:
1. Evidence or nothing: every finding names what contradicts the claim (a fact from the facts file, or the other note's line). If you
   cannot prove it, do not report it: list it under "unverifiable" instead. A note that is consistent produces NO finding.
2. Quote the note exactly (verbatim, at most 200 characters). Propose a replacement only when it is an exact substring that occurs
   once in the note and the right text follows from the facts; otherwise leave "replace" out.
3. Priority: what would mislead a reader most. At most 15 findings. No style, no wording, no formatting, no praise.
4. Dates and states change: a note written on a given date is not wrong for describing that date; report it only if it presents the old
   state as the current one.
5. Never write passwords, tokens, keys, private IP addresses or e-mail addresses in the file (the whole file is refused if you do).
6. Write the findings in {note_lang}.

Write {stage}/findings.json (valid JSON, no comments) in this shape:
{SCHEMA_EXAMPLE}
If there is nothing to report write {{"findings": [], "unverifiable": []}}. End your last message with [DONE]."""
