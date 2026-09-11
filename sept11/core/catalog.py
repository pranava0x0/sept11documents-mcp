"""Document-level comparison of two portal catalog snapshots.

The settlement promises rolling releases for twelve months and a privilege log
of withheld documents; the portal's own Update History names agencies only.
This comparison is the document-level record.

Vocabulary matters here (spec 08, step 5): a document present in the older
snapshot and absent from the newer one is `observed_absent`. Absence is not
deletion, redaction or withholding, and this module never says it is.
"""
from __future__ import annotations

from collections import Counter

TRACKED = ("page_count", "pdf_size", "box", "folder", "source", "agency", "production_volume", "production_end")


def diff_catalogs(old: list[dict], new: list[dict]) -> dict:
    o = {r["bates"]: r for r in old}
    n = {r["bates"]: r for r in new}
    added = sorted(set(n) - set(o))
    removed = sorted(set(o) - set(n))
    changed = []
    for b in sorted(set(o) & set(n)):
        deltas = {k: (o[b].get(k), n[b].get(k)) for k in TRACKED if o[b].get(k) != n[b].get(k)}
        if deltas:
            changed.append({"bates": b, "changes": deltas})
    pages_added = sum(n[b]["page_count"] or 0 for b in added)
    pages_removed = sum(o[b]["page_count"] or 0 for b in removed)
    return {
        "old_documents": len(o), "new_documents": len(n),
        "added": added, "removed": removed, "changed": changed,
        "pages_added": pages_added, "pages_removed": pages_removed,
        "added_by_source": dict(Counter(n[b]["source"] for b in added)),
        "added_by_box": dict(Counter(n[b]["box"] for b in added)),
        "removed_by_source": dict(Counter(o[b]["source"] for b in removed)),
    }


def render_markdown(report: dict, old_name: str = "old", new_name: str = "new") -> str:
    lines = [f"# Catalog diff: {old_name} -> {new_name}", "",
             f"- documents: {report['old_documents']} -> {report['new_documents']}",
             f"- added: {len(report['added'])} documents ({report['pages_added']} pages)",
             f"- removed: {len(report['removed'])} documents ({report['pages_removed']} pages)",
             f"- changed: {len(report['changed'])} documents", ""]
    if report["added_by_source"]:
        lines.append("## Added by source")
        lines += [f"- {k}: {v}" for k, v in sorted(report["added_by_source"].items(), key=lambda kv: -kv[1])]
        lines.append("")
    if report["added_by_box"]:
        lines.append("## Added by box")
        lines += [f"- {k or '(none)'}: {v}" for k, v in sorted(report["added_by_box"].items(), key=lambda kv: -kv[1])[:40]]
        lines.append("")
    if report["removed"]:
        lines.append("## Removed (a removal is news: check the privilege log)")
        lines += [f"- {b}" for b in report["removed"][:200]]
        lines.append("")
    if report["changed"]:
        lines.append("## Changed")
        for c in report["changed"][:200]:
            desc = "; ".join(f"{k}: {a!r} -> {b!r}" for k, (a, b) in c["changes"].items())
            lines.append(f"- {c['bates']}: {desc}")
        lines.append("")
    if not (report["added"] or report["removed"] or report["changed"]):
        lines.append("No changes.")
    return "\n".join(lines)
