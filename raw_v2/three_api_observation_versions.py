"""P6 P2 append-only observations, deduplicated states and revision events.

Schema is additive to a caller-owned RAW v2 SQLite clone. The caller owns one
BEGIN IMMEDIATE transaction for observations, state rows, events and RAW receipt.
There is no HTTP, filesystem opening, credential access or production migration.

Original checks/state/event rows are immutable. Active change-batch pointers
are an explicitly rebuildable derived projection; an out-of-order check
recomputes its own and its immediate successor's comparison without deleting
previous evidence or inventing historical engagement.
"""
from __future__ import annotations

from hashlib import sha256
import json
import sqlite3

_SCHEMA_VERSION = "p6-observation-v1"
_PARTS = ("content", "metrics", "structure", "locator")
_POST_FIELDS = {
    "content": ("caption", "timestamp"),
    "metrics": ("like_count", "comments_count"),
    "structure": ("media_type", "children", "coverage"),
    "locator": ("permalink", "media_url", "thumbnail_url", "child_locations"),
}
_ACCOUNT_FIELDS = ("id", "username", "followers_count", "media_count")
_ALLOWED_STATES = {"returned", "null", "absent", "not_tested"}


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _id(prefix, *items):
    digest = sha256(_json(items).encode("utf-8")).hexdigest()
    return prefix + digest


def ensure_observation_schema(db):
    """Create versioned RAW-only tables and indexes; never mutate existing rows."""
    statements = (
        """CREATE TABLE IF NOT EXISTS query_observations(
          request_id TEXT PRIMARY KEY REFERENCES request_log(request_id),
          evidence_id TEXT NOT NULL UNIQUE, capture_id TEXT NOT NULL UNIQUE,
          account_key TEXT NOT NULL REFERENCES accounts(account_key),
          checked_at TEXT NOT NULL, outcome TEXT NOT NULL CHECK(outcome='success'),
          returned_count INTEGER NOT NULL CHECK(returned_count BETWEEN 0 AND 4),
          field_schema_version TEXT NOT NULL, quality_status TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_query_account_time ON query_observations(account_key,checked_at,request_id)",
        """CREATE TABLE IF NOT EXISTS post_states(
          state_id TEXT PRIMARY KEY, post_id TEXT NOT NULL REFERENCES posts(post_id),
          component TEXT NOT NULL, schema_version TEXT NOT NULL,
          canonical_hash TEXT NOT NULL, full_values_json TEXT NOT NULL,
          field_states_json TEXT NOT NULL, first_observed_at TEXT NOT NULL,
          UNIQUE(post_id,component,schema_version,canonical_hash))""",
        "CREATE INDEX IF NOT EXISTS idx_post_states_post ON post_states(post_id,component)",
        """CREATE TABLE IF NOT EXISTS post_checks(
          check_id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES query_observations(request_id),
          post_id TEXT NOT NULL REFERENCES posts(post_id), account_key TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          content_state_id TEXT NOT NULL REFERENCES post_states(state_id),
          metrics_state_id TEXT NOT NULL REFERENCES post_states(state_id),
          structure_state_id TEXT NOT NULL REFERENCES post_states(state_id),
          locator_state_id TEXT NOT NULL REFERENCES post_states(state_id),
          UNIQUE(request_id,post_id))""",
        "CREATE INDEX IF NOT EXISTS idx_post_checks_history ON post_checks(post_id,observed_at,request_id)",
        """CREATE TABLE IF NOT EXISTS account_states(
          state_id TEXT PRIMARY KEY, account_key TEXT NOT NULL REFERENCES accounts(account_key),
          schema_version TEXT NOT NULL, canonical_hash TEXT NOT NULL,
          full_values_json TEXT NOT NULL, field_states_json TEXT NOT NULL,
          first_observed_at TEXT NOT NULL,
          UNIQUE(account_key,schema_version,canonical_hash))""",
        """CREATE TABLE IF NOT EXISTS account_checks(
          check_id TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES query_observations(request_id),
          account_key TEXT NOT NULL REFERENCES accounts(account_key),
          observed_at TEXT NOT NULL,
          profile_state_id TEXT NOT NULL REFERENCES account_states(state_id),
          UNIQUE(request_id,account_key))""",
        "CREATE INDEX IF NOT EXISTS idx_account_checks_history ON account_checks(account_key,observed_at,request_id)",
        """CREATE TABLE IF NOT EXISTS media_structure_members(
          structure_state_id TEXT NOT NULL REFERENCES post_states(state_id),
          position INTEGER NOT NULL CHECK(position >= 0), post_id TEXT NOT NULL,
          media_id TEXT NOT NULL, media_type TEXT,
          PRIMARY KEY(structure_state_id,position),
          UNIQUE(structure_state_id,media_id))""",
        """CREATE TABLE IF NOT EXISTS change_events(
          event_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL,
          entity_kind TEXT NOT NULL CHECK(entity_kind IN ('post','account')),
          entity_id TEXT NOT NULL, check_id TEXT NOT NULL, previous_check_id TEXT,
          observed_at TEXT NOT NULL, field_path TEXT NOT NULL,
          previous_field_status TEXT NOT NULL, new_field_status TEXT NOT NULL,
          previous_value_json TEXT, new_value_json TEXT,
          kind TEXT NOT NULL, calculation_version TEXT NOT NULL,
          UNIQUE(batch_id,field_path,kind))""",
        "CREATE INDEX IF NOT EXISTS idx_change_entity_time ON change_events(entity_kind,entity_id,observed_at)",
        """CREATE TABLE IF NOT EXISTS active_change_batches(
          entity_kind TEXT NOT NULL CHECK(entity_kind IN ('post','account')),
          check_id TEXT NOT NULL, batch_id TEXT NOT NULL,
          previous_check_id TEXT, change_count INTEGER NOT NULL,
          PRIMARY KEY(entity_kind,check_id))""",
        """CREATE VIEW IF NOT EXISTS current_change_events AS
          SELECT e.* FROM change_events e
          JOIN active_change_batches b
            ON b.entity_kind=e.entity_kind AND b.check_id=e.check_id
           AND b.batch_id=e.batch_id""",
    )
    for statement in statements:
        db.execute(statement)


def _state(values, fields, tested, *, overrides=None):
    """Never conflate missing fields, explicit NULL, zero and not-requested."""
    overrides = overrides or {}
    states, saved = {}, {}
    for field in fields:
        if field in overrides:
            status = overrides[field]
        elif field not in tested:
            status = "not_tested"
        elif field not in values:
            status = "absent"
        elif values[field] is None:
            status = "null"
        else:
            status = "returned"
        if status not in _ALLOWED_STATES:
            raise ValueError("Unknown field observation state")
        if status == "returned":
            if field not in values or values[field] is None:
                raise ValueError("Returned field lacks a value")
            saved[field] = values[field]
        elif status == "null":
            if field not in values or values[field] is not None:
                raise ValueError("Explicit NULL evidence required")
            saved[field] = None
        elif field in values:
            raise ValueError("Unrequested/absent field cannot have a value")
        states[field] = status
    return saved, states


def _post_components(record, post):
    spec = record.request.request_spec
    tested_media = frozenset(spec.media_fields.split(","))
    pid = post["id"]
    metric_overrides = dict(record.post_field_status[pid])
    content = _state(post, _POST_FIELDS["content"], tested_media)
    metrics = _state(post, _POST_FIELDS["metrics"], tested_media,
                     overrides=metric_overrides)

    structure = dict(post)
    structure.pop("children", None)
    structure["coverage"] = record.carousel_completeness[pid]
    tested_structure = set(tested_media) | {"coverage"}
    if "children" in post:
        structure["children"] = [
            {"id": child["id"], "media_type": child.get("media_type")}
            for child in post["children"]]
    if spec.include_carousel_children or "children" in post:
        tested_structure.add("children")
    struct_state = _state(structure, _POST_FIELDS["structure"],
                          tested_structure)
    locators = dict(post)
    if "children" in post:
        locators["child_locations"] = [
            {"id": child["id"], "media_url": child.get("media_url"),
             "thumbnail_url": child.get("thumbnail_url")}
            for child in post["children"]]
    if spec.include_carousel_children or "children" in post:
        tested_media = tested_media | {"child_locations"}
    locator_state = _state(locators, _POST_FIELDS["locator"], tested_media)
    return dict(zip(_PARTS, (content, metrics, struct_state, locator_state)))


def _write_state(db, kind, entity, component, values, states, observed_at):
    canonical = _json({"values": values, "field_states": states})
    digest = sha256(canonical.encode("utf-8")).hexdigest()
    if kind == "post":
        sid = _id("pstate-", entity, component, _SCHEMA_VERSION, digest)
        row = db.execute("SELECT full_values_json,field_states_json FROM post_states "
                         "WHERE state_id=?", (sid,)).fetchone()
        if row is None:
            db.execute("""INSERT INTO post_states
                (state_id,post_id,component,schema_version,canonical_hash,
                 full_values_json,field_states_json,first_observed_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (sid, entity, component, _SCHEMA_VERSION, digest,
                 _json(values), _json(states), observed_at))
        elif row != (_json(values), _json(states)):
            raise ValueError("Post state digest collision")
    else:
        sid = _id("astate-", entity, _SCHEMA_VERSION, digest)
        row = db.execute("SELECT full_values_json,field_states_json FROM account_states "
                         "WHERE state_id=?", (sid,)).fetchone()
        if row is None:
            db.execute("""INSERT INTO account_states
                (state_id,account_key,schema_version,canonical_hash,
                 full_values_json,field_states_json,first_observed_at)
                VALUES(?,?,?,?,?,?,?)""",
                (sid, entity, _SCHEMA_VERSION, digest,
                 _json(values), _json(states), observed_at))
        elif row != (_json(values), _json(states)):
            raise ValueError("Account state digest collision")
    return sid


def _checks(db, kind, entity):
    if kind == "post":
        rows = db.execute("""SELECT check_id,request_id,observed_at,
                    content_state_id,metrics_state_id,structure_state_id,locator_state_id
              FROM post_checks WHERE post_id=?
              ORDER BY observed_at,request_id""", (entity,)).fetchall()
    else:
        rows = db.execute("""SELECT check_id,request_id,observed_at,
                    profile_state_id
              FROM account_checks WHERE account_key=?
              ORDER BY observed_at,request_id""", (entity,)).fetchall()
    return rows


def _field_map(db, kind, check):
    if kind == "post":
        result = {}
        for component, sid in zip(_PARTS, check[3:]):
            row = db.execute("SELECT full_values_json,field_states_json "
                             "FROM post_states WHERE state_id=?", (sid,)).fetchone()
            if row is None:
                raise ValueError("Missing immutable post state")
            values, states = map(json.loads, row)
            for field, status in states.items():
                result[component + "." + field] = (status, values.get(field))
        return result
    row = db.execute("SELECT full_values_json,field_states_json FROM account_states "
                     "WHERE state_id=?", (check[3],)).fetchone()
    if row is None:
        raise ValueError("Missing account state")
    values, states = map(json.loads, row)
    return {"profile." + field: (status, values.get(field))
            for field, status in states.items()}


def _kind(field_path, old_status, new_status):
    if "not_tested" in (old_status, new_status) or "absent" in (old_status, new_status):
        return "availability_change"
    prefix = field_path.split(".", 1)[0]
    return {"content": "content_change", "metrics": "metric_change",
            "structure": "structure_change", "locator": "locator_change",
            "profile": "profile_change"}[prefix]


def _rebuild_active_comparison(db, kind, entity, check, previous):
    current = _field_map(db, kind, check)
    before = _field_map(db, kind, previous) if previous is not None else {}
    previous_id = previous[0] if previous is not None else None
    batch_id = _id("batch-", kind, check[0], previous_id, _SCHEMA_VERSION)
    count = 0
    if previous is not None:
        for field_path in sorted(current):
            new_status, new_value = current[field_path]
            old_status, old_value = before[field_path]
            if (old_status, old_value) == (new_status, new_value):
                continue
            category = _kind(field_path, old_status, new_status)
            event_id = _id("event-", batch_id, field_path, category)
            db.execute("""INSERT OR IGNORE INTO change_events
                  (event_id,batch_id,entity_kind,entity_id,check_id,previous_check_id,
                   observed_at,field_path,previous_field_status,new_field_status,
                   previous_value_json,new_value_json,kind,calculation_version)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                event_id, batch_id, kind, entity, check[0], previous_id,
                check[2], field_path, old_status, new_status,
                _json(old_value) if old_status in ("returned", "null") else None,
                _json(new_value) if new_status in ("returned", "null") else None,
                category, _SCHEMA_VERSION))
            count += 1
    db.execute("""INSERT INTO active_change_batches(
                   entity_kind,check_id,batch_id,previous_check_id,change_count)
                  VALUES(?,?,?,?,?)
                  ON CONFLICT(entity_kind,check_id) DO UPDATE SET
                    batch_id=excluded.batch_id,
                    previous_check_id=excluded.previous_check_id,
                    change_count=excluded.change_count""",
               (kind, check[0], batch_id, previous_id, count))
    return count


def _insert_and_compare(db, kind, entity, check_id):
    """Recompute just this check and its immediate successor for late arrivals."""
    rows = _checks(db, kind, entity)
    match = next((i for i, row in enumerate(rows) if row[0] == check_id), None)
    if match is None:
        raise ValueError("Observation check was not persisted")
    changed = _rebuild_active_comparison(
        db, kind, entity, rows[match], rows[match - 1] if match else None)
    if match + 1 < len(rows):
        _rebuild_active_comparison(db, kind, entity, rows[match + 1], rows[match])
    return changed


def project_contract_observation(db: sqlite3.Connection, record):
    """Atomically add one successful query, all entity checks and revisions.

    Must be invoked inside write_capture's BEGIN IMMEDIATE transaction after
    existing account, post, request_log identity checks; no own COMMIT.
    """
    from three_api_runtime_contract import CaptureRecord
    if not isinstance(record, CaptureRecord):
        raise TypeError("Validated contract capture required")
    if not db.in_transaction:
        raise ValueError("P2 projection requires the RAW writer transaction")
    ensure_observation_schema(db)
    req = record.request
    rid, account = req.request_id, req.account_key
    checked_at = record.observed_at.isoformat(timespec="microseconds").replace("+00:00", "Z")
    db.execute("""INSERT INTO query_observations
        (request_id,evidence_id,capture_id,account_key,checked_at,outcome,
         returned_count,field_schema_version,quality_status)
        VALUES(?,?,?,?,?,?,?,?,?)""",
        (rid, "response-" + rid, record.capture_id, account, checked_at,
         "success", len(record.normalized_posts), _SCHEMA_VERSION, "verified"))

    account_values = dict(record.account_observation)
    account_states = dict(record.field_status)
    account_states["id"] = "returned"
    account_states["username"] = "returned"
    account_saved, account_field_states = _state(
        account_values, _ACCOUNT_FIELDS, frozenset(_ACCOUNT_FIELDS),
        overrides=account_states)
    account_state_id = _write_state(
        db, "account", account, "profile", account_saved, account_field_states, checked_at)
    account_check_id = _id("acheck-", rid, account)
    db.execute("""INSERT INTO account_checks(
            check_id,request_id,account_key,observed_at,profile_state_id)
            VALUES(?,?,?,?,?)""",
            (account_check_id, rid, account, checked_at, account_state_id))
    account_events = _insert_and_compare(db, "account", account, account_check_id)

    event_count = account_events
    for post in record.normalized_posts:
        pid = post["id"]
        owner = db.execute("SELECT account_key FROM posts WHERE post_id=?", (pid,)).fetchone()
        if owner is None or owner[0] != account:
            raise ValueError("Post identity cross-account conflict")
        ids = {}
        components = _post_components(record, post)
        for component, (values, states) in components.items():
            ids[component] = _write_state(db, "post", pid, component,
                                          values, states, checked_at)
        struct_values = components["structure"][0]
        if "children" in struct_values:
            for position, item in enumerate(struct_values["children"]):
                db.execute("""INSERT OR IGNORE INTO media_structure_members
                    (structure_state_id,position,post_id,media_id,media_type)
                    VALUES(?,?,?,?,?)""",
                    (ids["structure"], position, pid, item["id"], item.get("media_type")))
        check_id = _id("pcheck-", rid, pid)
        db.execute("""INSERT INTO post_checks(
                check_id,request_id,post_id,account_key,observed_at,
                content_state_id,metrics_state_id,structure_state_id,locator_state_id)
                VALUES(?,?,?,?,?,?,?,?,?)""",
                (check_id, rid, pid, account, checked_at,
                 *(ids[part] for part in _PARTS)))
        event_count += _insert_and_compare(db, "post", pid, check_id)
    return {"query_observations": 1, "post_checks": len(record.normalized_posts),
            "account_checks": 1, "new_change_events": event_count}
