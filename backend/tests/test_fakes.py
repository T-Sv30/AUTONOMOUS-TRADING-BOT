import pytest
from fakes import FakeDB
from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError, OperationFailure


@pytest.mark.asyncio
async def test_collections_appear_on_demand_and_insert_stores_a_copy():
    db = FakeDB()
    doc = {"user_id": "u1", "value": 1}
    await db.anything.insert_one(doc)
    doc["value"] = 2
    assert db.anything.docs == [{"user_id": "u1", "value": 1}]
    assert db["anything"] is db.anything


@pytest.mark.asyncio
async def test_query_operators_equality_ne_gte_lte_in_or_exists():
    db = FakeDB()
    for i in range(5):
        await db.items.insert_one({"n": i, "kind": "even" if i % 2 == 0 else "odd", "nested": {"v": i * 10}})
    await db.items.insert_one({"kind": "none"})

    async def ns(query):
        return sorted(doc["n"] for doc in await db.items.find(query).to_list(100) if "n" in doc)

    assert await ns({"kind": "even"}) == [0, 2, 4]
    assert await ns({"kind": {"$ne": "even"}}) == [1, 3]
    assert await ns({"n": {"$gte": 3}}) == [3, 4]
    assert await ns({"n": {"$lte": 1}}) == [0, 1]
    assert await ns({"n": {"$gte": 1, "$lte": 3}}) == [1, 2, 3]
    assert await ns({"n": {"$in": [0, 4, 9]}}) == [0, 4]
    assert await ns({"$or": [{"n": 0}, {"kind": "odd"}]}) == [0, 1, 3]
    assert await ns({"nested.v": 20}) == [2]
    assert await db.items.count_documents({"n": {"$exists": False}}) == 1
    assert await db.items.count_documents({}) == 6


@pytest.mark.asyncio
async def test_unsupported_operator_raises_instead_of_matching_everything():
    db = FakeDB()
    await db.items.insert_one({"n": 1})
    with pytest.raises(NotImplementedError):
        await db.items.find_one({"n": {"$regex": "1"}})


@pytest.mark.asyncio
async def test_find_one_sort_and_cursor_sort_limit():
    db = FakeDB()
    for n, created in [(1, "2026-01-02"), (2, "2026-01-03"), (3, "2026-01-01")]:
        await db.items.insert_one({"n": n, "created_at": created, "group": n % 2})

    newest = await db.items.find_one({}, {"_id": 0}, sort=[("created_at", -1)])
    assert newest["n"] == 2
    oldest = await db.items.find_one({}, sort=[("created_at", 1)])
    assert oldest["n"] == 3
    multi = await db.items.find_one({}, sort=[("group", 1), ("created_at", -1)])
    assert multi["n"] == 2

    rows = await db.items.find({}).sort("created_at", -1).limit(2).to_list(10)
    assert [row["n"] for row in rows] == [2, 1]
    rows = await db.items.find({}).sort("n", 1).to_list(2)
    assert [row["n"] for row in rows] == [1, 2]


@pytest.mark.asyncio
async def test_update_set_inc_upsert_and_results():
    db = FakeDB()
    missing = await db.items.update_one({"key": "a"}, {"$set": {"v": 1}})
    assert missing.matched_count == 0 and db.items.docs == []

    upserted = await db.items.update_one({"key": "a"}, {"$set": {"v": 1}, "$inc": {"count": 2}, "$setOnInsert": {"created": True}}, upsert=True)
    assert upserted.upserted_id is not None
    assert db.items.docs == [{"key": "a", "v": 1, "count": 2, "created": True}]

    updated = await db.items.update_one({"key": "a"}, {"$inc": {"count": 3}, "$setOnInsert": {"created": "ignored"}})
    assert updated.modified_count == 1
    assert db.items.docs[0]["count"] == 5
    assert db.items.docs[0]["created"] is True

    await db.items.insert_one({"key": "b", "active": True})
    await db.items.insert_one({"key": "c", "active": True})
    many = await db.items.update_many({"active": True}, {"$set": {"active": False}})
    assert many.modified_count == 2
    assert await db.items.count_documents({"active": True}) == 0


@pytest.mark.asyncio
async def test_unique_key_raises_duplicate_key_error_on_insert_and_upsert():
    db = FakeDB()
    db.users.add_unique("email")
    db.locks.add_unique(["user_id", "symbol"])
    await db.users.insert_one({"email": "a@example.com"})
    with pytest.raises(DuplicateKeyError):
        await db.users.insert_one({"email": "a@example.com"})
    await db.users.insert_one({"email": "b@example.com"})
    assert len(db.users.docs) == 2

    await db.locks.insert_one({"user_id": "u1", "symbol": "BTC-USD"})
    await db.locks.insert_one({"user_id": "u1", "symbol": "ETH-USD"})
    with pytest.raises(DuplicateKeyError):
        await db.locks.insert_one({"user_id": "u1", "symbol": "BTC-USD", "extra": 1})


@pytest.mark.asyncio
async def test_create_index_is_recorded_and_unique_index_is_enforced():
    db = FakeDB()
    name = await db.users.create_index([("email", 1)], unique=True, name="uniq_email")
    assert name == "uniq_email"
    assert db.users.indexes == [([("email", 1)], {"unique": True, "name": "uniq_email"})]
    await db.users.insert_one({"email": "a@example.com"})
    with pytest.raises(DuplicateKeyError):
        await db.users.insert_one({"email": "a@example.com"})


@pytest.mark.asyncio
async def test_partial_unique_index_only_checks_matching_documents():
    db = FakeDB()
    await db.trades.create_index([("key", 1)], unique=True, partialFilterExpression={"claim": True}, name="uniq_claim")
    await db.trades.insert_one({"key": "k", "claim": False})
    await db.trades.insert_one({"key": "k", "claim": True})
    await db.trades.insert_one({"key": "k"})
    with pytest.raises(DuplicateKeyError):
        await db.trades.insert_one({"key": "k", "claim": True})
    await db.trades.update_one({"key": "k", "claim": True}, {"$set": {"claim": False}})
    await db.trades.insert_one({"key": "k", "claim": True})
    await db.trades.drop_index("uniq_claim")
    assert db.trades.unique_filters == {}


@pytest.mark.asyncio
async def test_find_one_and_update_returns_before_or_after_and_upserts():
    db = FakeDB()
    created = await db.locks.find_one_and_update({"key": "k"}, {"$set": {"v": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    assert created == {"key": "k", "v": 1}
    before = await db.locks.find_one_and_update({"key": "k"}, {"$set": {"v": 2}}, return_document=ReturnDocument.BEFORE)
    assert before["v"] == 1
    assert await db.locks.find_one_and_update({"key": "missing"}, {"$set": {"v": 1}}) is None


@pytest.mark.asyncio
async def test_bulk_write_upserts_and_records_calls():
    db = FakeDB()
    ops = [
        UpdateOne({"k": 1}, {"$set": {"k": 1, "v": "a"}}, upsert=True),
        UpdateOne({"k": 2}, {"$set": {"k": 2, "v": "b"}}, upsert=True),
    ]
    first = await db.candles.bulk_write(ops, ordered=False)
    assert first.upserted_count == 2
    await db.candles.bulk_write([UpdateOne({"k": 1}, {"$set": {"v": "c"}}, upsert=True)], ordered=False)
    assert sorted((doc["k"], doc["v"]) for doc in db.candles.docs) == [(1, "c"), (2, "b")]
    assert [(len(call_ops), ordered) for call_ops, ordered in db.candles.bulk_calls] == [(2, False), (1, False)]

    await db.candles.bulk_write([UpdateOne({"k": 3}, {"$set": {"v": "d"}})])
    assert await db.candles.count_documents({"k": 3}) == 0


@pytest.mark.asyncio
async def test_delete_one_removes_a_single_match():
    db = FakeDB()
    await db.items.insert_one({"k": 1})
    await db.items.insert_one({"k": 1})
    result = await db.items.delete_one({"k": 1})
    assert result.deleted_count == 1
    assert len(db.items.docs) == 1


@pytest.mark.asyncio
async def test_drop_index_removes_the_index_and_missing_names_raise_index_not_found():
    db = FakeDB()
    await db.users.create_index([("email", 1)], unique=True, name="uniq_email")
    await db.users.drop_index("uniq_email")
    assert db.users.indexes == []
    await db.users.insert_one({"email": "a@example.com"})
    await db.users.insert_one({"email": "a@example.com"})  # the unique key went with the index
    with pytest.raises(OperationFailure) as exc:
        await db.users.drop_index("uniq_email")
    assert exc.value.code == 27


@pytest.mark.asyncio
async def test_cursor_skip_pages_after_sort():
    db = FakeDB()
    for n in range(5):
        await db.items.insert_one({"n": n})
    rows = await db.items.find({}).sort("n", 1).skip(2).limit(2).to_list(None)
    assert [row["n"] for row in rows] == [2, 3]
