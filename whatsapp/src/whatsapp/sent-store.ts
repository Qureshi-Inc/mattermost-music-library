/**
 * The messages this bot has sent, kept for a week so WhatsApp retries can be answered.
 *
 * When a recipient's phone can't decrypt one of our messages (it reinstalled,
 * changed phone, or its session with us went stale) it sends a retry receipt.
 * Baileys answers by re-encrypting the original on a fresh session, but only if
 * `getMessage` can hand the original back. Without this store it can't, and the
 * recipient is stuck on "Waiting for this message" for good.
 *
 * On disk, not in memory, so a restart between the send and the retry is fine.
 */

import Database from 'better-sqlite3';
import { mkdirSync } from 'fs';
import { dirname } from 'path';
import { proto } from '@whiskeysockets/baileys';
import type { WAMessage, WAMessageKey } from '@whiskeysockets/baileys';

const KEEP_MS = 7 * 24 * 3600 * 1000;

export class SentMessageStore {
  private db: Database.Database;

  constructor(dbPath: string) {
    mkdirSync(dirname(dbPath), { recursive: true });
    this.db = new Database(dbPath);
    this.db.pragma('journal_mode = WAL');
    this.db.exec(`CREATE TABLE IF NOT EXISTS sent_messages (
      id TEXT NOT NULL, jid TEXT NOT NULL, ts INTEGER NOT NULL, message BLOB NOT NULL,
      PRIMARY KEY (id, jid))`);
    this.db.exec('CREATE INDEX IF NOT EXISTS sent_messages_ts ON sent_messages (ts)');
    this.prune();
  }

  /** Keep our own outgoing messages; everything else is ignored. */
  save(msg: WAMessage): void {
    const { id, remoteJid, fromMe } = msg.key || {};
    if (!fromMe || !id || !remoteJid || !msg.message) return;
    const bytes = Buffer.from(proto.Message.encode(msg.message).finish());
    this.db.prepare('INSERT OR REPLACE INTO sent_messages (id, jid, ts, message) VALUES (?, ?, ?, ?)')
      .run(id, remoteJid, Date.now(), bytes);
  }

  /** The original message for a retry receipt, or undefined if we never sent it (or it's too old). */
  get(key: WAMessageKey): proto.IMessage | undefined {
    if (!key.id) return undefined;
    // The retry can name the chat by a different JID (phone vs @lid), so the id alone decides.
    const row = this.db.prepare('SELECT message FROM sent_messages WHERE id = ? ORDER BY ts DESC LIMIT 1')
      .get(key.id) as { message: Buffer } | undefined;
    return row ? proto.Message.decode(row.message) : undefined;
  }

  prune(): void {
    this.db.prepare('DELETE FROM sent_messages WHERE ts < ?').run(Date.now() - KEEP_MS);
  }
}

/** Baileys' CacheStore, on a Map: counts retries per message so a broken one gives up. */
export class RetryCounter {
  private m = new Map<string, { v: unknown; until: number }>();

  get<T>(key: string): T | undefined {
    const e = this.m.get(key);
    if (!e) return undefined;
    if (e.until < Date.now()) {
      this.m.delete(key);
      return undefined;
    }
    return e.v as T;
  }

  set<T>(key: string, value: T): void {
    if (this.m.size > 5000) this.m.clear();
    this.m.set(key, { v: value, until: Date.now() + 3600 * 1000 });
  }

  del(key: string): void {
    this.m.delete(key);
  }

  flushAll(): void {
    this.m.clear();
  }
}
