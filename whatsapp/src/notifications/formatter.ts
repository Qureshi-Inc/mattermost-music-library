/**
 * Format WhatsApp notification messages.
 *
 * Constructs the notification text with @mentions and post links.
 */

import { Config } from '../config';

export interface NotificationData {
  originalPosterUsername: string;
  originalPosterJid: string | null;
  replierUsername: string;
  messagePreview: string;
  postId: string;
  teamName: string;
  mattermostUrl: string;
}

/**
 * Format a reply notification for WhatsApp.
 *
 * If the original poster has a linked WhatsApp account, their JID
 * is included in the mentions array for a native @mention.
 */
export function formatReplyNotification(data: NotificationData): {
  text: string;
  mentions: string[];
} {
  const mentions: string[] = [];
  let mentionText: string;

  if (data.originalPosterJid) {
    // Native WhatsApp mention - use @number format in text
    const number = data.originalPosterJid.replace(/@s\.whatsapp\.net$/, '');
    mentionText = `@${number}`;
    mentions.push(data.originalPosterJid);
  } else {
    // No WhatsApp link - just use their Mattermost username
    mentionText = `@${data.originalPosterUsername}`;
  }

  // Truncate message preview to 200 chars
  let preview = data.messagePreview;
  if (preview.length > 200) {
    preview = preview.substring(0, 197) + '...';
  }

  // Build the post permalink
  const permalink = `${data.mattermostUrl}/${data.teamName}/pl/${data.postId}`;

  const text = [
    `${mentionText} \u{1F3B5} ${data.replierUsername} replied to your Slapshare post:`,
    '',
    `"${preview}"`,
    '',
    `\u{1F517} ${permalink}`,
  ].join('\n');

  return { text, mentions };
}

/**
 * Format a link confirmation message for WhatsApp.
 */
export function formatLinkConfirmation(mattermostUsername: string): string {
  return [
    `\u{2705} Linked! Your Mattermost account (@${mattermostUsername}) is now connected.`,
    '',
    'You will receive WhatsApp notifications when someone replies to your Slapshare posts.',
    '',
    'Commands in Mattermost:',
    '\u{2022} `@slaptastic whatsapp mute` - Pause notifications',
    '\u{2022} `@slaptastic whatsapp unmute` - Resume notifications',
    '\u{2022} `@slaptastic whatsapp unlink` - Remove this link',
  ].join('\n');
}
