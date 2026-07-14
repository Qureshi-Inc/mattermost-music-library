/**
 * Database migrations for the WhatsApp bridge.
 */

import Database from 'better-sqlite3';

const MIGRATIONS = [
  {
    version: 1,
    description: 'Initial schema',
    up: `
      CREATE TABLE IF NOT EXISTS user_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mattermost_user_id TEXT NOT NULL UNIQUE,
        mattermost_username TEXT NOT NULL,
        whatsapp_jid TEXT NOT NULL,
        whatsapp_phone TEXT NOT NULL,
        linked_at INTEGER NOT NULL DEFAULT (unixepoch()),
        muted INTEGER NOT NULL DEFAULT 0
      );

      CREATE TABLE IF NOT EXISTS link_codes (
        code TEXT PRIMARY KEY,
        mattermost_user_id TEXT NOT NULL,
        mattermost_username TEXT NOT NULL,
        created_at INTEGER NOT NULL DEFAULT (unixepoch()),
        expires_at INTEGER NOT NULL
      );

      CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id TEXT NOT NULL,
        root_id TEXT NOT NULL,
        sender_user_id TEXT NOT NULL,
        recipient_user_id TEXT NOT NULL,
        sent_at INTEGER NOT NULL DEFAULT (unixepoch()),
        whatsapp_message_id TEXT
      );

      CREATE TABLE IF NOT EXISTS notification_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        post_id TEXT NOT NULL,
        root_id TEXT NOT NULL,
        sender_user_id TEXT NOT NULL,
        sender_username TEXT NOT NULL,
        recipient_user_id TEXT NOT NULL,
        message_preview TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        created_at INTEGER NOT NULL DEFAULT (unixepoch()),
        attempts INTEGER NOT NULL DEFAULT 0,
        last_attempt_at INTEGER,
        status TEXT NOT NULL DEFAULT 'pending',
        error TEXT
      );

      CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_dedup
        ON notifications (post_id, recipient_user_id);

      CREATE INDEX IF NOT EXISTS idx_queue_status
        ON notification_queue (status, created_at);

      CREATE INDEX IF NOT EXISTS idx_link_codes_expiry
        ON link_codes (expires_at);
    `,
  },
];

export function runMigrations(db: Database.Database): void {
  // Create migrations tracking table
  db.exec(`
    CREATE TABLE IF NOT EXISTS schema_migrations (
      version INTEGER PRIMARY KEY,
      description TEXT NOT NULL,
      applied_at INTEGER NOT NULL DEFAULT (unixepoch())
    );
  `);

  const applied = db.prepare(
    'SELECT version FROM schema_migrations ORDER BY version'
  ).all() as { version: number }[];

  const appliedVersions = new Set(applied.map((r) => r.version));

  for (const migration of MIGRATIONS) {
    if (appliedVersions.has(migration.version)) continue;

    console.log(`[db] Running migration v${migration.version}: ${migration.description}`);

    db.transaction(() => {
      db.exec(migration.up);
      db.prepare(
        'INSERT INTO schema_migrations (version, description) VALUES (?, ?)'
      ).run(migration.version, migration.description);
    })();
  }

  console.log('[db] Migrations complete');
}
