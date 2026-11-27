"""Shared in-memory Mongo stand-in for unit tests.

Collections appear on demand (``db.anything``). Each collection keeps its
documents in ``.docs`` so tests can seed or inspect state directly. Only the
query and update operators the services use are supported; an unsupported
operator raises so a test never passes on a silently ignored filter.
"""

from types import SimpleNamespace
from typing import Any

from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError, OperationFailure

_MISSING = object()


def get_path(doc: dict[str, Any], key: str, default: Any = None) -> Any:
    value: Any = doc
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def has_path(doc: dict[str, Any], key: str) -> bool:
    return get_path(doc, key, _MISSING) is not _MISSING


def _compare(op: str, value: Any, operand: Any) -> bool:
    if op == "$ne":
        return value != operand
    if op == "$in":
        return value in operand
    if op == "$nin":
        return value not in operand
    if value is None:
        return False
    if op == "$gte":
        return value >= operand
    if op == "$lte":
        return value <= operand
    if op == "$gt":
        return value > operand
    if op == "$lt":
        return value < operand
    raise NotImplementedError(f"fakes.FakeCollection does not support query operator {op}")


def matches(doc: dict[str, Any], query: dict[str, Any] | None) -> bool:
    for key, cond in (query or {}).items():
        if key == "$or":
            if not any(matches(doc, sub) for sub in cond):
                return False
            continue
        if key == "$and":
            if not all(matches(doc, sub) for sub in cond):
                return False
            continue
        if isinstance(cond, dict) and cond and all(str(op).startswith("$") for op in cond):
            value = get_path(doc, key)
            for op, operand in cond.items():
                if op == "$exists":
                    if has_path(doc, key) != bool(operand):
                        return False
                elif not _compare(op, value, operand):
                    return False
        elif get_path(doc, key) != cond:
            return False
    return True


def _sort_key(value: Any) -> tuple[bool, Any]:
    # Missing and None sort first, like Mongo's ascending order puts null first.
    return (value is not None, value)


def sort_docs(docs: list[dict[str, Any]], sort: Any, direction: int = 1) -> list[dict[str, Any]]:
    if not sort:
        return docs
    keys = [(sort, direction)] if isinstance(sort, str) else list(sort)
    for key, key_direction in reversed(keys):
        docs = sorted(docs, key=lambda doc, k=key: _sort_key(get_path(doc, k)), reverse=key_direction < 0)
    return docs


def apply_update(doc: dict[str, Any], update: dict[str, Any], *, inserted: bool = False) -> None:
    for op in update:
        if op not in {"$set", "$inc", "$setOnInsert", "$unset"}:
            raise NotImplementedError(f"fakes.FakeCollection does not support update operator {op}")
    for key, value in update.get("$set", {}).items():
        doc[key] = value
    for key, value in update.get("$inc", {}).items():
        doc[key] = doc.get(key, 0) + value
    for key in update.get("$unset", {}):
        doc.pop(key, None)
    if inserted:
        for key, value in update.get("$setOnInsert", {}).items():
            doc[key] = value


class FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]):
        self.docs = docs

    def sort(self, key, direction: int = 1):
        self.docs = sort_docs(self.docs, key, direction)
        return self

    def skip(self, count: int):
        self.docs = self.docs[count:]
        return self

    def limit(self, limit: int):
        if limit:
            self.docs = self.docs[:limit]
        return self

    async def to_list(self, limit: int | None = None):
        return self.docs[:limit] if limit else list(self.docs)


class FakeCollection:
    def __init__(self):
        self.docs: list[dict[str, Any]] = []
        self.bulk_calls: list[tuple[list[Any], bool]] = []
        self.indexes: list[tuple[Any, dict[str, Any]]] = []
        self.unique_keys: list[tuple[str, ...]] = []

    def add_unique(self, fields) -> None:
        """Register a unique key; insert_one then raises DuplicateKeyError on a clash."""
        self.unique_keys.append((fields,) if isinstance(fields, str) else tuple(fields))

    def _assert_unique(self, candidate: dict[str, Any], ignore: dict[str, Any] | None = None) -> None:
        for fields in self.unique_keys:
            values = [get_path(candidate, field) for field in fields]
            for existing in self.docs:
                if existing is ignore:
                    continue
                if [get_path(existing, field) for field in fields] == values:
                    raise DuplicateKeyError(f"E11000 duplicate key error: {dict(zip(fields, values, strict=True))}")

    async def create_index(self, keys, **kwargs):
        self.indexes.append((keys, dict(kwargs)))
        if kwargs.get("unique"):
            fields = [keys] if isinstance(keys, str) else [key for key, _ in keys]
            self.add_unique(fields)
        return kwargs.get("name") or "_".join(str(part) for part in ([keys] if isinstance(keys, str) else [k for k, _ in keys]))

    async def drop_index(self, name: str):
        for index, (keys, kwargs) in enumerate(self.indexes):
            if kwargs.get("name") == name:
                del self.indexes[index]
                if kwargs.get("unique"):
                    fields = (keys,) if isinstance(keys, str) else tuple(key for key, _ in keys)
                    if fields in self.unique_keys:
                        self.unique_keys.remove(fields)
                return
        # Mongo answers a drop of a missing index with IndexNotFound (code 27).
        raise OperationFailure(f"index not found with name [{name}]", code=27)

    async def insert_one(self, doc: dict[str, Any]):
        stored = dict(doc)
        self._assert_unique(stored)
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=len(self.docs))

    async def find_one(self, query=None, projection=None, sort=None):
        found = sort_docs([doc for doc in self.docs if matches(doc, query)], sort)
        return dict(found[0]) if found else None

    def find(self, query=None, projection=None):
        return FakeCursor([dict(doc) for doc in self.docs if matches(doc, query)])

    async def count_documents(self, query=None):
        return len([doc for doc in self.docs if matches(doc, query)])

    def _upsert_seed(self, query: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in query.items() if not k.startswith("$") and not isinstance(v, dict)}

    async def update_one(self, query, update, upsert: bool = False):
        doc = next((d for d in self.docs if matches(d, query)), None)
        if doc is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
            doc = self._upsert_seed(query)
            apply_update(doc, update, inserted=True)
            self._assert_unique(doc)
            self.docs.append(doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=len(self.docs))
        before = dict(doc)
        apply_update(doc, update)
        return SimpleNamespace(matched_count=1, modified_count=int(doc != before), upserted_id=None)

    async def update_many(self, query, update, upsert: bool = False):
        matched = [doc for doc in self.docs if matches(doc, query)]
        if not matched and upsert:
            return await self.update_one(query, update, upsert=True)
        modified = 0
        for doc in matched:
            before = dict(doc)
            apply_update(doc, update)
            modified += int(doc != before)
        return SimpleNamespace(matched_count=len(matched), modified_count=modified, upserted_id=None)

    async def find_one_and_update(self, query, update, upsert: bool = False, return_document=False, projection=None, sort=None):
        found = sort_docs([doc for doc in self.docs if matches(doc, query)], sort)
        if found:
            before = dict(found[0])
            apply_update(found[0], update)
            return dict(found[0]) if return_document else before
        if not upsert:
            return None
        doc = self._upsert_seed(query)
        apply_update(doc, update, inserted=True)
        self._assert_unique(doc)
        self.docs.append(doc)
        return dict(doc) if return_document else None

    async def delete_one(self, query):
        for index, doc in enumerate(self.docs):
            if matches(doc, query):
                del self.docs[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query):
        kept = [doc for doc in self.docs if not matches(doc, query)]
        deleted = len(self.docs) - len(kept)
        self.docs = kept
        return SimpleNamespace(deleted_count=deleted)

    async def bulk_write(self, ops, ordered: bool = True):
        ops = list(ops)
        self.bulk_calls.append((ops, ordered))
        upserted = 0
        modified = 0
        for op in ops:
            if not isinstance(op, UpdateOne):
                raise NotImplementedError(f"fakes.FakeCollection.bulk_write supports UpdateOne only, got {type(op).__name__}")
            # pymongo keeps UpdateOne's filter, update and upsert flag in private attributes;
            # this is the only place tests read them.
            result = await self.update_one(op._filter, op._doc, upsert=bool(op._upsert))
            upserted += int(result.upserted_id is not None)
            modified += result.modified_count
        return SimpleNamespace(upserted_count=upserted, modified_count=modified)


class FakeDB:
    """In-memory Mongo stand-in; any attribute access creates a collection on demand."""

    def __getattr__(self, name: str) -> FakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        collection = FakeCollection()
        setattr(self, name, collection)
        return collection

    def __getitem__(self, name: str) -> FakeCollection:
        return getattr(self, name)
