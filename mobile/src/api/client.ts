import type {
  AnalysisResult,
  AnalysisStatus,
  ApiErrorBody,
  Upload,
  UploadSlot,
} from './types';

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryAfterMs?: number,
  ) {
    super(message);
  }
}

export type ResultResponse =
  | { state: 'completed'; result: AnalysisResult }
  | { state: 'processing'; status: AnalysisStatus };

type Fetch = typeof fetch;

function retryAfterMs(response: Response): number | undefined {
  const value = response.headers.get('Retry-After');
  if (!value) return undefined;

  const seconds = Number(value);
  if (Number.isFinite(seconds)) return Math.max(0, seconds * 1000);

  const at = Date.parse(value);
  return Number.isNaN(at) ? undefined : Math.max(0, at - Date.now());
}

export function createApiClient(baseUrl: string, token: string, fetchImpl: Fetch = fetch) {
  const api = `${baseUrl.replace(/\/$/, '')}/api/v1`;

  async function request(path: string, init: RequestInit = {}): Promise<Response> {
    let response: Response;
    try {
      response = await fetchImpl(`${api}${path}`, {
        ...init,
        headers: {
          Authorization: `Token ${token}`,
          'Content-Type': 'application/json',
          ...init.headers,
        },
      });
    } catch {
      throw new ApiError(0, 'NETWORK_ERROR', `Cannot reach the server at ${baseUrl}.`);
    }
    if (response.ok) return response;
    let body: Partial<ApiErrorBody> = {};
    try {
      body = await response.json();
    } catch {
      // Proxy and gateway failures are not guaranteed to use the API error envelope.
    }
    if (response.status === 401) {
      throw new ApiError(
        401,
        body.error?.code ?? 'NOT_AUTHENTICATED',
        'Not signed in: the API token is missing or invalid (EXPO_PUBLIC_API_TOKEN).',
      );
    }
    throw new ApiError(
      response.status,
      body.error?.code ?? `HTTP_${response.status}`,
      body.error?.message ?? `Request failed with HTTP ${response.status}.`,
      retryAfterMs(response),
    );
  }

  const json = <T>(path: string, init?: RequestInit) =>
    request(path, init).then((r) => r.json() as Promise<T>);

  return {
    createUpload: (filename: string, contentType: string, fileSize: number) =>
      json<UploadSlot>('/uploads', {
        method: 'POST',
        body: JSON.stringify({ filename, content_type: contentType, file_size: fileSize }),
      }),

    completeUpload: (uploadId: string) =>
      json<Upload>(`/uploads/${uploadId}/complete`, { method: 'POST' }),

    createAnalysis: (uploadId: string) =>
      json<AnalysisStatus>('/analyses', {
        method: 'POST',
        body: JSON.stringify({ upload_id: uploadId, analysis_profile: 'default' }),
      }),

    getAnalysis: (id: string) => json<AnalysisStatus>(`/analyses/${id}`),

    async getResult(id: string): Promise<ResultResponse> {
      const response = await request(`/analyses/${id}/result`);
      if (response.status === 202) {
        return { state: 'processing', status: (await response.json()) as AnalysisStatus };
      }
      return { state: 'completed', result: (await response.json()) as AnalysisResult };
    },
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;
