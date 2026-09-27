from bili_notes.store import Store

from conftest import make_done


def test_add_dedupes_and_claims_in_order(store):
    a, new_a = store.add("BVaaaaaaaaaa")
    b, _ = store.add("BVbbbbbbbbbb")
    assert new_a and store.add("BVaaaaaaaaaa") == (a, False)
    assert store.claim_next().id == a
    assert store.claim_next().id == b
    assert store.claim_next() is None


def test_reset_processing_requeues(store):
    vid, _ = store.add("BVaaaaaaaaaa")
    store.claim_next()
    store.reset_processing()
    assert store.get(vid).status == "pending"


def test_deleted_not_readded_by_sync_but_manual_restores(store):
    vid = make_done(store)
    store.delete(vid)
    assert store.add("BV1ThZnYxEZi", source="fav") == (vid, False)
    assert store.get(vid).status == "deleted"
    assert store.add("BV1ThZnYxEZi", source="manual") == (vid, True)
    assert store.get(vid).status == "pending"


def test_requeue_can_drop_transcript(store):
    vid = make_done(store)
    store.requeue(vid, keep_transcript=True)
    assert store.get(vid).transcript
    store.requeue(vid, keep_transcript=False)
    assert store.get(vid).transcript == ""


def test_search_fts_and_short_terms(store):
    vid = make_done(store)
    make_done(store, bvid="BVotherother", note="## 一句话概括\n讲罗马帝国", title="罗马",
              transcript="[00:00] 罗马帝国的衰落")
    assert store.fts
    hits = store.search("一条鞭法")  # >=3 字走 FTS
    assert [h.video.id for h in hits] == [vid]
    assert hits[0].snippets[0] == ("00:30", "一条鞭法把赋役合并 折算成银两征收")
    hits = store.search("明朝 考成")  # 2 字走 LIKE，多个词同时匹配
    assert [h.video.id for h in hits] == [vid]
    assert store.search("明朝 罗马") == []


def test_search_without_fts(settings, monkeypatch):
    s = Store(settings.db_path)
    s.fts = False
    make_done(s)
    assert len(s.search("张居正改革")) == 1


def test_search_only_done_and_note_snippets(store):
    vid = make_done(store, note="## 核心观点\n海瑞罢官的意义 [00:30](https://x)")
    pending, _ = store.add("BVpendingpend")
    hits = store.search("海瑞")
    assert [h.video.id for h in hits] == [vid]
    assert hits[0].snippets == [("", "海瑞罢官的意义 [00:30]")]


def test_kv(store):
    assert store.kv_get("x", "d") == "d"
    store.kv_set("x", "1")
    store.kv_set("x", "2")
    assert store.kv_get("x") == "2"
