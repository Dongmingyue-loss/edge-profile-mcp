# Anti-automation behaviour and how this project handles it

All statements below were measured on 2026-11-08 from this machine, through the
same Playwright + real-Edge stack the server uses, with an isolated profile and
`navigator.webdriver === true` (Playwright cannot hide this reliably, and this
project does not try to). Nothing here is inferred from blog posts.

Reproduce with `scripts/probe_engines.py` (see the bottom of this file).

## Summary

| Engine | Mechanism observed | Solvable here? | Handling |
| --- | --- | --- | --- |
| Bing | **Intermittent** query truncation on bare `/search?q=` requests | **Partly** — form submission raises the success rate, no guarantee | Primary path, result set relevance-checked |
| Bing RSS | Degrades to single-character matching | No, but detectable | Relevance filter, then discard |
| So.com (360) | None observed; plain URL works from a cold profile | **Yes** | Second path |
| Baidu | Redirect to `wappass.baidu.com` image-rotation slider | **No** — image CAPTCHA | Not attempted; reported as blocked |
| Yandex | Redirect to `yandex.com/showcaptcha`, SmartCaptcha | **No** — interactive CAPTCHA | Not attempted; reported as blocked |
| Sogou | `/antispider/` click-the-characters CAPTCHA under load | **No** — click CAPTCHA | Tried, reported as blocked |
| DuckDuckGo (html/lite) | `202` with an explicit "bots use DuckDuckGo too" page | **No** — challenge page | Not used |
| Brave | `429` plus challenge markers | No — rate limited | Not used |
| Startpage | `200` but lands on `error-pages/blocked` | No — blocked | Not used |

The design position: use what answers normally (form submission, plain URLs,
correct request shape), prefer an engine that does not challenge at all, and
**report the rest honestly** rather than fabricating results out of footer links.
No CAPTCHA is solved, no verification credential is forged or transplanted, and
no fingerprint is spoofed.

## Bing

### What was observed

A direct navigation to

```
https://cn.bing.com/search?q=大学生健康教育指南
```

can return HTTP 200 with 10 organic result containers (`#b_results li.b_algo`)
where every result is about the single character **大**:

```
大（汉语文字）_百度百科          https://baike.baidu.com/item/%E5%A4%A7/34128
大 dà、dài、tài - 汉典          https://www.zdic.net/hans/%E5%A4%A7
大的意思,大的解释...             https://www.hanyuguoxue.com/zidian/zi-22823
```

When this happens the query has been truncated to its first character. It is a
silent degradation: status 200, a full-looking result page, and a plausible page
title (`大学生健康教育指南 - 搜索`). A scraper that only checks HTTP 200 and a
non-empty result list will return garbage here.

### It is intermittent, not a rule

An earlier version of this document described the truncation as deterministic.
Repeated measurement did not support that, so the claim is corrected here.

Observed across many fresh profiles on the same day:

| Trial | Request shape | Result |
| --- | --- | --- |
| First diagnosis | bare URL | truncated (all results about 大) |
| Later retest, several fresh profiles | bare URL | correct (9–10 of 10 relevant) |
| Consecutive queries in one fresh profile | bare URL | query 1 truncated, queries 2–3 correct |
| Form submission instead of a URL | form | correct |

So the trigger correlates with session state and request history rather than with
the URL shape alone: a brand-new profile's first request is the most likely to be
degraded, and every subsequent request in that session was fine. Adding form
parameters (`&form=QBLH`, `&FORM=QBRE`, `&qs=n&form=QBRE`) made no difference in
those trials — the earlier "it's the missing form token" theory was not
confirmed.

**Practical consequence:** no client-side change makes Bing guaranteeable. What
this project does is (a) submit the home-page form, which raises the success
rate, and (b) **never trust the result set** — a relevance check decides whether
the answer is usable, and an unusable answer is treated as a miss so the next
engine is tried.

### Extraction detail that is deterministic

`innerText` returns an empty string for anchors inside Bing's result containers,
while `textContent` returns the real title. The selector therefore reads
`textContent` with `innerText` only as a fallback:

```javascript
li.querySelector('h2 a').textContent
```

This one *is* reliable: a naive `a[href]` + `innerText` scrape of Bing yields
blank titles or unrelated footer anchors regardless of whether the query was
truncated.

## So.com (360)

### What was observed

A plain URL works, including from a completely fresh profile:

```
https://www.so.com/s?q=流感疫苗 接种
→ 200, title 流感疫苗 接种_360搜索
→ 27 anchors carrying data-mdurl, 14 with usable titles
```

No challenge page, no redirect to a verification host, no cookie gate. Among the
returned targets were `xinmi.gov.cn` and `zznews.gov.cn` — real government pages.

### Extraction detail

So.com wraps every outbound link in `https://www.so.com/link?m=...`, but each
wrapped anchor carries the true destination in a `data-mdurl` attribute, so no
redirect needs to be followed (following it would be slow and would look exactly
like the click-tracking the site expects for its own analytics).

Selecting `li.res-list` is a trap: those containers hold only 7 items, mostly
aggregate cards for news, images and wenku, while the organic results sit in
other blocks. Extraction is therefore driven by `a[data-mdurl]` and filtered:

- keep only `http(s)` targets,
- drop `so.com` / `360.cn` internal destinations,
- require a title of at least 8 characters.

With `trusted_only=True` this typically yields a small number of government or
academic pages; with `trusted_only=False` it yields around ten mixed results.

## DuckDuckGo

Both `html.duckduckgo.com/html/` and `lite.duckduckgo.com/lite/` answer **202**
with a page whose body reads:

> Unfortunately, bots use DuckDuckGo too.
> Please complete the following challenge to confirm this search was made by a human.

That is an explicit refusal, not a degraded result page. No result containers are
present. Not used.

## Bing RSS

`https://cn.bing.com/search?format=rss&count=20&q=<query>` needs no browsing
context and returns valid RSS with 10 items — but the items can again be about the
truncated leading character:

```
大（汉语文字）_百度百科 | https://baike.baidu.com/item/%E5%A4%A7/34128
```

There is no form to submit here, so the degradation cannot be avoided. The
endpoint is kept only as a last-resort transport and its output is passed through
a relevance filter (`parse_rss_items` keeps a row only if its title or
description mentions a query term of two or more characters). With that filter
the endpoint correctly yields **0 rows** for a truncated response, instead of
returning 10 irrelevant hits.

## Baidu

### What the project does instead

The search path submits Bing's home-page form rather than building a URL:

```python
page.goto("https://cn.bing.com/", wait_until="domcontentloaded")
page.locator("input[name=q]").first.fill(query)
page.locator("input[name=q]").first.press("Enter")
```

This was 10/10 relevant across three fresh profiles and three queries in one
batch, but later trials showed bare URLs also succeeding, so form submission is
an **improvement, not a guarantee**. It costs one extra navigation.

Because no request shape is trustworthy, the guard is the relevance check:
`search_web` inspects the returned titles, and if nothing mentions the query (or
a two-character window of it) the engine is recorded as *degraded* and the next
engine is tried. A degraded set is never returned to the caller as results.

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

1. **Never infer success from HTTP status.** Bing can return 200 with a
   full-looking page while serving truncated results; DuckDuckGo returns 202 with
   a challenge page. Every engine's output passes a relevance check before it is
   accepted.
2. **Prefer the engine that does not challenge.** So.com answers a plain URL
   reliably, so it is tried before engines that need form submission or that block.
3. **Prefer the site's own interaction path.** Typing into a search box is what a
   browser does; it also happens to be what the anti-abuse logic expects.
4. **Do not solve CAPTCHAs.** Image-rotation, click-order and interactive
   "not a robot" widgets are access controls. Report the block and stop.
5. **Do not harvest or transplant cookies** to skip a challenge. A persistent
   profile lets a human solve it once; that is the only accepted route.
6. **Report honestly.** When every engine is blocked, the result carries
   `blocked_engines` / `degraded_engines` and a `hint`, with `total: 0`. Returning
   page furniture as results is worse than returning nothing.
7. **No stealth tooling.** The project leaves `navigator.webdriver` as Playwright
   sets it. Fingerprint-spoofing arms races are out of scope and add no lasting
   value.
8. **State uncertainty.** Several of these behaviours vary by IP reputation,
   request rate and time of day. Measurements here are a snapshot, not a
   specification, and the guards are designed so that a wrong assumption degrades
   into "no results" rather than into wrong results.

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
