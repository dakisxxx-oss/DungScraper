# Hard-site check

Same 20 URLs, same rule, before and after the robustness pass.

A row counts when the markdown has at least 20 words and is not a short bot interstitial ("Just a moment", captcha, access denied). `robots.txt` refusals are reported separately. They are the crawler doing what it was asked, not a failed download.

| Result | Before | After |
| --- | ---: | ---: |
| Useful page | 14/20 | 15/20 |
| robots.txt disallow | 3 | 3 |
| Challenge page left | 3 | 2 |

The change is the Stack Overflow question on `yield`. Before: browser, HTTP 403, 4 words, "Just a moment". After: browser, HTTP 403, about 16,000 words, and the answer text is in the markdown. The page still leads with the signup form.

AP News and Medium still end on "Just a moment" after the browser waits up to 8 seconds. Reuters, old Reddit, and Reddit disallow `DungScraperBot` in robots.txt.

Docs, Wikipedia, BBC, the Guardian, GitHub, React, arXiv, Amazon search, and CNN returned a long page on both runs. CNN's markdown opens on a feedback prompt in both runs.

Re-run with `python benchmark/hard.py after`.
