---
name: verify-a-quote
description: Check that a quotation, number or date from the September 11th City records appears on the cited Bates page, then format the citation. Use before repeating any quote from the archive.
---

# Verify a quote

Use this before a quote from the City's September 11th portal goes into an answer, an article or a
filing.

## Steps

1. If the user gives a Bates number and page, call `portal_get_page_text` with both. If they give
   only a document, call `portal_get_document` first for the page count.
2. Call `citations_verify` with one claim per quote: the quote exactly as the user has it and the
   source `{"type": "portal", "bates": ..., "page": ...}`. Use an ellipsis (`...`) only where words
   are omitted.
3. For each `found` result, call `citations_format` for the same page and give the user the short
   form inline and the full form in a reference list.
4. For `not-found`, read the page text and show the closest passage verbatim. Say plainly that the
   quote as given is not on that page.

## Rules

- Quote only text that `portal_get_page_text` returned. Never correct the archive's spelling or OCR.
- `unverifiable` means the page text is not captured on this machine. Say so and give the PDF link.
- If page text is withheld for personal information, do not try to reconstruct it from elsewhere.
- One page per citation. A quote that spans pages needs two citations.
