/**
 * User linking service.
 *
 * Manages the flow of linking a Mattermost user to their WhatsApp identity:
 * 1. User runs `@slaptastic whatsapp link` in Mattermost
 * 2. Bot generates a 6-character code and DMs it to the user
 * 3. User sends `link CODE` as a WhatsApp DM to the bot number
 * 4. Bridge verifies the code and creates the link
 *
 * For simplicity in v1, the link command accepts a phone number directly:
 * `@slaptastic whatsapp link +1234567890`
 */

import { randomBytes } from 'crypto';
import { DatabaseClient } from '../database/client';
import { WhatsAppClient } from '../whatsapp/client';
import { UserLink } from '../database/models';

export class LinkingService {
  constructor(
    private readonly db: DatabaseClient,
    private readonly whatsapp: WhatsAppClient,
  ) {}

  /**
   * Generate a link code for a Mattermost user.
   * Returns the code they need to send via WhatsApp DM.
   */
  generateLinkCode(mattermostUserId: string, mattermostUsername: string): string {
    const code = randomBytes(3).toString('hex').toUpperCase(); // 6-char hex code
    this.db.createLinkCode(code, mattermostUserId, mattermostUsername, 600); // 10 min TTL
    return code;
  }

  /**
   * Attempt to link directly with a phone number.
   * Resolves the number against the WhatsApp group to get the JID.
   */
  async linkWithPhone(
    mattermostUserId: string,
    mattermostUsername: string,
    phoneNumber: string,
  ): Promise<{ success: boolean; message: string }> {
    // Clean the phone number
    const cleaned = phoneNumber.replace(/[^0-9+]/g, '');
    if (cleaned.length < 8) {
      return { success: false, message: 'Invalid phone number format. Include country code (e.g. +14155551234).' };
    }

    // Try to find this number in the WhatsApp group
    const jid = await this.whatsapp.resolveGroupMember(cleaned);
    if (!jid) {
      // Try verifying the number on WhatsApp directly
      const verifiedJid = await this.whatsapp.verifyNumber(cleaned);
      if (!verifiedJid) {
        return {
          success: false,
          message: `Could not find ${cleaned} on WhatsApp. Make sure the number is correct and includes the country code.`,
        };
      }
      // Number exists on WhatsApp but isn't in the group - link anyway
      this.db.createUserLink(mattermostUserId, mattermostUsername, verifiedJid, cleaned);
      return {
        success: true,
        message: `Linked! Note: This number is not in the notification group yet. Ask an admin to add you.`,
      };
    }

    this.db.createUserLink(mattermostUserId, mattermostUsername, jid, cleaned);
    return {
      success: true,
      message: `Linked your Mattermost account to WhatsApp. You'll receive notifications when someone replies to your Slapshare posts.`,
    };
  }

  /**
   * Consume a link code (called when a WhatsApp message with `link CODE` is received).
   */
  async consumeCode(
    code: string,
    whatsappJid: string,
    whatsappPhone: string,
  ): Promise<{ success: boolean; mattermostUsername?: string }> {
    const linkCode = this.db.consumeLinkCode(code.toUpperCase());
    if (!linkCode) {
      return { success: false };
    }

    this.db.createUserLink(
      linkCode.mattermost_user_id,
      linkCode.mattermost_username,
      whatsappJid,
      whatsappPhone,
    );

    return { success: true, mattermostUsername: linkCode.mattermost_username };
  }

  /**
   * Get the link status for a user.
   */
  getStatus(mattermostUserId: string): UserLink | null {
    return this.db.getUserLink(mattermostUserId);
  }

  /**
   * Unlink a user.
   */
  unlink(mattermostUserId: string): boolean {
    return this.db.deleteUserLink(mattermostUserId);
  }

  /**
   * Mute notifications for a user.
   */
  mute(mattermostUserId: string): boolean {
    return this.db.setMuted(mattermostUserId, true);
  }

  /**
   * Unmute notifications for a user.
   */
  unmute(mattermostUserId: string): boolean {
    return this.db.setMuted(mattermostUserId, false);
  }

  setGroupJid(groupJid: string): void {
    this.db.setSetting('whatsapp_group_jid', groupJid);
    console.log(`[linking] WhatsApp group JID set to: ${groupJid}`);
  }

  getGroupJid(): string | null {
    return this.db.getSetting('whatsapp_group_jid');
  }
}
