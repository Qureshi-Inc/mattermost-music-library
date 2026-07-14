/**
 * Mattermost WebSocket protocol types.
 */

export interface MattermostWsAuth {
  seq: number;
  action: 'authentication_challenge';
  data: {
    token: string;
  };
}

export interface MattermostWsEvent {
  event?: string;
  seq_reply?: number;
  status?: string;
  data?: {
    post?: string;
    sender_name?: string;
    channel_display_name?: string;
    channel_name?: string;
    channel_type?: string;
    team_id?: string;
    [key: string]: unknown;
  };
  broadcast?: {
    channel_id?: string;
    team_id?: string;
    user_id?: string;
    [key: string]: unknown;
  };
}

export interface MattermostPost {
  id: string;
  create_at: number;
  update_at: number;
  delete_at: number;
  user_id: string;
  channel_id: string;
  root_id: string;
  message: string;
  type: string;
  props: {
    from_bot?: string;
    [key: string]: unknown;
  };
  hashtags: string;
  pending_post_id: string;
}

export interface MattermostUser {
  id: string;
  username: string;
  nickname: string;
  first_name: string;
  last_name: string;
  email: string;
}

export interface ParsedReply {
  postId: string;
  channelId: string;
  rootId: string;
  userId: string;
  username: string;
  message: string;
  senderName: string;
}

export interface ParsedCommand {
  postId: string;
  channelId: string;
  userId: string;
  username: string;
  command: string;
  args: string;
  rootId: string;
}
