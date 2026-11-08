"""Unit tests for the pure parsing helpers. No browser involved."""

from __future__ import annotations

import unittest

from edge_profile_mcp import tools


class DecodingTests(unittest.TestCase):
    def test_utf8_passthrough(self):
        raw = "中文内容".encode("utf-8")
        self.assertEqual(tools.decode_bytes(raw), "中文内容")

    def test_declared_gb_charset(self):
        raw = '<meta charset="gb2312">中文'.encode("gb18030")
        self.assertIn("中文", tools.decode_bytes(raw))

    def test_undeclared_gb_fallback(self):
        raw = "中文内容".encode("gb18030")
        # Not valid UTF-8, so the gb18030 path must catch it.
        self.assertIn("中文", tools.decode_bytes(raw))


class HtmlToTextTests(unittest.TestCase):
    def test_drops_script_and_style(self):
        html = "<div>keep<script>var x=1</script><style>a{}</style></div>"
        text = tools.html_to_text(html)
        self.assertIn("keep", text)
        self.assertNotIn("var x", text)
        self.assertNotIn("a{}", text)

    def test_block_tags_become_line_breaks(self):
        text = tools.html_to_text("<p>one</p><p>two</p>")
        self.assertEqual(text.splitlines(), ["one", "two"])

    def test_entities_and_fullwidth_space(self):
        text = tools.html_to_text("<p>a&nbsp;b&amp;c</p><p>前\u3000后</p>")
        self.assertIn("a b&c", text)
        self.assertIn("前 后", text)

    def test_collapses_runs_of_whitespace(self):
        text = tools.html_to_text("<p>a    b\t\tc</p>")
        self.assertEqual(text, "a b c")

    def test_prefers_main_content_container(self):
        filler = "<p>" + "noise " * 100 + "</p>"
        body = "article body " * 50
        html = f"<div>before</div>{filler}<article>{body}</article>"
        text = tools.html_to_text(html)
        self.assertIn("article body", text)
        self.assertNotIn("before", text)

    def test_page_title(self):
        self.assertEqual(tools.page_title("<title>  a  b </title>"), "a b")
        self.assertEqual(tools.page_title("<html></html>"), "")


class LinkTests(unittest.TestCase):
    def test_extract_links_strips_inner_tags(self):
        links = tools.extract_links('<a href="/x"><b>Go</b> <i>there</i></a>')
        self.assertEqual(links, [{"href": "/x", "text": "Go there"}])

    def test_extract_links_missing_href_ignored(self):
        self.assertEqual(tools.extract_links("<a name=x>nope</a>"), [])


class SlugTests(unittest.TestCase):
    def test_stable_and_safe(self):
        first = tools.slugify_url("https://example.com/a/b?c=1")
        self.assertEqual(first, tools.slugify_url("https://example.com/a/b?c=1"))
        self.assertNotIn("/", first)
        self.assertNotIn("?", first)
        self.assertNotIn(":", first)

    def test_distinct_urls_differ(self):
        a = tools.slugify_url("https://example.com/a")
        b = tools.slugify_url("https://example.com/b")
        self.assertNotEqual(a, b)

    def test_root_url_still_named(self):
        # A trailing-slash URL yields the host as the readable part, not "page_".
        slug = tools.slugify_url("https://example.com/")
        self.assertTrue(slug.startswith("example.com_"), slug)
        self.assertNotIn("/", slug)


class TrustedDomainTests(unittest.TestCase):
    def test_accepts_gov_and_academic(self):
        for url in ("https://www.nhc.gov.cn/a",
                    "https://www.moe.gov.cn/b",
                    "https://x.edu.cn/c",
                    "https://www.chinacdc.cn/d",
                    "https://www.who.int/e"):
            with self.subTest(url=url):
                self.assertTrue(tools.is_trusted(url))

    def test_rejects_other_domains(self):
        for url in ("https://example.com/a",
                    "https://baike.baidu.com/item/x",
                    "https://gov.cn.evil.test/x"):
            with self.subTest(url=url):
                self.assertFalse(tools.is_trusted(url))

    def test_rejects_malformed(self):
        self.assertFalse(tools.is_trusted("not a url"))
        self.assertFalse(tools.is_trusted(""))


class So360ParsingTests(unittest.TestCase):
    """360 wraps outbound links; the real target lives in data-mdurl."""

    def test_prefers_data_mdurl_over_redirect(self):
        rows = [
            {"realUrl": "https://www.163.com/dy/article/ABC.html",
             "href": "https://www.so.com/link?m=xyz",
             "title": "50岁及以上成年人有了新型流感疫苗,你该接种吗?", "snippet": ""},
        ]
        got = tools.parse_so360_containers(rows, limit=10, trusted_only=False)
        self.assertEqual(got[0]["url"], "https://www.163.com/dy/article/ABC.html")

    def test_drops_internal_so_com_destinations(self):
        rows = [
            {"realUrl": "", "href": "https://wenku.so.com/s?q=x", "title": "流感疫苗 接种 - 360文库"},
            {"realUrl": "", "href": "https://image.so.com/i?q=x", "title": "流感疫苗 接种_360图片"},
            {"realUrl": "https://hao.360.com/?src=x", "href": "", "title": "360导航首页入口"},
            {"realUrl": "https://www.mfk.com/ask/1.shtml", "href": "",
             "title": "流感疫苗接种注意事项"},
        ]
        got = tools.parse_so360_containers(rows, limit=10, trusted_only=False)
        self.assertEqual([r["url"] for r in got], ["https://www.mfk.com/ask/1.shtml"])

    def test_trusted_filter_keeps_only_authoritative(self):
        rows = [
            {"realUrl": "https://www.xinmi.gov.cn/xxbd/1.jhtml", "title": "免费流感疫苗接种点"},
            {"realUrl": "https://www.163.com/dy/article/1.html", "title": "流感疫苗接种要点"},
        ]
        got = tools.parse_so360_containers(rows, limit=10, trusted_only=True)
        self.assertEqual([r["url"] for r in got], ["https://www.xinmi.gov.cn/xxbd/1.jhtml"])

    def test_drops_short_titles_and_duplicates(self):
        rows = [
            {"realUrl": "https://a.gov.cn/x/y", "title": "短标题"},
            {"realUrl": "https://a.gov.cn/x/y", "title": "这是一个足够长的标题内容"},
            {"realUrl": "https://a.gov.cn/x/y", "title": "这是一个足够长的标题内容"},
        ]
        got = tools.parse_so360_containers(rows, limit=10, trusted_only=False)
        self.assertEqual(len(got), 1)

    def test_search_url_builder(self):
        url = tools.search_url("so360", "流感 疫苗")
        self.assertTrue(url.startswith("https://www.so.com/s?q="))
        self.assertIn("%E6%B5%81", url)

    def test_plans_order_and_modes(self):
        plans = tools.search_plans()
        engines = [p["engine"] for p in plans]
        self.assertEqual(engines[0], "bing")
        self.assertIn("so360", engines)
        modes = {p["engine"]: p["mode"] for p in plans}
        self.assertEqual(modes["so360"], "url")
        self.assertEqual(modes["bing"], "form")


class ChallengeDetectionTests(unittest.TestCase):
    def test_baidu_wappass(self):
        self.assertTrue(tools.looks_like_challenge(
            "https://wappass.baidu.com/static/captcha/tuxing_v2.html",
            "百度安全验证", "请完成下方验证后继续操作"))

    def test_yandex_showcaptcha(self):
        self.assertTrue(tools.looks_like_challenge(
            "https://yandex.com/showcaptcha?cc=1",
            "Are you not a robot?", "Press to continue"))

    def test_sogou_antispider(self):
        self.assertTrue(tools.looks_like_challenge(
            "https://www.sogou.com/antispider/?m=1", "搜狗搜索",
            "此验证码用于确认这些请求是您的正常行为"))

    def test_real_result_page_is_not_a_challenge(self):
        self.assertFalse(tools.looks_like_challenge(
            "https://cn.bing.com/search?q=x", "x - 搜索",
            "搜索结果 第一条 第二条"))

    def test_duckduckgo_bot_page(self):
        self.assertTrue(tools.looks_like_challenge(
            "https://html.duckduckgo.com/html/", "DuckDuckGo",
            "Unfortunately, bots use DuckDuckGo too. Please complete the following challenge"))

    def test_so_com_normal_page_is_not_a_challenge(self):
        self.assertFalse(tools.looks_like_challenge(
            "https://www.so.com/s?q=x", "x_360搜索",
            "搜索 网页 资讯 结果列表"))


class RelevanceTests(unittest.TestCase):
    def test_terms_drop_single_characters(self):
        self.assertEqual(tools.query_terms("a 流感 疫苗"), ["流感", "疫苗"])

    def test_is_relevant(self):
        self.assertTrue(tools.is_relevant("中国流感疫苗预防接种技术指南", "流感 疫苗"))
        self.assertFalse(tools.is_relevant("大（汉语文字）_百度百科", "大学生健康教育指南"))

    def test_merge_cjk_terms(self):
        terms = tools.merge_cjk_terms("大学生健康教育指南")
        self.assertTrue(terms)
        self.assertTrue(all(len(t) == 2 for t in terms))
        self.assertEqual(tools.merge_cjk_terms("has spaces here"), [])


class SearchResultParsingTests(unittest.TestCase):
    def test_drops_footer_and_shallow_paths(self):
        anchors = [
            {"href": "https://www.moe.gov.cn/srcsite/A17/x.html",
             "text": "普通高等学校健康教育指导纲要"},
            {"href": "https://www.miibeian.gov.cn/", "text": "京ICP证050897号备案信息"},
            {"href": "https://example.com/a/b/c", "text": "无关内容也不要的结果标题"},
        ]
        rows = tools.parse_search_results(anchors, limit=10, trusted_only=True,
                                          query="健康教育")
        self.assertEqual([r["url"] for r in rows],
                         ["https://www.moe.gov.cn/srcsite/A17/x.html"])

    def test_respects_limit(self):
        anchors = [{"href": f"https://x.gov.cn/a/b/{i}", "text": f"标题 关键字 {i}"}
                   for i in range(10)]
        rows = tools.parse_search_results(anchors, limit=3, trusted_only=False,
                                          query="关键字")
        self.assertEqual(len(rows), 3)

    def test_bing_containers_mapping(self):
        rows = tools.parse_bing_containers([
            {"href": "https://www.nhc.gov.cn/a/b/c", "title": "指南",
             "snippet": "摘要"},
            {"href": "", "title": "no href", "snippet": ""},
        ], limit=10, trusted_only=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://www.nhc.gov.cn/a/b/c")


class RssParsingTests(unittest.TestCase):
    RSS = """<?xml version="1.0"?><rss><channel>
      <item><title>流感疫苗技术指南</title><link>https://www.chinacdc.cn/a/b</link>
        <description>接种建议</description></item>
      <item><title>大（汉语文字）</title><link>https://baike.baidu.com/item/x</link>
        <description>汉字</description></item>
    </channel></rss>"""

    def test_filters_irrelevant_and_untrusted(self):
        rows = tools.parse_rss_items(self.RSS, "流感疫苗")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://www.chinacdc.cn/a/b")

    def test_truncated_response_yields_nothing(self):
        # A single-character degradation must not produce results.
        rows = tools.parse_rss_items(self.RSS, "大学生健康教育指南")
        self.assertEqual(rows, [])

    def test_untrusted_when_filter_disabled(self):
        rows = tools.parse_rss_items(self.RSS, "汉字", trusted_only=False)
        self.assertEqual(len(rows), 1)

    def test_malformed_xml(self):
        self.assertEqual(tools.parse_rss_items("<not xml", "q"), [])


class CacheTests(unittest.TestCase):
    def test_write_and_append(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = tools.write_cached_text(tmp, "https://e.com/a", "T", "body text")
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("URL: https://e.com/a", content)
            self.assertIn("body text", content)

            target = tools.append_jsonl(tmp, "log.jsonl", [{"a": 1}, {"b": 2}])
            self.assertEqual(len(target.read_text(encoding="utf-8").splitlines()), 2)

    def test_cache_dir_env_override(self):
        import os
        old = os.environ.get("EDGE_AUTOMATION_CACHE_DIR")
        try:
            os.environ["EDGE_AUTOMATION_CACHE_DIR"] = r"E:\some\cache"
            self.assertTrue(tools.default_cache_dir().endswith("cache"))
        finally:
            if old is None:
                os.environ.pop("EDGE_AUTOMATION_CACHE_DIR", None)
            else:
                os.environ["EDGE_AUTOMATION_CACHE_DIR"] = old


if __name__ == "__main__":
    unittest.main(verbosity=2)
