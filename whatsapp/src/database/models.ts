/**
 * Database model types for the WhatsApp bridge.
 */

export interface UserLink {
  id: number;
  mattermost_user_id: string;
  mattermost_username: string;
  whatsapp_jid: string;
  whatsapp_phone: string;
  linked_at: number;
  muted: boolean;
}

export interface LinkCode {
  code: string;
  mattermost_user_id: string;
  mattermost_username: string;
  created_at: number;
  expires_at: number;
}

export interface NotificationRecord {
  id: number;
  post_id: string;
  root_id: string;
  sender_user_id: string;
  recipient_user_id: string;
  sent_at: number;
  whatsapp_message_id: string | null;
}

export interface QueuedNotification {
  id: number;
  post_id: string;
  root_id: string;
  sender_user_id: string;
  sender_username: string;
  recipient_user_id: string;
  message_preview: string;
  channel_id: string;
  created_at: number;
  attempts: number;
  last_attempt_at: number | null;
  status: 'pending' | 'sent' | 'failed' | 'skipped';
  error: string | null;
}
