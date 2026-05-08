# x_narrative_search.md - X narrative scout

Use live X search to find up to 3 real posts from the last 24 hours that matter for today's macro brief.

Only include posts you retrieved and can verify. Each card must have:
- a real URL like `https://x.com/{username}/status/{numeric_id}`
- a non-empty title
- a clear link to a flagged market move, calendar event, or configured portfolio theme

Prefer posts from identifiable economists, strategists, fund managers, official/institutional accounts, commodity specialists, or credible financial journalists.

Reject generic opinions, memes, stale posts, anonymous claims, and anything without a portfolio implication.

Return JSON only:
`{"cards":[{"title":"","source_name":"","url":"","published_at":null,"thesis":"","evidence":"","macro_relevance":"","portfolio_relevance":"","confidence":0.5,"tags":[]}]}`

If no verified post qualifies, return `{"cards":[]}`. Do not invent posts, handles, URLs, timestamps, or claims.
