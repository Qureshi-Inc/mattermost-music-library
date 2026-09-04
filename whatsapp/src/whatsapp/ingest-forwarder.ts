/**
 * Forwards incoming Baileys messages from watched groups to the
 * psn-messenger WhatsApp analytics ingest endpoint.
 */

import { WASocket, proto } from '@whiskeysockets/baileys';

export interface IngestConfig {
  url: string;
  secret: string;
  groupJids: string[];
}

export class IngestForwarder {
  private config: IngestConfig;

  constructor(config: IngestConfig) {
    this.config = config;
  }

  attach(sock: WASocket): void {
    if (!this.config.url || this.config.groupJids.length === 0) return;

    sock.ev.on('messages.upsert', ({ messages, type }) => {
      if (type !== 'notify') return;

      for (const msg of messages) {
        const jid = msg.key.remoteJid || '';
        if (!this.config.groupJids.includes(jid)) continue;
        this.forward(msg, jid).catch((err) => {
          console.warn('[ingest] forward failed:', err.message);
        });
      }
    });
  }

  private async forward(msg: proto.IWebMessageInfo, groupJid: string): Promise<void> {
    const content = msg.message;
    if (!content) return;

    const msgId = msg.key.id || '';
    const senderJid = msg.key.participant || msg.key.remoteJid || '';
    const senderName = msg.pushName || senderJid.split('@')[0] || 'Unknown';
    const rawTs = msg.messageTimestamp;
    const ts = typeof rawTs === 'number' ? rawTs : rawTs ? Number(rawTs) : Math.floor(Date.now() / 1000);

    if (content.reactionMessage) {
      await this.post({
        type: 'reaction',
        target_msg_id: content.reactionMessage.key?.id || '',
        reactor_jid: senderJid,
        reactor_name: senderName,
        emoji: content.reactionMessage.text || '',
        timestamp: ts,
        group_jid: groupJid,
      });
      return;
    }

    let text: string | null = null;
    let msgType = 'text';
    let replyTo: string | null = null;

    if (content.conversation) {
      text = content.conversation;
    } else if (content.extendedTextMessage) {
      text = content.extendedTextMessage.text || null;
      replyTo = content.extendedTextMessage.contextInfo?.stanzaId || null;
    } else if (content.imageMessage) {
      text = content.imageMessage.caption || null;
      msgType = 'image';
    } else if (content.videoMessage) {
      text = content.videoMessage.caption || null;
      msgType = 'video';
    } else if (content.audioMessage || (content as any).pttMessage) {
      msgType = 'audio';
    } else if (content.documentMessage) {
      text = content.documentMessage.caption || null;
      msgType = 'document';
    } else if (content.stickerMessage) {
      msgType = 'sticker';
    } else {
      return; // protocol noise, skip
    }

    await this.post({
      message_id: msgId,
      sender_jid: senderJid,
      sender_name: senderName,
      group_jid: groupJid,
      timestamp: ts,
      text,
      message_type: msgType,
      from_me: msg.key.fromMe || false,
      reply_to: replyTo,
    });
  }

  private async post(payload: object): Promise<void> {
    const res = await fetch(this.config.url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-ingest-secret': this.config.secret,
      },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      console.warn(`[ingest] POST ${this.config.url} returned ${res.status}`);
    }
  }
}
