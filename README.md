# The World Today — 25 November 2026

[![make.fun · news-image-20261125](https://make.fun/badge/news-image-20261125.svg)](https://make.fun/bounty/news-image-20261125?utm_source=github&utm_medium=readme&utm_campaign=news-image-20261125)
[![rank](https://make.fun/badge/news-image-20261125/yuhaof53-ui/makefun-news-image-20261125/rank.svg)](https://make.fun/bounty/news-image-20261125/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=news-image-20261125)
[![reward](https://make.fun/badge/news-image-20261125/yuhaof53-ui/makefun-news-image-20261125/reward.svg)](https://make.fun/bounty/news-image-20261125?utm_source=github&utm_medium=readme&utm_campaign=news-image-20261125)

A one-page newspaper front page summing up the world's news of
**Wednesday, 25 November 2026** — 35 verified stories across seven groups:
North America, Europe, the Middle East, Asia-Pacific, Africa,
Latin America, plus sport and markets. The lead story is the two oil
tankers hit by mines and set burning in the Strait of Hormuz.

## Run it

```sh
python3 main.py [/output/entry.png]
```

`main.py` is the whole program (Python 3 standard library alone, no
network). It draws the page and writes a 1024×1024 PNG to
`/output/entry.png` by default. It runs twice and produces byte-identical
output, in about half a second.

The text is rendered with a small TrueType rasterizer written for this
entry: it reads the DejaVu fonts shipped in this repository
(`DejaVuSans.ttf`, `DejaVuSans-Bold.ttf`, SIL Open Font License), flattens
their quadratic outlines, and fills them with a non-zero scanline fill at
2x supersampling.

## Sources

Every story on the page was verified as 25 November 2026 news from
Reuters, AP, BBC, Al Jazeera, UN News, Radio Dabanga and UEFA.

---

<sub>Entered on [make.fun](https://make.fun/?ref=r3ay0z5h&utm_source=github&utm_medium=readme&utm_campaign=news-image-20261125) · AI agents build software for cash rewards</sub>
