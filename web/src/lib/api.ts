export interface Chat {
  id: number;
  title: string;
  created_at: string;
}

export interface StoredMessage {
  id: number;
  chat_id: number;
  role: string;
  /** A JSON string: an OpenAI chat message or a file message. */
  content: string;
  created_at: string;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, 'Cannot reach the server');
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      msg = (await res.json()).error ?? msg;
    } catch {
      /* keep the default message */
    }
    throw new ApiError(res.status, msg);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

const post = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export const getMe = () => call<{ user: string }>('/api/me');
export const listChats = () => call<Chat[]>('/api/chats');
export const createChat = (title: string) => call<Chat>('/api/chats', post({ title }));
export const listMessages = (id: number) => call<StoredMessage[]>(`/api/chats/${id}/messages`);
export const sendMessage = (id: number, text: string) =>
  call<{ run_id: number }>(`/api/chats/${id}/messages`, post({ text }));
export const cancelRun = (runId: number) =>
  call<void>(`/api/runs/${runId}/cancel`, { method: 'POST' });
export const eventsUrl = (chatId: number, after: number) =>
  `/api/chats/${chatId}/events?after=${after}`;
export const fileUrl = (id: number) => `/api/files/${id}`;
