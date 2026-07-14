/**
 * Database client for the WhatsApp bridge.
 * Wraps better-sqlite3 with typed query methods.
 */

import Database from 'better-sqlite3';
import { mkdirSync } from 'fs';
import { dirname } from 'path';
import { runMigrations } from './migrations';
import { UserLink, LinkCode, NotificationRecord, QueuedNotification } from './models';

export class DatabaseClient {
  private db: Database.Database;

  constructor(dbPath: string) {
    mkdirSync(dirname(dbPath), { recursive: true });
    this.db = new Database(dbPath);
    this.db.pragma('journal_mode = WAL');
    this.db.pragma('foreign_keys = ON');
    runMigrations(this.db);
  }

  // ---- User Links ----

  getUserLink(mattermostUserId: string): UserLink | null {
    return this.db.prepare(
      'SELECT * FROM user_links WHERE mattermost_user_id = ?'
    ).get(mattermostUserId) as UserLink | null;
  }

  getUserLinkByJid(whatsappJid: string): UserLink | null {
    return this.db.prepare(
      'SELECT * FROM user_links WHERE whatsapp_jid = ?'
    ).get(whatsappJid) as UserLink | null;
  }

  getAllLinkedUsers(): UserLink[] {
    return this.db.prepare(
      'SELECT * FROM user_links WHERE muted = 0'
    ).all() as UserLink[];
  }

  createUserLink(
    mattermostUserId: string,
    mattermostUsername: string,
    whatsappJid: string,
    whatsappPhone: string,
  ): UserLink {
    this.db.prepare(`
      INSERT OR REPLACE INTO user_links
        (mattermost_user_id, mattermost_username, whatsapp_jid, whatsapp_phone, linked_at, muted)
      VALUES (?, ?, ?, ?, unixepoch(), 0)
    `).run(mattermostUserId, mattermostUsername, whatsappJid, whatsappPhone);

    return this.getUserLink(mattermostUserId)!;
  }

  deleteUserLink(mattermostUserId: string): boolean {
    const result = this.db.prepare(
      'DELETE FROM user_links WHERE mattermost_user_id = ?'
    ).run(mattermostUserId);
    return result.changes > 0;
  }

  setMuted(mattermostUserId: string, muted: boolean): boolean {
    const result = this.db.prepare(
      'UPDATE user_links SET muted = ? WHERE mattermost_user_id = ?'
    ).run(muted ? 1 : 0, mattermostUserId);
    return result.changes > 0;
  }

  // ---- Link Codes ----

  createLinkCode(
    code: string,
    mattermostUserId: string,
    mattermostUsername: string,
    ttlSeconds: number = 600,
  ): LinkCode {
    const expiresAt = Math.floor(Date.now() / 1000) + ttlSeconds;

    // Clean up any existing codes for this user
    this.db.prepare(
      'DELETE FROM link_codes WHERE mattermost_user_id = ?'
    ).run(mattermostUserId);

    this.db.prepare(`
      INSERT INTO link_codes (code, mattermost_user_id, mattermost_username, expires_at)
      VALUES (?, ?, ?, ?)
    `).run(code, mattermostUserId, mattermostUsername, expiresAt);

    return {
      code,
      mattermost_user_id: mattermostUserId,
      mattermost_username: mattermostUsername,
      created_at: Math.floor(Date.now() / 1000),
      expires_at: expiresAt,
    };
  }

  consumeLinkCode(code: string): LinkCode | null {
    const now = Math.floor(Date.now() / 1000);

    const linkCode = this.db.prepare(
      'SELECT * FROM link_codes WHERE code = ? AND expires_at > ?'
    ).get(code, now) as LinkCode | null;

    if (linkCode) {
      this.db.prepare('DELETE FROM link_codes WHERE code = ?').run(code);
    }

    return linkCode;
  }

  cleanExpiredCodes(): number {
    const now = Math.floor(Date.now() / 1000);
    const result = this.db.prepare(
      'DELETE FROM link_codes WHERE expires_at <= ?'
    ).run(now);
    return result.changes;
  }

  // ---- Notifications ----

  hasNotificationBeenSent(postId: string, recipientUserId: string): boolean {
    const row = this.db.prepare(
      'SELECT 1 FROM notifications WHERE post_id = ? AND recipient_user_id = ?'
    ).get(postId, recipientUserId);
    return !!row;
  }

  recordNotification(
    postId: string,
    rootId: string,
    senderUserId: string,
    recipientUserId: string,
    whatsappMessageId?: string,
  ): void {
    this.db.prepare(`
      INSERT OR IGNORE INTO notifications
        (post_id, root_id, sender_user_id, recipient_user_id, whatsapp_message_id)
      VALUES (?, ?, ?, ?, ?)
    `).run(postId, rootId, senderUserId, recipientUserId, whatsappMessageId || null);
  }

  // ---- Notification Queue ----

  enqueue(notification: Omit<QueuedNotification, 'id' | 'created_at' | 'attempts' | 'last_attempt_at' | 'status' | 'error'>): number {
    const result = this.db.prepare(`
      INSERT INTO notification_queue
        (post_id, root_id, sender_user_id, sender_username, recipient_user_id, message_preview, channel_id)
      VALUES (?, ?, ?, ?, ?, ?, ?)
    `).run(
      notification.post_id,
      notification.root_id,
      notification.sender_user_id,
      notification.sender_username,
      notification.recipient_user_id,
      notification.message_preview,
      notification.channel_id,
    );
    return result.lastInsertRowid as number;
  }

  getPendingNotifications(limit: number = 10): QueuedNotification[] {
    return this.db.prepare(`
      SELECT * FROM notification_queue
      WHERE status = 'pending' AND attempts < 3
      ORDER BY created_at ASC
      LIMIT ?
    `).all(limit) as QueuedNotification[];
  }

  markSent(id: number): void {
    this.db.prepare(`
      UPDATE notification_queue
      SET status = 'sent', last_attempt_at = unixepoch(), attempts = attempts + 1
      WHERE id = ?
    `).run(id);
  }

  markFailed(id: number, error: string): void {
    this.db.prepare(`
      UPDATE notification_queue
      SET status = CASE WHEN attempts >= 2 THEN 'failed' ELSE status END,
          last_attempt_at = unixepoch(),
          attempts = attempts + 1,
          error = ?
      WHERE id = ?
    `).run(error, id);
  }

  markSkipped(id: number, reason: string): void {
    this.db.prepare(`
      UPDATE notification_queue
      SET status = 'skipped', error = ?
      WHERE id = ?
    `).run(reason, id);
  }

  setSetting(key: string, value: string): void {
    this.db.prepare(`
      INSERT INTO settings (key, value) VALUES (?, ?)
      ON CONFLICT(key) DO UPDATE SET value = excluded.value
    `).run(key, value);
  }

  getSetting(key: string): string | null {
    const row = this.db.prepare('SELECT value FROM settings WHERE key = ?').get(key) as { value: string } | undefined;
    return row?.value || null;
  }

  close(): void {
    this.db.close();
  }
}
