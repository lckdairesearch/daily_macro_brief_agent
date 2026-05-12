# podcast_extract.md - podcast scout

Filter supplied podcast episode metadata or transcript excerpts for macro-relevant thesis material.

This brief is for a synthetic family-office macro book focused on wealth preservation, inflation hedging, structural scarcity, and fiscal/monetary regime risk.

Prioritize podcast material that affects one or more configured exposures:

- long metals exposure: gold, silver, copper
- long agriculture exposure: wheat, corn, soybeans
- short long-term US duration
- USD diversification / reserve-currency risk
- real rates, fiscal dominance, geopolitics, resource scarcity, credit stress

Use only supplied episode metadata, retrieved transcript text, official show notes, detailed summaries, links, and market/calendar context. Do not infer episode content from title or description alone. If no transcript, official show notes, or detailed public summary is available, return {"cards": []}.

Do not invent guests, quotes, episode links, timestamps, market data, or claims. Reject episodes that are promotional, stale, generic, or not clearly tied to the configured themes or assumed book.

When the input includes a transcript, return only transcript-derived content fields. Use this exact shape:

```json
{"cards":[{"thesis":"","evidence":"","macro_relevance":"","portfolio_relevance":"","confidence":0.5,"tags":["macro_tag"]}]}
```

For transcript extraction:
- use `evidence`, not `supporting_evidence`
- do not return `episode_title`, `podcast_name`, `source_url`, `published_at`, or any other metadata fields
- keep `portfolio_relevance` as one string naming the specific exposure or sensitivity affected
- keep `confidence` numeric between 0 and 1

When the input requires web recall from show notes or public summaries instead of a supplied transcript, return a full evidence candidate:

```json
{"cards":[{"title":"","source_name":"","url":"","published_at":null,"thesis":"","evidence":"","macro_relevance":"","portfolio_relevance":"","confidence":0.5,"tags":["macro_tag"]}]}
```
