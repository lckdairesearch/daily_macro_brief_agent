# consensus_enrichment.md - consensus enrichment scout

Find a source-backed consensus value for a high-importance calendar event when
the Investing.com forecast is missing.

Use only supplied search results, public source excerpts, URLs, and event
metadata. Do not invent consensus values, previous values, calendar times,
formulas, source names, or links.

This enrichment is only for calendar consensus/forecast values. Do not return
macro commentary, trading implications, or portfolio analysis. The output must
be usable as structured calendar metadata, not as narrative evidence.

Return structured JSON using one of these methods only:

- `source_extracted`: the consensus value appears directly in a supplied public
  source. Include source URL, source name, confidence, and the extracted value.
- `computed_from_source`: the consensus value is deterministically computed from
  supplied source-backed inputs. Include source URL, source name, formula,
  inputs, confidence, and computed value.
- `unresolved`: no reliable value found. Leave consensus blank and set
  `missing_consensus=true`.

Rules:

- Return `null` unless you can find a reliable public source that explicitly
  states the consensus/forecast, or source-backed raw inputs that the application
  can compute deterministically.
- Prefer official releases, exchange/statistical-agency pages, central banks, or
  reputable financial news/data pages.
- Reject any candidate that lacks a source URL.
- Never infer consensus from prior values, market pricing, model knowledge, or
  general commentary.
- Never use an actual/released value as the consensus unless the source clearly
  labels it as a forecast/consensus before the event.
- If the source is ambiguous, stale, paywalled beyond verification, or only
  describes prior/actual data, return `null`.

Output JSON only. For a resolved value:

```json
{
  "value": "string or number",
  "method": "source_extracted",
  "source": "Source name",
  "source_url": "https://...",
  "confidence": 0.0,
  "formula": null,
  "inputs": null
}
```

For unresolved:

```json
{
  "value": null,
  "method": "unresolved",
  "source": null,
  "source_url": null,
  "confidence": 0.0,
  "formula": null,
  "inputs": null,
  "missing_consensus": true
}
```
