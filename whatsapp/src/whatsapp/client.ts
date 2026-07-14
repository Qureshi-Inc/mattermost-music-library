/**
 * High-level WhatsApp client that wraps the connection
 * and provides notification-specific methods.
 */

import { Config } from '../config';
import { WhatsAppConnection } from './connection';

export class WhatsAppClient {
  private connection: WhatsAppConnection;
  private readonly groupJid: string;
  private participantCache: Map<string, string> = new Map();
  private cacheExpiry = 0;
  private readonly cacheTtl = 5 * 60 * 1000; // 5 minutes

  constructor(config: Config['whatsapp']) {
    this.connection = new WhatsAppConnection(config);
    this.groupJid = config.groupJid;
  }

  get isReady(): boolean {
    return this.connection.isConnected;
  }

  async connect(): Promise<void> {
    await this.connection.connect();
  }

  disconnect(): void {
    this.connection.disconnect();
  }

  onReady(callback: () => void): void {
    this.connection.on('ready', callback);
  }

  onQr(callback: (qr: string) => void): void {
    this.connection.on('qr', callback);
  }

  /**
   * Send a notification to the configured WhatsApp group.
   * Supports native @mentions via participant JIDs.
   */
  async sendGroupNotification(
    text: string,
    mentionJids: string[] = [],
  ): Promise<void> {
    await this.connection.sendMessage(this.groupJid, text, mentionJids);
  }

  /**
   * Resolve a phone number to a WhatsApp JID from the group.
   * Returns the JID if found in the group, null otherwise.
   */
  async resolveGroupMember(phoneNumber: string): Promise<string | null> {
    const participants = await this.getParticipants();
    const cleaned = phoneNumber.replace(/[^0-9]/g, '');

    // Direct match
    if (participants.has(cleaned)) {
      return participants.get(cleaned)!;
    }

    // Try with country code variants
    for (const [number, jid] of participants) {
      if (number.endsWith(cleaned) || cleaned.endsWith(number)) {
        return jid;
      }
    }

    return null;
  }

  /**
   * Get all group participants (cached).
   */
  async getParticipants(): Promise<Map<string, string>> {
    const now = Date.now();
    if (now < this.cacheExpiry && this.participantCache.size > 0) {
      return this.participantCache;
    }

    try {
      this.participantCache = await this.connection.getGroupParticipants(this.groupJid);
      this.cacheExpiry = now + this.cacheTtl;
    } catch (err) {
      console.error('[whatsapp-client] Failed to fetch group participants:', err);
    }

    return this.participantCache;
  }

  /**
   * Verify a JID is on WhatsApp.
   */
  async verifyNumber(phoneNumber: string): Promise<string | null> {
    return this.connection.isOnWhatsApp(phoneNumber);
  }
}
