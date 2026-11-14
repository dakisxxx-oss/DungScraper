# Hard-site check

A row counts when the markdown has at least 20 words and is not a short bot interstitial. `robots.txt` refusals and missing official-API credentials are not counted as useful pages.

## Robustness pass

| Result | Before | After |
| --- | ---: | ---: |
| Useful page | 14/20 | 15/20 |
| robots.txt disallow | 3 | 3 |
| Challenge page left | 3 | 2 |

Stack Overflow's `yield` question went from a 4-word "Just a moment" page to about 16,000 words. AP News and Medium stayed on a challenge page. Reuters and Reddit were robots.txt refusals.

## API adapters

Same 20 URLs after the adapter round (`benchmark/hard-round3.json`), compared with the robustness result above.

| Result | Before this round | After |
| --- | ---: | ---: |
| Useful page | 15/20 | 15/20 |
| robots.txt disallow | 1 (Reuters) | 1 (Reuters) |
| Reddit, no API credentials | 2 robots refusals | 2 `reddit_not_configured` |
| Challenge page left | 2 | 2 |

Wikipedia, GitHub, Hacker News, and arXiv succeeded via their public APIs. Reddit HTML was not fetched. AP News and Medium still end on a security check. Overlay stripping can drop a leading Stack Overflow signup or CNN feedback block when another heading follows; those pages were already successes, so the 15/20 count does not move.

Re-run with `python benchmark/hard.py round3`.
