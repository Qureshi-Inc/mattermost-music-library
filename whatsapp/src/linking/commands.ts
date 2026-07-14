/**
 * Command handler for WhatsApp-related Mattermost commands.
 *
 * Supported commands:
 * - @slaptastic whatsapp link [phone] - Link WhatsApp account
 * - @slaptastic whatsapp unlink - Remove link
 * - @slaptastic whatsapp status - Check link status
 * - @slaptastic whatsapp mute - Pause notifications
 * - @slaptastic whatsapp unmute - Resume notifications
 */

import { MattermostClient } from '../mattermost/client';
import { ParsedCommand } from '../mattermost/types';
import { LinkingService } from './service';

export class CommandHandler {
  constructor(
    private readonly mmClient: MattermostClient,
    private readonly linkingService: LinkingService,
  ) {}

  async handleCommand(command: ParsedCommand): Promise<void> {
    let response: string;

    switch (command.command) {
      case 'link':
        response = await this.handleLink(command);
        break;
      case 'unlink':
        response = this.handleUnlink(command);
        break;
      case 'status':
        response = this.handleStatus(command);
        break;
      case 'mute':
        response = this.handleMute(command);
        break;
      case 'unmute':
        response = this.handleUnmute(command);
        break;
      default:
        response = [
          '**WhatsApp Bridge Commands:**',
          '',
          '| Command | Description |',
          '|---------|-------------|',
          '| `@slaptastic whatsapp link +PHONE` | Link your WhatsApp number |',
          '| `@slaptastic whatsapp unlink` | Remove your WhatsApp link |',
          '| `@slaptastic whatsapp status` | Check your link status |',
          '| `@slaptastic whatsapp mute` | Pause WhatsApp notifications |',
          '| `@slaptastic whatsapp unmute` | Resume WhatsApp notifications |',
        ].join('\n');
        break;
    }

    // Reply in thread or create a new thread from the command post
    const rootId = command.rootId || command.postId;
    await this.mmClient.postMessage(command.channelId, response, rootId);
  }

  private async handleLink(command: ParsedCommand): Promise<string> {
    const phoneNumber = command.args;

    if (!phoneNumber) {
      // Generate a link code for DM-based linking
      const code = this.linkingService.generateLinkCode(command.userId, command.username);
      return [
        `**Link your WhatsApp account:**`,
        '',
        `Send this message as a DM to the Slaptastic bot on WhatsApp:`,
        '',
        `\`link ${code}\``,
        '',
        `_This code expires in 10 minutes._`,
        '',
        '---',
        '',
        'Or link directly with your phone number:',
        '`@slaptastic whatsapp link +1234567890`',
      ].join('\n');
    }

    // Direct phone number linking
    const result = await this.linkingService.linkWithPhone(
      command.userId,
      command.username,
      phoneNumber,
    );

    if (result.success) {
      return `\u{2705} ${result.message}`;
    }
    return `\u{274C} ${result.message}`;
  }

  private handleUnlink(command: ParsedCommand): string {
    const unlinked = this.linkingService.unlink(command.userId);
    if (unlinked) {
      return '\u{2705} WhatsApp link removed. You will no longer receive notifications.';
    }
    return '\u{2139}\u{FE0F} No WhatsApp link found for your account.';
  }

  private handleStatus(command: ParsedCommand): string {
    const link = this.linkingService.getStatus(command.userId);
    if (!link) {
      return [
        '\u{2139}\u{FE0F} **Not linked.** Your Mattermost account is not connected to WhatsApp.',
        '',
        'Use `@slaptastic whatsapp link +PHONE` to connect.',
      ].join('\n');
    }

    const maskedPhone = link.whatsapp_phone.replace(/(\+\d{2})\d+(\d{4})/, '$1****$2');
    const muteStatus = link.muted ? '\u{1F515} Muted' : '\u{1F514} Active';
    const linkedDate = new Date(link.linked_at * 1000).toISOString().split('T')[0];

    return [
      `**WhatsApp Link Status:**`,
      '',
      `| Field | Value |`,
      `|-------|-------|`,
      `| Phone | ${maskedPhone} |`,
      `| Status | ${muteStatus} |`,
      `| Linked | ${linkedDate} |`,
    ].join('\n');
  }

  private handleMute(command: ParsedCommand): string {
    const muted = this.linkingService.mute(command.userId);
    if (muted) {
      return '\u{1F515} WhatsApp notifications muted. Use `@slaptastic whatsapp unmute` to resume.';
    }
    return '\u{2139}\u{FE0F} No WhatsApp link found. Link first with `@slaptastic whatsapp link`.';
  }

  private handleUnmute(command: ParsedCommand): string {
    const unmuted = this.linkingService.unmute(command.userId);
    if (unmuted) {
      return '\u{1F514} WhatsApp notifications resumed!';
    }
    return '\u{2139}\u{FE0F} No WhatsApp link found. Link first with `@slaptastic whatsapp link`.';
  }
}
