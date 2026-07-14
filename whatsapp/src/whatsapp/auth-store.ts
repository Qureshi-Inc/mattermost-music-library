/**
 * Baileys auth state persistence using better-sqlite3.
 *
 * Stores WhatsApp session credentials in SQLite so the connection
 * survives restarts without re-scanning the QR code.
 */

import {
  AuthenticationCreds,
  AuthenticationState,
  SignalDataTypeMap,
  initAuthCreds,
  proto,
  BufferJSON,
} from '@whiskeysockets/baileys';
import Database from 'better-sqlite3';
import { createCipheriv, createDecipheriv, randomBytes } from 'crypto';

const ALGORITHM = 'aes-256-gcm';

export class SqliteAuthStore {
  private db: Database.Database;
  private encryptionKey: Buffer;

  constructor(dbPath: string, encryptionKeyHex: string) {
    this.encryptionKey = Buffer.from(encryptionKeyHex, 'hex');
    if (this.encryptionKey.length !== 32) {
      throw new Error('WHATSAPP_AUTH_ENCRYPTION_KEY must be 32 bytes (64 hex chars)');
    }

    this.db = new Database(dbPath);
    this.db.pragma('journal_mode = WAL');
    this.initTables();
  }

  private initTables(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS auth_creds (
        id TEXT PRIMARY KEY DEFAULT 'creds',
        data TEXT NOT NULL,
        updated_at INTEGER NOT NULL DEFAULT (unixepoch())
      );
      CREATE TABLE IF NOT EXISTS auth_keys (
        category TEXT NOT NULL,
        id TEXT NOT NULL,
        data TEXT NOT NULL,
        updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
        PRIMARY KEY (category, id)
      );
    `);
  }

  private encrypt(plaintext: string): string {
    const iv = randomBytes(12);
    const cipher = createCipheriv(ALGORITHM, this.encryptionKey, iv);
    const encrypted = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
    const tag = cipher.getAuthTag();
    // Format: iv:tag:ciphertext (all base64)
    return `${iv.toString('base64')}:${tag.toString('base64')}:${encrypted.toString('base64')}`;
  }

  private decrypt(ciphertext: string): string {
    const parts = ciphertext.split(':');
    if (parts.length !== 3) throw new Error('Invalid encrypted data format');
    const iv = Buffer.from(parts[0], 'base64');
    const tag = Buffer.from(parts[1], 'base64');
    const encrypted = Buffer.from(parts[2], 'base64');
    const decipher = createDecipheriv(ALGORITHM, this.encryptionKey, iv);
    decipher.setAuthTag(tag);
    return decipher.update(encrypted) + decipher.final('utf8');
  }

  private saveCreds(creds: AuthenticationCreds): void {
    const data = this.encrypt(JSON.stringify(creds, BufferJSON.replacer));
    this.db.prepare(
      `INSERT OR REPLACE INTO auth_creds (id, data, updated_at) VALUES ('creds', ?, unixepoch())`
    ).run(data);
  }

  private loadCreds(): AuthenticationCreds | null {
    const row = this.db.prepare(
      `SELECT data FROM auth_creds WHERE id = 'creds'`
    ).get() as { data: string } | undefined;

    if (!row) return null;

    try {
      const decrypted = this.decrypt(row.data);
      return JSON.parse(decrypted, BufferJSON.reviver);
    } catch (err) {
      console.error('[auth-store] Failed to decrypt creds, starting fresh:', err);
      return null;
    }
  }

  async getAuthState(): Promise<{ state: AuthenticationState; saveCreds: () => Promise<void> }> {
    let creds = this.loadCreds();
    if (!creds) {
      creds = initAuthCreds();
      this.saveCreds(creds);
    }

    const state: AuthenticationState = {
      creds,
      keys: {
        get: (type: keyof SignalDataTypeMap, ids: string[]) => {
          const result: Record<string, any> = {};
          const stmt = this.db.prepare(
            `SELECT id, data FROM auth_keys WHERE category = ? AND id = ?`
          );
          for (const id of ids) {
            const row = stmt.get(type, id) as { id: string; data: string } | undefined;
            if (row) {
              try {
                const decrypted = this.decrypt(row.data);
                let value = JSON.parse(decrypted, BufferJSON.reviver);
                // Handle pre-key specifically
                if (type === 'app-state-sync-key' && value) {
                  value = proto.Message.AppStateSyncKeyData.fromObject(value);
                }
                result[id] = value;
              } catch {
                // Corrupted key, skip
              }
            }
          }
          return result;
        },
        set: (data: Record<string, Record<string, any>>) => {
          const insertStmt = this.db.prepare(
            `INSERT OR REPLACE INTO auth_keys (category, id, data, updated_at) VALUES (?, ?, ?, unixepoch())`
          );
          const deleteStmt = this.db.prepare(
            `DELETE FROM auth_keys WHERE category = ? AND id = ?`
          );

          const transaction = this.db.transaction(() => {
            for (const category in data) {
              for (const id in data[category]) {
                const value = data[category][id];
                if (value) {
                  const encrypted = this.encrypt(JSON.stringify(value, BufferJSON.replacer));
                  insertStmt.run(category, id, encrypted);
                } else {
                  deleteStmt.run(category, id);
                }
              }
            }
          });
          transaction();
        },
      },
    };

    return {
      state,
      saveCreds: async () => {
        this.saveCreds(state.creds);
      },
    };
  }

  close(): void {
    this.db.close();
  }
}
