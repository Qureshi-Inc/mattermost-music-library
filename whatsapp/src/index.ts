/**
 * WhatsApp Notification Bridge for Slaptastic
 *
 * Standalone Node.js worker that:
 * 1. Connects to Mattermost WebSocket (same channel as the music bot)
 * 2. Detects replies to posts in the Slapshare channel
 * 3. Sends WhatsApp notifications to linked users via Baileys
 *
 * This runs as a separate service alongside the Python music bot.
 */

import { loadConfig } from './config';
import { MattermostClient } from './mattermost/client';
import { WhatsAppClient } from './whatsapp/client';
import { DatabaseClient } from './database/client';
import { NotificationHandler } from './notifications/handler';
import { NotificationQueue } from './notifications/queue';
import { LinkingService } from './linking/service';
import { CommandHandler } from './linking/commands';
import { ParsedReply, ParsedCommand } from './mattermost/types';

async function main(): Promise<void> {
  console.log('[bridge] Starting WhatsApp notification bridge for Slaptastic');

  // Load configuration
  const config = loadConfig();

  if (!config.whatsapp.enabled) {
    console.log('[bridge] WhatsApp bridge is disabled (WHATSAPP_ENABLED=false)');
    process.exit(0);
  }

  // Initialize database
  const db = new DatabaseClient(config.database.path);
  console.log('[bridge] Database initialized');

  // Initialize WhatsApp client
  const whatsapp = new WhatsAppClient(config.whatsapp);

  // Initialize services
  const linkingService = new LinkingService(db, whatsapp);

  // Initialize Mattermost client
  const mmClient = new MattermostClient(config.mattermost);
  const notificationHandler = new NotificationHandler(mmClient, db, config);
  const commandHandler = new CommandHandler(mmClient, linkingService);

  // Initialize notification queue processor
  const queue = new NotificationQueue(db, whatsapp, config);

  // Wire up Mattermost events
  mmClient.on('reply', (reply: ParsedReply) => {
    notificationHandler.handleReply(reply).catch((err) => {
      console.error('[bridge] Error handling reply:', err);
    });
  });

  mmClient.on('command', (command: ParsedCommand) => {
    commandHandler.handleCommand(command).catch((err) => {
      console.error('[bridge] Error handling command:', err);
    });
  });

  mmClient.on('connected', () => {
    console.log('[bridge] Mattermost connection established');
  });

  mmClient.on('disconnected', () => {
    console.log('[bridge] Mattermost disconnected, will reconnect...');
  });

  // Wire up WhatsApp events
  whatsapp.onReady(() => {
    console.log('[bridge] WhatsApp connection ready');
    queue.start();
  });

  whatsapp.onQr((qr) => {
    console.log('[bridge] Scan the QR code above with WhatsApp to pair this device');
  });

  // Start connections
  console.log('[bridge] Connecting to WhatsApp...');
  await whatsapp.connect();

  console.log('[bridge] Connecting to Mattermost...');
  mmClient.start();

  // Periodic cleanup of expired link codes
  setInterval(() => {
    const cleaned = db.cleanExpiredCodes();
    if (cleaned > 0) {
      console.log(`[bridge] Cleaned ${cleaned} expired link codes`);
    }
  }, 60000); // Every minute

  // Graceful shutdown
  const shutdown = () => {
    console.log('[bridge] Shutting down...');
    queue.stop();
    mmClient.stop();
    whatsapp.disconnect();
    db.close();
    process.exit(0);
  };

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  console.log('[bridge] WhatsApp notification bridge is running');
}

main().catch((err) => {
  console.error('[bridge] Fatal error:', err);
  process.exit(1);
});
