---
name: building-records
description: Find what the City's September 11th archive files under a Lower Manhattan address or building number, and read a cited page from it. Use when someone asks about a specific building, school or block.
---

# Building records

The Department of Environmental Protection filed much of its 2001–2002 paperwork by building. Many
folder labels in the archive are addresses, sometimes with a building identification number (BIN),
block and lot.

## Steps

1. Call `building_lookup` with the address as the user gives it, or with a seven-digit BIN.
2. If no folder matches, try the street name alone (`John Street`) and then a landmark name
   (`Stuyvesant`). Report each attempt.
3. For each folder worth reading, run the `next_calls` entry it returns (`catalog_search` with box,
   folder and `exact: true`) to list that folder's documents alone.
4. Read one or two documents with `portal_get_page_text`. Sampling results may already be listed in
   `readings_lookup`; check with the street name as `location`.
5. Answer with the folder labels as printed, the document count, and any quoted finding with its
   `NYC-WTC_… p.N` citation.

## Rules

- A label match shows where the City filed paper. Say that a document about the building may sit
  in a folder that does not name it.
- Do not say whether the address is inside an exposure zone. Quote the `zone_definitions` the tool
  returns and link the program's map; the program decides. A zone with `who` set applies to that
  group only; say so when quoting it. Where a zone's `definition` is empty, give its `note` and
  official link and say the wording was not captured; do not supply wording of your own.
- Do not ask for or record the user's own address beyond the lookup itself.
