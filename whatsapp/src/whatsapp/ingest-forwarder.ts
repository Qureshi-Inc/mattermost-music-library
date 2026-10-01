/**
 * Forwards incoming Baileys messages from watched groups to the
 * psn-messenger WhatsApp analytics ingest endpoint.
 */

import { WASocket, WAMessage, downloadMediaMessage } from '@whiskeysockets/baileys';

export interface IngestConfig {
  url: string;
  secret: string;
  groupJids: string[];
}

const IMAGE_MAX_BYTES = 4 * 1024 * 1024; // 4 MB — keep ingest payloads manageable

export class IngestForwarder {
  private config: IngestConfig;
  private sock: WASocket | null = null;

  constructor(config: IngestConfig) {
    this.config = config;
  }

  attach(sock: WASocket): void {
    this.sock = sock;
    if (!this.config.url || this.config.groupJids.length === 0) return;

    sock.ev.on('messages.upsert', ({ messages, type }) => {
      // 'notify' = incoming messages; 'append' = our own outgoing messages.
      // We forward both so the app can learn our sent message IDs (for
      // swipe-reply detection) and can see incoming images/text.
      if (type !== 'notify' && type !== 'append') return;

      for (const msg of messages) {
        const jid = msg.key.remoteJid || '';
        const isGroup = jid.endsWith('@g.us');
        if (isGroup && !this.config.groupJids.includes(jid)) continue;
        if (!isGroup && !jid.endsWith('@s.whatsapp.net') && !jid.endsWith('@lid')) continue;
        // For outgoing ('append') events, only forward our own messages.
        if (type === 'append' && !msg.key.fromMe) continue;
        this.forward(msg, jid).catch((err) => {
          console.warn('[ingest] forward failed:', err.message);
        });
      }
    });
  }

  private async forward(msg: WAMessage, groupJid: string): Promise<void> {
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
      replyTo = content.imageMessage.contextInfo?.stanzaId || null;
      if (this.sock) {
        try {
          const buf = await downloadMediaMessage(msg, 'buffer', {});
          if (buf instanceof Buffer && buf.length <= IMAGE_MAX_BYTES) {
            const payload = await this.buildPayload(msg, groupJid, text, msgType, replyTo ?? null, buf, 'image/jpeg');
            await this.post(payload);
            return;
          }
        } catch (e: any) {
          console.warn('[ingest] image download failed, forwarding without bytes:', e.message);
        }
      }
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

    await this.post(this.buildPayload(msg, groupJid, text, msgType, replyTo ?? null));
  }

  private buildPayload(
    msg: WAMessage,
    groupJid: string,
    text: string | null,
    msgType: string,
    replyTo: string | null,
    imageBuf?: Buffer,
    imageType?: string,
  ): object {
    const msgId = msg.key.id || '';
    const senderJid = msg.key.participant || msg.key.remoteJid || '';
    const senderName = msg.pushName || senderJid.split('@')[0] || 'Unknown';
    const rawTs = msg.messageTimestamp;
    const ts = typeof rawTs === 'number' ? rawTs : rawTs ? Number(rawTs) : Math.floor(Date.now() / 1000);
    const payload: Record<string, unknown> = {
      message_id: msgId,
      sender_jid: senderJid,
      sender_name: senderName,
      group_jid: groupJid,
      timestamp: ts,
      text,
      message_type: msgType,
      from_me: msg.key.fromMe || false,
      reply_to: replyTo,
    };
    if (imageBuf) {
      payload.image_b64 = imageBuf.toString('base64');
      payload.image_type = imageType || 'image/jpeg';
    }
    return payload;
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
