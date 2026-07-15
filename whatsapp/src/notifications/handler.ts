/**
 * Notification handler - bridges Mattermost reply events to WhatsApp notifications.
 *
 * When a reply is detected in the Slapshare channel:
 * 1. Notify the original poster (unless it's a self-reply)
 * 2. Notify any @mentioned users in the reply
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

    // Truncate message for preview
    let messagePreview = reply.message;
    if (messagePreview.length > 300) {
      messagePreview = messagePreview.substring(0, 297) + '...';
    }

    // 1. Notify the original poster (unless self-reply)
    if (reply.userId !== originalPosterUserId) {
      this.notifyUser(originalPosterUserId, reply, messagePreview);
    }

    // 2. Notify any @mentioned users
    const mentions = this.extractMentions(reply.message);
    for (const mentionedUsername of mentions) {
      // Find the mentioned user's Mattermost ID from linked users
      const allLinked = this.db.getAllLinkedUsers();
      const mentionedLink = allLinked.find(
        (u) => u.mattermost_username.toLowerCase() === mentionedUsername.toLowerCase()
      );

      if (!mentionedLink) continue;

      // Don't notify the sender or someone already notified as OP
      if (mentionedLink.mattermost_user_id === reply.userId) continue;
      if (mentionedLink.mattermost_user_id === originalPosterUserId) continue;

      this.notifyUser(mentionedLink.mattermost_user_id, reply, `[mention]${messagePreview}`);
    }
  }

  private notifyUser(recipientUserId: string, reply: ParsedReply, messagePreview: string): void {
    // Check deduplication
    if (this.db.hasNotificationBeenSent(reply.postId, recipientUserId)) {
      console.log(`[handler] Notification already sent for post ${reply.postId} -> ${recipientUserId}`);
      return;
    }

    // Check if recipient has a linked WhatsApp account
    const link = this.db.getUserLink(recipientUserId);
    if (!link) {
      console.log(`[handler] User ${recipientUserId} has no WhatsApp link`);
      return;
    }

    if (link.muted) {
      console.log(`[handler] User ${link.mattermost_username} has muted notifications`);
      return;
    }

    // Queue the notification
    const queueId = this.db.enqueue({
      post_id: reply.postId,
      root_id: reply.rootId,
      sender_user_id: reply.userId,
      sender_username: reply.username,
      recipient_user_id: recipientUserId,
      message_preview: messagePreview,
      channel_id: reply.channelId,
    });

    console.log(
      `[handler] Queued notification #${queueId}: ${reply.username} -> ${link.mattermost_username}`
    );
  }

  private extractMentions(message: string): string[] {
    const mentionRegex = /@(\w+)/g;
    const mentions: string[] = [];
    let match;
    while ((match = mentionRegex.exec(message)) !== null) {
      const username = match[1];
      if (username !== 'slaptastic' && username !== 'slapper' && username !== 'channel' && username !== 'all' && username !== 'here') {
        mentions.push(username);
      }
    }
    return mentions;
  }
}
