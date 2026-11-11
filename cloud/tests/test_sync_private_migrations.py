"""Private file schema upgrades are transactional, repeatable and lossless."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from test_sync import conv
from test_sync_private_attachments import SECRET

from nanomuse_cloud.db import Database
from nanomuse_cloud.sync import SyncStore
from nanomuse_cloud.sync_private_attachments import ConversationFiles


def legacy_database(path):
    db = Database(str(path))
    account, cid, mid, ident = (str(uuid.uuid4()) for _ in range(4))
    db.create_account('migration-hash', 'email', 'hint', 0, account_id=account)
    sync = SyncStore(db)
    sync.set_enabled(account, True)
    assert sync.push(account, 'test', [conv(cid)], [{'mid': mid, 'cid': cid, 'role': 'user', 'text': 'original'}])['accepted'] == 2
    content, name, mime = b'legacy encrypted contents', 'legacy.txt', 'text/plain'
    master = hmac.new(SECRET.encode(), b'nanolika:sync-private-files:v1', hashlib.sha256).digest()
    key_id = hashlib.sha256(master).hexdigest()[:16]
    key = hmac.new(master, account.encode(), hashlib.sha256).digest()
    aad = json.dumps([account, cid, ident, name, mime, len(content)], ensure_ascii=False, separators=(',', ':')).encode()
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, content, aad)
    with db.tx() as c:
        c.execute('''CREATE TABLE sync_private_files (
            file_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, cid TEXT NOT NULL,
            operation TEXT NOT NULL, request_digest TEXT NOT NULL, name TEXT NOT NULL, mime TEXT NOT NULL,
            size INTEGER NOT NULL, created INTEGER NOT NULL, key_id TEXT NOT NULL, nonce BLOB NOT NULL,
            ciphertext BLOB NOT NULL, UNIQUE(account_id,operation),
            FOREIGN KEY(account_id,cid) REFERENCES sync_conversations(account_id,cid) ON DELETE CASCADE)''')
        c.execute('INSERT INTO sync_private_files VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (ident, account, cid, 'operation', 'digest', name, mime, len(content), 123, key_id, nonce, ciphertext))
        c.execute('''CREATE TABLE sync_private_generations (
            account_id TEXT PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
            generation INTEGER NOT NULL CHECK(generation>=0))''')
        c.execute('INSERT INTO sync_private_generations VALUES(?,?)', (account, 5))
        c.execute('UPDATE sync_messages SET attachments=? WHERE account_id=? AND mid=?', (json.dumps([{'file_id': ident}]), account, mid))
    return db, sync, account, cid, mid, ident, content


def test_upgrade_restores_legacy_columns_references_and_conservative_wipe_boundary(tmp_path):
    db, sync, account, cid, mid, ident, content = legacy_database(tmp_path / 'cloud.sqlite')
    store = ConversationFiles(db, SECRET)
    assert store.get(account, cid, ident)[1] == content
    state = sync.state(account)
    assert (state['private_generation'], state['private_reset_reason'], state['private_last_wipe_generation']) == (5, 'legacy_unknown', 5)
    with db.tx() as c:
        assert c.execute('SELECT COUNT(*) FROM sync_private_file_refs WHERE account_id=? AND mid=? AND file_id=?', (account, mid, ident)).fetchone()[0] == 1
    assert ConversationFiles(db, SECRET).get(account, cid, ident)[1] == content
    db.close()
    db = Database(str(tmp_path / 'cloud.sqlite'))
    assert ConversationFiles(db, SECRET).get(account, cid, ident)[1] == content
    db.close()


def test_upgrade_replaces_old_trigger_and_preserves_held_file(tmp_path):
    db, sync, account, cid, mid, ident, content = legacy_database(tmp_path / 'cloud.sqlite')
    ConversationFiles(db, SECRET)
    with db.tx() as c:
        c.execute('DROP TRIGGER sync_private_last_ref_deleted')
        c.execute('''CREATE TRIGGER sync_private_last_ref_deleted AFTER DELETE ON sync_private_file_refs
            WHEN NOT EXISTS (SELECT 1 FROM sync_private_file_refs WHERE file_id=OLD.file_id)
            BEGIN DELETE FROM sync_private_files WHERE file_id=OLD.file_id; END''')
        c.execute("INSERT INTO sync_private_recovery_holds VALUES(?,?,?,'history_pruned')", (ident, account, 'old-message'))
    reopened = ConversationFiles(db, SECRET)
    with db.tx() as c:
        sql = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='sync_private_last_ref_deleted'").fetchone()[0]
        assert 'sync_private_recovery_holds' in sql
    assert sync.push(account, 'test', [], [{'mid': mid, 'cid': cid, 'role': 'user', 'deleted': True}])['accepted'] == 1
    assert reopened.get(account, cid, ident)[1] == content
    db.close()


def test_failure_rolls_back_schema_and_keeps_ciphertext_unchanged(tmp_path):
    db, _sync, account, cid, mid, ident, content = legacy_database(tmp_path / 'cloud.sqlite')
    def reject_trigger(action, name, _other, _db, _source):
        if action == sqlite3.SQLITE_CREATE_TRIGGER and name == 'sync_private_last_ref_deleted':
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    db._conn.set_authorizer(reject_trigger)
    with pytest.raises(sqlite3.DatabaseError):
        ConversationFiles(db, SECRET)
    db._conn.set_authorizer(None)
    with db.tx() as c:
        columns = {row['name'] for row in c.execute('PRAGMA table_info(sync_private_files)')}
        assert 'aad_cid' not in columns and 'recovery_reason' not in columns
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='sync_private_schema_meta'").fetchone()
        assert c.execute('SELECT COUNT(*) FROM sync_private_files WHERE file_id=?', (ident,)).fetchone()[0] == 1
    assert ConversationFiles(db, SECRET).get(account, cid, ident)[1] == content
    db.close()


def test_two_process_connections_upgrade_same_database_concurrently(tmp_path):
    db1, _sync, account, cid, mid, ident, content = legacy_database(tmp_path / 'cloud.sqlite')
    db2 = Database(str(tmp_path / 'cloud.sqlite'))
    start = threading.Barrier(2)
    def upgrade(db):
        start.wait(timeout=5)
        return ConversationFiles(db, SECRET).get(account, cid, ident)[1]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(upgrade, db) for db in (db1, db2)]
        assert [f.result(timeout=15) for f in futures] == [content, content]
    with db1.tx() as c:
        assert c.execute('SELECT version FROM sync_private_schema_meta').fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM sync_private_files WHERE file_id=?', (ident,)).fetchone()[0] == 1
        assert c.execute('SELECT COUNT(*) FROM sync_private_file_refs WHERE account_id=? AND mid=?', (account, mid)).fetchone()[0] == 1
    db1.close()
    db2.close()


def test_older_code_refuses_newer_schema_without_modifying_files(tmp_path):
    db, _sync, account, cid, _mid, ident, content = legacy_database(tmp_path / 'cloud.sqlite')
    store = ConversationFiles(db, SECRET)
    with db.tx() as c:
        c.execute('UPDATE sync_private_schema_meta SET version=2 WHERE id=1')
        trigger = c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='sync_private_last_ref_deleted'").fetchone()[0]
    with pytest.raises(RuntimeError, match='newer'):
        ConversationFiles(db, SECRET)
    assert store.get(account, cid, ident)[1] == content
    with db.tx() as c:
        assert c.execute('SELECT version FROM sync_private_schema_meta WHERE id=1').fetchone()[0] == 2
        assert c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='sync_private_last_ref_deleted'").fetchone()[0] == trigger
    db.close()
