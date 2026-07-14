/**
 * Configuration for the WhatsApp notification bridge.
 * All values come from environment variables.
 */

export interface Config {
  whatsapp: {
    enabled: boolean;
    groupJid: string;
    authEncryptionKey: string;
    authStatePath: string;
  };
  mattermost: {
    url: string;
    token: string;
    teamName: string;
    channelId: string;
    botUsername: string;
  };
  database: {
    path: string;
  };
  logLevel: string;
}

function requireEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function optionalEnv(name: string, defaultValue: string): string {
  return process.env[name] || defaultValue;
}

export function loadConfig(): Config {
  return {
    whatsapp: {
      enabled: optionalEnv('WHATSAPP_ENABLED', 'true') === 'true',
      groupJid: requireEnv('WHATSAPP_GROUP_JID'),
      authEncryptionKey: requireEnv('WHATSAPP_AUTH_ENCRYPTION_KEY'),
      authStatePath: optionalEnv('AUTH_STATE_PATH', './data/auth-state'),
    },
    mattermost: {
      url: optionalEnv('MATTERMOST_URL', 'https://mm.qureshi.io'),
      token: requireEnv('MATTERMOST_TOKEN'),
      teamName: optionalEnv('MATTERMOST_TEAM_NAME', 'qureshi'),
      channelId: optionalEnv('SLAPSHARE_CHANNEL_ID', 'o18rtx6ewifc8r1dnc3kp4xoic'),
      botUsername: optionalEnv('BOT_USERNAME', 'slaptastic'),
    },
    database: {
      path: optionalEnv('DATABASE_PATH', './data/whatsapp-bridge.db'),
    },
    logLevel: optionalEnv('LOG_LEVEL', 'info'),
  };
}
