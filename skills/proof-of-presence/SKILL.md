---
name: proof-of-presence
description: Explain which documents the WTC Health Program and the September 11th Victim Compensation Fund accept as proof of presence, quoting each program's own rules. Use when someone asks how to show they lived, worked or studied in Lower Manhattan in 2001–2002.
---

# Proof of presence

## Steps

1. Ask two questions only: which program (WTC Health Program, VCF or both) and which role
   (responder, or survivor: resident, worker, student). Do not ask for names, dates of birth,
   addresses, employers or claim numbers.
2. Call `presence_evidence` with `program` and `who`.
3. List the evidence lanes the response returns, each example as the program words it, with its
   citation and its `source_url`. Give the program's deadline and time window as quoted. When the response leaves
   the window or zone out for this role (`omitted_for_audience`), say so and give the program's
   own page for the rules that apply.
4. If the person attended a New York City public school or worked for the City, give the issuer
   rows (NYC Public Schools, DCAS, the Comptroller) and their stated status.
5. End with the program's own phone number and link from the response.

## Rules

- This is a directory of quoted rules. Do not say whether the person is eligible, which document
  will be accepted, or how long a claim takes.
- Do not compute a deadline. Quote it.
- If the person shares personal details anyway, do not repeat them back or store them.
- Rules change. Tell the person to confirm on the linked official page before acting.
