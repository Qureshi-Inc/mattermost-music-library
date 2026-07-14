/**
 * Notification queue processor.
 *
 * Drains pending notifications from the database queue and sends
 * them via WhatsApp, handling rate limiting and retries.
 */

import { DatabaseClient } from '../database/client';
import { WhatsAppClient } from '../whatsapp/client';
import { formatReplyNotification, NotificationData } from './formatter';
import { Config } from '../config';

export class NotificationQueue {
  private processing = false;
  private timer: NodeJS.Timeout | null = null;
  private readonly pollInterval = 2000; // 2 seconds
  private readonly sendDelay = 1000; // 1 second between messages

  constructor(
    private readonly db: DatabaseClient,
    private readonly whatsapp: WhatsAppClient,
    private readonly config: Config,
  ) {}

  start(): void {
    if (this.timer) return;
    console.log('[queue] Notification queue processor started');
    this.timer = setInterval(() => this.process(), this.pollInterval);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private async process(): Promise<void> {
    if (this.processing) return;
    if (!this.whatsapp.isReady) return;

    this.processing = true;

    try {
      const pending = this.db.getPendingNotifications(5);
      if (pending.length === 0) return;

      for (const notification of pending) {
        // Check deduplication
        if (this.db.hasNotificationBeenSent(notification.post_id, notification.recipient_user_id)) {
          this.db.markSkipped(notification.id, 'already_sent');
          continue;
        }

        // Check if recipient is still linked and not muted
        const link = this.db.getUserLink(notification.recipient_user_id);
        if (!link) {
          this.db.markSkipped(notification.id, 'user_not_linked');
          continue;
        }
        if (link.muted) {
          this.db.markSkipped(notification.id, 'user_muted');
          continue;
        }

        // Don't notify people about their own replies
        if (notification.sender_user_id === notification.recipient_user_id) {
          this.db.markSkipped(notification.id, 'self_reply');
          continue;
        }

        try {
          const data: NotificationData = {
            originalPosterUsername: link.mattermost_username,
            originalPosterJid: link.whatsapp_jid,
            replierUsername: notification.sender_username,
            messagePreview: notification.message_preview,
            postId: notification.post_id,
            teamName: this.config.mattermost.teamName,
            mattermostUrl: this.config.mattermost.url,
          };

          const { text, mentions } = formatReplyNotification(data);
          await this.whatsapp.sendGroupNotification(text, mentions);

          // Record success
          this.db.recordNotification(
            notification.post_id,
            notification.root_id,
            notification.sender_user_id,
            notification.recipient_user_id,
          );
          this.db.markSent(notification.id);

          console.log(
            `[queue] Sent notification for post ${notification.post_id} to ${link.mattermost_username}`
          );

          // Rate limiting delay between sends
          await sleep(this.sendDelay);
        } catch (err) {
          const errorMsg = err instanceof Error ? err.message : String(err);
          console.error(`[queue] Failed to send notification ${notification.id}:`, errorMsg);
          this.db.markFailed(notification.id, errorMsg);
        }
      }
    } finally {
      this.processing = false;
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
