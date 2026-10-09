---
name: release-watch
description: Report what the City's September 11th portal catalog shows since a given date and which settlement and Res. 560-A obligations are due next. Use for questions about new releases, the rolling production and upcoming deadlines.
---

# Release watch

## Steps

1. Call `portal_catalog_stats` for the current capture: documents, pages and capture time.
2. Call `portal_changes_since` with the user's date. Report documents observed added, absent or
   changed, and the interval compared.
3. Call `upcoming_dates` with today's date as `as_of`. List dated obligations ahead with days to
   go, and recurring obligations without dates.
4. Call `doi_milestones` for the status observed on each obligation and when it was checked.
5. For anything new, offer `records_manifest` with the added Bates numbers as a reading list.

## Rules

- A comparison needs two accepted captures. If there is only one, say there is nothing earlier to
  compare against.
- "Observed" means seen on a public page when checked. Make no compliance finding.
- Only the dates the settlement and the resolution state are dates. Do not schedule recurring
  meetings on invented days.
