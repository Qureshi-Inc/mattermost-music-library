/**
 * Mattermost WebSocket client for the WhatsApp bridge.
 *
 * Connects to Mattermost WebSocket, authenticates, and listens for
 * reply events in the Slapshare channel.
 */

import WebSocket from 'ws';
import { EventEmitter } from 'events';
import { Config } from '../config';
import {
  MattermostWsEvent,
  MattermostPost,
  MattermostUser,
  ParsedReply,
  ParsedCommand,
} from './types';

const WHATSAPP_COMMAND_PATTERN = /^(?:@slaptastic\s+)?whatsapp\s+(link|unlink|status|mute|unmute)(?:\s+(.+))?$/i;

export interface MattermostClientEvents {
  reply: (reply: ParsedReply) => void;
  command: (command: ParsedCommand) => void;
  connected: () => void;
  disconnected: () => void;
  error: (error: Error) => void;
}

export class MattermostClient extends EventEmitter {
  private ws: WebSocket | null = null;
  private seq = 0;
  private reconnectAttempt = 0;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private running = false;
  private readonly config: Config['mattermost'];
  private readonly baseDelay = 1000;
  private readonly maxDelay = 60000;

  constructor(config: Config['mattermost']) {
    super();
    this.config = config;
  }

  get wsUrl(): string {
    const url = new URL(this.config.url);
    const scheme = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${scheme}//${url.host}/api/v4/websocket`;
  }

  get apiUrl(): string {
    return `${this.config.url.replace(/\/$/, '')}/api/v4`;
  }

  start(): void {
    this.running = true;
    this.connect();
  }

  stop(): void {
    this.running = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private connect(): void {
    if (!this.running) return;

    console.log(`[mattermost] Connecting to ${this.wsUrl}`);

    this.ws = new WebSocket(this.wsUrl);

    this.ws.on('open', () => {
      console.log('[mattermost] WebSocket connected, authenticating...');
      this.authenticate();
      this.reconnectAttempt = 0;
      this.emit('connected');
    });

    this.ws.on('message', (data: WebSocket.Data) => {
      this.handleMessage(data.toString());
    });

    this.ws.on('close', (code: number, reason: Buffer) => {
      console.log(`[mattermost] WebSocket closed: ${code} ${reason.toString()}`);
      this.emit('disconnected');
      this.scheduleReconnect();
    });

    this.ws.on('error', (err: Error) => {
      console.error(`[mattermost] WebSocket error: ${err.message}`);
      this.emit('error', err);
    });
  }

  private authenticate(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    this.seq++;
    const payload = {
      seq: this.seq,
      action: 'authentication_challenge',
      data: { token: this.config.token },
    };
    this.ws.send(JSON.stringify(payload));
    console.log('[mattermost] Authentication challenge sent');
  }

  private handleMessage(raw: string): void {
    let data: MattermostWsEvent;
    try {
      data = JSON.parse(raw);
    } catch {
      console.warn('[mattermost] Non-JSON message received');
      return;
    }

    // Handle auth reply
    if (data.seq_reply) {
      console.log(`[mattermost] Auth reply: status=${data.status}`);
      return;
    }

    // Only care about posted events
    if (data.event !== 'posted') return;

    const postData = data.data;
    if (!postData?.post) return;

    let post: MattermostPost;
    try {
      post = typeof postData.post === 'string'
        ? JSON.parse(postData.post)
        : postData.post as unknown as MattermostPost;
    } catch {
      console.warn('[mattermost] Failed to parse post JSON');
      return;
    }

    // Must be in the slapshare channel
    if (post.channel_id !== this.config.channelId) return;

    // Ignore bot messages
    const senderName = (postData.sender_name || '').replace(/^@/, '');
    if (senderName === this.config.botUsername) return;
    if (post.props?.from_bot === 'true') return;

    // Check for whatsapp commands (in any message, reply or root)
    const cmdMatch = post.message.match(WHATSAPP_COMMAND_PATTERN);
    if (cmdMatch) {
      const command: ParsedCommand = {
        postId: post.id,
        channelId: post.channel_id,
        userId: post.user_id,
        username: senderName,
        command: cmdMatch[1].toLowerCase(),
        args: (cmdMatch[2] || '').trim(),
        rootId: post.root_id,
      };
      console.log(`[mattermost] WhatsApp command: ${command.command} from ${command.username}`);
      this.emit('command', command);
      return;
    }

    // Only emit replies (root_id must be non-empty)
    if (!post.root_id) return;

    const reply: ParsedReply = {
      postId: post.id,
      channelId: post.channel_id,
      rootId: post.root_id,
      userId: post.user_id,
      username: senderName,
      message: post.message,
      senderName,
    };

    console.log(`[mattermost] Reply detected from ${reply.username} in thread ${reply.rootId}`);
    this.emit('reply', reply);
  }

  private scheduleReconnect(): void {
    if (!this.running) return;

    this.reconnectAttempt++;
    const delay = Math.min(
      this.baseDelay * Math.pow(2, this.reconnectAttempt - 1),
      this.maxDelay,
    );

    console.log(`[mattermost] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempt})`);
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }

  /**
   * Fetch a post by ID via the REST API.
   */
  async getPost(postId: string): Promise<MattermostPost | null> {
    try {
      const resp = await fetch(`${this.apiUrl}/posts/${postId}`, {
        headers: { Authorization: `Bearer ${this.config.token}` },
      });
      if (!resp.ok) return null;
      return await resp.json() as MattermostPost;
    } catch (err) {
      console.error(`[mattermost] Failed to fetch post ${postId}:`, err);
      return null;
    }
  }

  /**
   * Fetch a user by ID via the REST API.
   */
  async getUser(userId: string): Promise<MattermostUser | null> {
    try {
      const resp = await fetch(`${this.apiUrl}/users/${userId}`, {
        headers: { Authorization: `Bearer ${this.config.token}` },
      });
      if (!resp.ok) return null;
      return await resp.json() as MattermostUser;
    } catch (err) {
      console.error(`[mattermost] Failed to fetch user ${userId}:`, err);
      return null;
    }
  }

  /**
   * Post a message to a channel (optionally as a reply).
   */
  async postMessage(channelId: string, message: string, rootId?: string): Promise<void> {
    const payload: Record<string, string> = {
      channel_id: channelId,
      message,
    };
    if (rootId) payload.root_id = rootId;

    try {
      const resp = await fetch(`${this.apiUrl}/posts`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${this.config.token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        const body = await resp.text();
        console.error(`[mattermost] Failed to post message: ${resp.status} ${body}`);
      }
    } catch (err) {
      console.error('[mattermost] Failed to post message:', err);
    }
  }
}
