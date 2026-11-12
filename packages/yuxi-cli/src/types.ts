export type Json = Record<string, unknown>;

export interface Remote {
  name: string;
  url: string;
  apiKey?: string;
  apiKeyId?: string;
}

export interface Config {
  current: string;
  remotes: Record<string, Remote>;
}

export interface SseEvent {
  event?: string;
  id?: string;
  data?: string;
}

export class YuxiError extends Error {
  constructor(message: string, public readonly status?: number, public readonly code?: string) {
    super(message);
    this.name = "YuxiError";
  }
}
