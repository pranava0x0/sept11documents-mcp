---
name: statements-and-records
description: Set officials' public statements about Lower Manhattan air after September 11, 2001 beside the City's own records and later reviews, in date order, each cited. Use for questions about what was said and what was known.
---

# Statements and records

Council Resolution 560-A asks the Department of Investigation to compare what mayoral
administrations knew about the toxins with what they told the public. This skill lays the
published sources together. It does not perform that comparison.

## Steps

1. Call `timeline_lookup`, with `topic` (air, schools, liability, cleanup, oversight) if the
   question has one, and `date_from`/`date_to` if it names a period.
2. Present two columns or two lists: `public_statement` rows, and `city_record` plus
   `later_review` rows. Keep each row's date, quote and citation together.
3. For any date with `date_precision` of month or year, give the `date_basis` text. Never narrow a
   month to a day.
4. Where the question is about measured contamination, call `readings_lookup` with
   `include_unreviewed: true` and list the values exactly as printed, with units and citations.
5. To go further than the curated rows, use `catalog_search` and `portal_get_page_text`, and verify
   each new quote with `citations_verify` before using it.

## Rules

- Draw no conclusion about what any official knew or intended. State that the analysis belongs to
  DOI under Res. 560-A.
- Do not convert units or compare a reading with a standard the document does not itself state.
- The readings are the results located so far. Say how many rows there are, and make no estimate
  of levels across Lower Manhattan from them.
