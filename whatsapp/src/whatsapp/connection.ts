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

    this.emit('socket', this.socket);

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
   * Send a video message to a JID.
   */
  async sendVideo(jid: string, videoBuffer: Buffer, mimetype: string, caption?: string): Promise<void> {
    if (!this.socket) {
      throw new Error('WhatsApp not connected');
    }
    await this.socket.sendMessage(jid, {
      video: videoBuffer,
      mimetype,
      caption: caption || undefined,
    });
  }

  /**
   * Send an image message to a JID.
   */
  async sendImage(jid: string, imageBuffer: Buffer, caption?: string): Promise<void> {
    if (!this.socket) {
      throw new Error('WhatsApp not connected');
    }
    await this.socket.sendMessage(jid, {
      image: imageBuffer,
      caption: caption || undefined,
    });
  }

  /**
   * Send a document (file) to a JID — preserves full quality, no inline playback.
   */
  async sendDocument(jid: string, fileBuffer: Buffer, mimetype: string, fileName: string, caption?: string): Promise<void> {
    if (!this.socket) {
      throw new Error('WhatsApp not connected');
    }
    await this.socket.sendMessage(jid, {
      document: fileBuffer,
      mimetype,
      fileName,
      caption: caption || undefined,
    });
  }

  /**
   * Send an audio voice note to a JID. ptt=true makes it play inline as a voice message.
   */
  async sendAudio(jid: string, audioBuffer: Buffer, mimetype = 'audio/ogg; codecs=opus'): Promise<void> {
    if (!this.socket) {
      throw new Error('WhatsApp not connected');
    }
    await this.socket.sendMessage(jid, {
      audio: audioBuffer,
      mimetype,
      ptt: true,
    });
  }

  /**
   * React to a message with an emoji.
   */
  async sendReaction(jid: string, messageId: string, participant: string, emoji: string, fromMe = false): Promise<void> {
    if (!this.socket) return;
    const key = { id: messageId, remoteJid: jid, fromMe, participant: participant || undefined };
    await this.socket.sendMessage(jid, { react: { text: emoji, key } });
  }

  /**
   * Edit a message we previously sent.
   */
  async editMessage(jid: string, messageId: string, newText: string): Promise<void> {
    if (!this.socket) return;
    await this.socket.sendMessage(jid, {
      text: newText,
      edit: { id: messageId, remoteJid: jid, fromMe: true },
    });
  }

  /**
   * Send composing/paused presence to a group.
   */
  async sendTyping(jid: string, composing: boolean): Promise<void> {
    if (!this.socket) return;
    await this.socket.sendPresenceUpdate(composing ? 'composing' : 'paused', jid);
  }

  /**
   * Send a text message to a JID.
   */
  async sendMessage(jid: string, text: string, mentions?: string[]): Promise<string | null> {
    if (!this.socket) {
      throw new Error('WhatsApp not connected');
    }

    try {
      const result = await this.socket.sendMessage(jid, {
        text,
        mentions: mentions && mentions.length > 0 ? mentions : undefined,
      });
      return result?.key?.id ?? null;
    } catch (err: any) {
      // If mentions cause jidDecode failure, retry without mentions
      if (err?.message?.includes('jidDecode') || err?.message?.includes('destructure')) {
        console.warn('[whatsapp] Mention failed, sending without mentions:', err.message);
        const result = await this.socket.sendMessage(jid, { text });
        return result?.key?.id ?? null;
      } else {
        throw err;
      }
    }
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
      // JID can be number@s.whatsapp.net or number@lid
      const number = p.id.replace(/@s\.whatsapp\.net$/, '').replace(/@lid$/, '');
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
