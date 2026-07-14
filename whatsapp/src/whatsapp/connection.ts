/**
 * WhatsApp connection manager using Baileys.
 *
 * Handles connection lifecycle, QR code display for pairing,
 * auto-reconnection, and group participant resolution.
 */

import makeWASocket, {
  DisconnectReason,
  WASocket,
  fetchLatestBaileysVersion,
  makeCacheableSignalKeyStore,
  useMultiFileAuthState,
} from '@whiskeysockets/baileys';
import { Boom } from '@hapi/boom';
import { EventEmitter } from 'events';
import pino from 'pino';
import { mkdirSync } from 'fs';
import { Config } from '../config';

export interface WhatsAppConnectionEvents {
  ready: () => void;
  qr: (qr: string) => void;
  disconnected: (reason: string) => void;
}

export class WhatsAppConnection extends EventEmitter {
  private socket: WASocket | null = null;
  private readonly config: Config['whatsapp'];
  private reconnecting = false;
  private logger: pino.Logger;

  constructor(config: Config['whatsapp']) {
    super();
    this.config = config;
    this.logger = pino({ level: 'warn' });
  }

  get sock(): WASocket | null {
    return this.socket;
  }

  get isConnected(): boolean {
    return this.socket?.user != null;
  }

  async connect(): Promise<void> {
    // Ensure auth state directory exists
    mkdirSync(this.config.authStatePath, { recursive: true });

    const { state, saveCreds } = await useMultiFileAuthState(this.config.authStatePath);
    const { version } = await fetchLatestBaileysVersion();

    console.log(`[whatsapp] Connecting with Baileys v${version.join('.')}`);

    this.socket = makeWASocket({
      version,
      auth: {
        creds: state.creds,
        keys: makeCacheableSignalKeyStore(state.keys, this.logger),
      },
      logger: this.logger,
      generateHighQualityLinkPreview: false,
      syncFullHistory: false,
    });

    // Save credentials on update
    this.socket.ev.on('creds.update', saveCreds);

    // Handle connection updates
    this.socket.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        console.log('[whatsapp] QR code received - scan with WhatsApp to pair:');
        console.log('');
        // Render QR code in terminal
        const qrcode = require('qrcode-terminal');
        qrcode.generate(qr, { small: true }, (code: string) => {
          console.log(code);
        });
        console.log('');
        console.log('[whatsapp] Raw QR string:', qr);
        this.emit('qr', qr);
      }

      if (connection === 'close') {
        const statusCode = (lastDisconnect?.error as Boom)?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

        console.log(
          `[whatsapp] Connection closed: status=${statusCode}, reconnect=${shouldReconnect}`
        );

        if (shouldReconnect && !this.reconnecting) {
          this.reconnecting = true;
          setTimeout(async () => {
            this.reconnecting = false;
            await this.connect();
          }, 3000);
        } else if (!shouldReconnect) {
          console.log('[whatsapp] Logged out - delete auth state and restart to re-pair');
          this.emit('disconnected', 'logged_out');
        }
      }

      if (connection === 'open') {
        console.log('[whatsapp] Connected successfully');
        this.emit('ready');
      }
    });
  }

  /**
   * Send a text message to a JID.
   */
  async sendMessage(jid: string, text: string, mentions?: string[]): Promise<void> {
    if (!this.socket) {
      throw new Error('WhatsApp not connected');
    }

    await this.socket.sendMessage(jid, {
      text,
      mentions: mentions && mentions.length > 0 ? mentions : undefined,
    }, {
      // @ts-ignore - disable link preview generation
      linkPreview: null,
    });
  }

  /**
   * Get group metadata including participants.
   */
  async getGroupParticipants(groupJid: string): Promise<Map<string, string>> {
    if (!this.socket) {
      throw new Error('WhatsApp not connected');
    }

    const metadata = await this.socket.groupMetadata(groupJid);
    const participants = new Map<string, string>();

    for (const p of metadata.participants) {
      // JID format: number@s.whatsapp.net
      const number = p.id.replace(/@s\.whatsapp\.net$/, '');
      participants.set(number, p.id);
    }

    return participants;
  }

  /**
   * Check if a phone number is on WhatsApp.
   */
  async isOnWhatsApp(phoneNumber: string): Promise<string | null> {
    if (!this.socket) return null;

    try {
      const results = await this.socket.onWhatsApp(phoneNumber);
      const result = results?.[0];
      return result?.exists ? result.jid : null;
    } catch {
      return null;
    }
  }

  disconnect(): void {
    if (this.socket) {
      this.socket.end(undefined);
      this.socket = null;
    }
  }
}
