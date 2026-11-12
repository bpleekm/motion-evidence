# Project rules

- Keep this independent from motion-detect; consume the versioned evidence files, not its Python modules.
- Original-session files are read-only. All run data, tests, caches and builds use the configured external work root.
- Static sitting, sleeping and static standing permit acceleration. Person presence alone is not a retain/review rule.
- AI reviews editable ranges, reads context, cites supplied frame IDs; the program maps PTS and merges. Locked ranges are immutable.
- Missing data, configuration, model access or inconclusive output is HOLD, never a fabricated successful AI review.
- API keys live in local .env files, ignored by Git. Logs contain metadata and sanitized errors, not keys or image data URLs.
- Commit generic source, schemas, synthetic tests and complete product/architecture/core docs only.
- After checks and staged-content privacy review, commit and push each delivered update using the requested identity. Never change global Git settings.
