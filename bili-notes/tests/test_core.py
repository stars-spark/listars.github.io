import hashlib
import urllib.parse

import pytest

from bili_notes.bilibili import (
    BiliError,
    Segment,
    VideoInfo,
    mixin_key,
    parse_ref,
    pick_subtitle,
    subtitle_json_to_segments,
    wbi_sign,
)
from bili_notes.pipeline import linkify_timestamps, merge_segments, parse_timestamp, safe_filename
from bili_notes.summarize import format_timestamp


def make_info(page=1):
    return VideoInfo(
        bvid="BV1xx411c7mD", aid=2, cid=3, page=page, title="t", part_title="",
        owner="up", duration=600, desc="",
    )


@pytest.mark.parametrize(
    "text,bvid,aid,page",
    [
        ("BV1xx411c7mD", "BV1xx411c7mD", None, 1),
        ("https://www.bilibili.com/video/BV1xx411c7mD/?p=3&spm_id_from=x", "BV1xx411c7mD", None, 3),
        ("https://m.bilibili.com/video/BV1xx411c7mD", "BV1xx411c7mD", None, 1),
        ("av170001", None, 170001, 1),
        ("https://www.bilibili.com/video/av170001?p=2", None, 170001, 2),
    ],
)
def test_parse_ref(text, bvid, aid, page):
    ref = parse_ref(text)
    assert (ref.bvid, ref.aid, ref.page) == (bvid, aid, page)


def test_parse_ref_rejects_garbage():
    with pytest.raises(BiliError):
        parse_ref("hello world")


def test_wbi_sign_matches_reference_algorithm():
    key = mixin_key("7cd084941338484aae1ad9425b84077c", "4932caff0ff746eab6f01bf08b70ac45")
    assert len(key) == 32
    signed = wbi_sign({"foo": "114", "bar": "514", "zab": "1919810", "q": "a(b)!"}, key, wts=1702204169)
    expected_query = "bar=514&foo=114&q=ab&wts=1702204169&zab=1919810"
    assert urllib.parse.urlencode({k: v for k, v in signed.items() if k != "w_rid"}) == expected_query
    assert signed["w_rid"] == hashlib.md5((expected_query + key).encode()).hexdigest()


def test_pick_subtitle_prefers_human_chinese_then_ai():
    subs = [{"lan": "ai-en"}, {"lan": "ai-zh"}, {"lan": "zh-CN"}]
    assert pick_subtitle(subs)["lan"] == "zh-CN"
    assert pick_subtitle(subs[:2])["lan"] == "ai-zh"


def test_subtitle_json_to_segments_skips_empty():
    body = [{"from": 0, "to": 1.5, "content": " 你好 "}, {"from": 1.5, "to": 2, "content": ""}]
    assert subtitle_json_to_segments(body) == [Segment(0.0, 1.5, "你好")]


def test_merge_segments_groups_by_window():
    segs = [Segment(i * 10, i * 10 + 10, f"s{i}") for i in range(7)]
    assert merge_segments(segs, window=30) == [
        "[00:00] s0 s1 s2",
        "[00:30] s3 s4 s5",
        "[01:00] s6",
    ]


def test_timestamps():
    assert format_timestamp(754) == "12:34"
    assert format_timestamp(3723) == "1:02:03"
    assert parse_timestamp("12:34") == 754
    assert parse_timestamp("1:02:03") == 3723


def test_linkify_timestamps_skips_existing_links():
    md = "观点 [12:34]，已有 [01:00](http://x)"
    out = linkify_timestamps(md, make_info())
    assert "[12:34](https://www.bilibili.com/video/BV1xx411c7mD?t=754)" in out
    assert "[01:00](http://x)" in out
    assert "?p=2&t=60" in linkify_timestamps("[01:00]", make_info(page=2))


def test_safe_filename():
    assert safe_filename('a/b:c*"d"') == "a b c d"
