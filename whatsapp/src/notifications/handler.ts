/**
 * Notification handler - bridges Mattermost reply events to WhatsApp notifications.
 *
 * When a reply is detected in the Slapshare channel:
 * 1. Fetch the root post to identify the original poster
 * 2. Check if the original poster has a linked WhatsApp account
 * 3. Queue a WhatsApp notification with @mention
 */

import { MattermostClient } from '../mattermost/client';
import { ParsedReply } from '../mattermost/types';
import { DatabaseClient } from '../database/client';
import { Config } from '../config';

export class NotificationHandler {
  constructor(
    private readonly mmClient: MattermostClient,
    private readonly db: DatabaseClient,
    private readonly config: Config,
  ) {}

  /**
   * Handle a reply event from Mattermost.
   */
  async handleReply(reply: ParsedReply): Promise<void> {
    console.log(`[handler] Processing reply from ${reply.username} in thread ${reply.rootId}`);

    // Fetch the root post to find the original poster
    const rootPost = await this.mmClient.getPost(reply.rootId);
    if (!rootPost) {
      console.warn(`[handler] Could not fetch root post ${reply.rootId}`);
      return;
    }

    const originalPosterUserId = rootPost.user_id;

    // Don't notify if replying to yourself
    if (reply.userId === originalPosterUserId) {
      console.log('[handler] Skipping self-reply notification');
      return;
    }

    // Check deduplication before even queuing
    if (this.db.hasNotificationBeenSent(reply.postId, originalPosterUserId)) {
      console.log('[handler] Notification already sent for this post+recipient');
      return;
    }

    // Check if the original poster has a linked WhatsApp account
    const link = this.db.getUserLink(originalPosterUserId);
    if (!link) {
      console.log(`[handler] Original poster ${originalPosterUserId} has no WhatsApp link`);
      return;
    }

    if (link.muted) {
      console.log(`[handler] Original poster ${link.mattermost_username} has muted notifications`);
      return;
    }

    // Truncate message for preview
    let messagePreview = reply.message;
    if (messagePreview.length > 300) {
      messagePreview = messagePreview.substring(0, 297) + '...';
    }

    // Queue the notification
    const queueId = this.db.enqueue({
      post_id: reply.postId,
      root_id: reply.rootId,
      sender_user_id: reply.userId,
      sender_username: reply.username,
      recipient_user_id: originalPosterUserId,
      message_preview: messagePreview,
      channel_id: reply.channelId,
    });

    console.log(
      `[handler] Queued notification #${queueId}: ${reply.username} -> ${link.mattermost_username}`
    );
  }
}
