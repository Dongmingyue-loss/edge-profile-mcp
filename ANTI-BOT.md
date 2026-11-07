# Anti-automation behaviour and how this project handles it

All statements below were measured on 2026-11-08 from this machine, through the
same Playwright + real-Edge stack the server uses, with an isolated profile and
`navigator.webdriver === true` (Playwright cannot hide this reliably, and this
project does not try to). Nothing here is inferred from blog posts.

Reproduce with `scripts/probe_engines.py` (see the bottom of this file).

## Summary

| Engine | Mechanism observed | Solvable here? | Handling |
| --- | --- | --- | --- |
| Bing | Query truncation on bare `/search?q=` requests | **Yes**, by submitting the home-page form | Primary path |
| Bing RSS | Degrades to single-character matching | No, but detectable | Relevance filter, then discard |
| Baidu | Redirect to `wappass.baidu.com` image-rotation slider | **No** — image CAPTCHA | Not attempted; reported as blocked |
| Yandex | Redirect to `yandex.com/showcaptcha`, SmartCaptcha | **No** — interactive CAPTCHA | Not attempted; reported as blocked |
| Sogou | `/antispider/` click-the-characters CAPTCHA under load | **No** — click CAPTCHA | Tried, reported as blocked |

The design position: solve what can be solved legitimately (form submission,
correct request shape), and **report the rest honestly** rather than fabricating
results out of footer links.

## Bing

### What was observed

A direct navigation to

```
https://cn.bing.com/search?q=大学生健康教育指南
```

returns HTTP 200 with 10 organic result containers (`#b_results li.b_algo`), but
every result is about the single character **大**:

```
大（汉语文字）_百度百科          https://baike.baidu.com/item/%E5%A4%A7/34128
大 dà、dài、tài - 汉典          https://www.zdic.net/hans/%E5%A4%A7
大的意思,大的解释...             https://www.hanyuguoxue.com/zidian/zi-22823
```

The query was truncated to its first character. This is a silent degradation —
status 200, a full-looking result page, and a plausible page title
(`大学生健康教育指南 - 搜索`). Any scraper that only checks for HTTP 200 and
non-empty results will return garbage here.

### Why it happens

Bing appears to require evidence of a real search interaction. A bare URL with a
`q=` parameter and no preceding browsing context is treated as a low-confidence
query and matched loosely. This is consistent with the observed behaviour: the
same query returns correct results once the session has submitted a search form.

### The fix that works

Navigate to the search home page, type into the engine's own input, press Enter:

```python
page.goto("https://cn.bing.com/", wait_until="domcontentloaded")
page.locator("input[name=q]").first.fill(query)
page.locator("input[name=q]").first.press("Enter")
```

Measured across three independent, freshly-created profiles and three queries
(`大学生健康教育指南`, `高血压 防治 指南`, `流感疫苗 接种 建议`): **10/10 relevant
results every time**, compared with 0/10 via direct URL.

Warm-up visits were tested separately (0, 1 and 2 prior visits to the home page)
and made no difference — the deciding factor is the **form submission**, not
prior browsing history. That means the fix costs one extra navigation, not an
elaborate session-priming routine.

### Extraction detail

`innerText` returns an empty string for anchors inside Bing's result containers,
while `textContent` returns the real title. The selector therefore reads
`textContent` with `innerText` only as a fallback:

```javascript
li.querySelector('h2 a').textContent
```

This is why a naive `a[href]` + `innerText` scrape of Bing yields titles that look
blank or that pick up unrelated footer anchors.

## Bing RSS

`https://cn.bing.com/search?format=rss&count=20&q=<query>` needs no browsing
context and returns valid RSS with 10 items — but the items are again about the
truncated leading character:

```
大（汉语文字）_百度百科 | https://baike.baidu.com/item/%E5%A4%A7/34128
```

There is no form to submit here, so the truncation cannot be avoided. The
endpoint is kept only as a last-resort transport and its output is passed through
a relevance filter (`parse_rss_items` keeps a row only if its title or
description mentions a query term of two or more characters). With that filter
the endpoint correctly yields **0 rows** for a truncated response, instead of
returning 10 irrelevant hits.

## Baidu

### What was observed

```
GET https://www.baidu.com/s?wd=大学生健康教育指南
→ 302 https://wappass.baidu.com/static/captcha/tuxing_v2.html?...
   title: 百度安全验证
   body:  请完成下方验证后继续操作 / 拖动左侧滑块使图片为正
```

Cookies set before the redirect: `BAIDUID`, `BAIDUID_BFESS`, `XFI`.
The interstitial is an **image-rotation slider**: the user must drag a handle so
a rotated picture is upright. It is graded server-side on the resulting angle.

### Why it is not solved here

This is an image-understanding CAPTCHA presented after a risk decision, not a
JavaScript challenge. Solving it requires either a human or a model trained on
that specific widget, and doing so would be circumventing an access control the
site deliberately applies to automated clients. This project does not attempt it.

### Handling

Baidu is not in the engine list. If a future caller adds it, `looks_like_challenge`
recognises `wappass` and `百度安全验证` and reports the engine as blocked.

## Yandex

### What was observed

```
GET https://yandex.com/search/?text=大学生健康教育指南
→ 200 https://yandex.com/showcaptcha?cc=1&form-fb-hint=...
   title: Are you not a robot?
   body:  Please confirm that you are not a robot
          We're sorry, but it looks like requests sent from your device are automated.
          Press to continue / Yandex SmartCaptcha
```

Cookies present after the redirect: `yandexuid`, `yashr`, `_yasc`, `bh`, `i`,
`pi`, and notably **`spravka`**.

### Mechanism

Yandex SmartCaptcha issues a short-lived **`spravka` cookie** once a challenge is
passed. The cookie is what marks the client as verified; it is set only after the
challenge is solved, and it is bound to the client (and, per Yandex's own
documentation, the validation token is single-use with a five-minute TTL).

The `showcaptcha` redirect is the transport: the search URL is replaced by a
challenge URL carrying a `form-fb-hint` fingerprint of the original request.

### Why it is not solved here

The initial SmartCaptcha gate is an interactive "I'm not a robot" widget. A
`spravka` cookie cannot be forged, and it cannot be obtained without solving the
challenge. Reusing a cookie harvested from a real browser session would be
credential sharing for the purpose of bypassing an access control, which this
project does not do.

### A legitimate path that does exist

Because the profile is persistent, a **human** can open the automated Edge window
once, solve the captcha, and the resulting `spravka` cookie stays in the profile.
Subsequent automated searches in that same profile then behave like a normal
client. This is the intended, non-circumventing route, and it is the reason the
profile is persistent rather than ephemeral. It is documented rather than
automated: nothing in the code tries to manufacture a `spravka`.

## Sogou

### What was observed

```
GET https://www.sogou.com/web?query=大学生健康教育指南
→ https://www.sogou.com/antispider/?m=1&antip=web_hd&from=/websearch/...
   body: 此验证码用于确认这些请求是您的正常行为而不是自动程序发出的
         请依次点击【蹿,口,沟,互】
```

An order-sensitive click CAPTCHA over Chinese characters. Not solvable without a
human or a specialised model; the engine is attempted only because it occasionally
returns a real result page, and it is skipped as soon as a challenge is detected.

## Design rules that follow from this

1. **Never infer success from HTTP status.** Bing returns 200 with a full-looking
   page while serving truncated results. Every engine's output passes a relevance
   check before it is accepted.
2. **Prefer the site's own interaction path.** Typing into a search box is what a
   browser does; it also happens to be what the anti-abuse logic expects.
3. **Do not solve CAPTCHAs.** Image-rotation, click-order and interactive
   "not a robot" widgets are access controls. Report the block and stop.
4. **Do not harvest or transplant cookies** to skip a challenge. A persistent
   profile lets a human solve it once; that is the only accepted route.
5. **Report honestly.** When every engine is blocked, the result carries
   `blocked_engines` and a `hint`, with `total: 0`. Returning page furniture as
   results is worse than returning nothing.
6. **No stealth tooling.** The project leaves `navigator.webdriver` as Playwright
   sets it. Fingerprint-spoofing arms races are out of scope and add no lasting
   value.

## Reproducing the measurements

The probe used during development navigates each engine, records status, final
URL, redirect target, result-container counts, challenge markers, cookies and
`navigator.webdriver`. The essential part:

```python
page.goto(engine_url, wait_until="domcontentloaded", timeout=30000)
page.wait_for_timeout(3000)
record = {
    "status": response.status,
    "final_url": page.url,                      # shows the redirect
    "title": page.title(),                      # "百度安全验证", "Are you not a robot?"
    "containers": page.locator("#b_results li.b_algo").count(),
    "cookies": [c["name"] for c in context.cookies()],
    "webdriver": page.evaluate("navigator.webdriver"),
}
```

Key signals to compare: `final_url` changing host indicates an interstitial
redirect; `containers > 0` with irrelevant titles indicates silent degradation;
presence of `spravka` on Yandex indicates a previously solved challenge.
