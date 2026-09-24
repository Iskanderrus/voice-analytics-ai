import { ApiError, createApiClient } from '../api/client';

const jsonResponse = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  });

describe('api client', () => {
  it('sends the token and parses JSON bodies', async () => {
    const fetchMock = jest.fn().mockResolvedValue(jsonResponse(202, { id: 'job-1', status: 'QUEUED' }));
    const api = createApiClient('http://api.test/', 'tok', fetchMock);

    const job = await api.createAnalysis('upload-1');

    expect(job.status).toBe('QUEUED');
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://api.test/api/v1/analyses');
    expect(init.headers.Authorization).toBe('Token tok');
    expect(JSON.parse(init.body)).toEqual({ upload_id: 'upload-1', analysis_profile: 'default' });
  });

  it('turns the backend error envelope into an ApiError', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValue(
        jsonResponse(409, { error: { code: 'UPLOAD_INCOMPLETE', message: 'Not uploaded yet.' } }),
      );
    const api = createApiClient('http://api.test', 'tok', fetchMock);

    await expect(api.completeUpload('u1')).rejects.toEqual(
      expect.objectContaining({ status: 409, code: 'UPLOAD_INCOMPLETE', message: 'Not uploaded yet.' }),
    );
  });

  it('keeps Retry-After metadata on throttling errors', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValue(
        jsonResponse(429, { error: { code: 'THROTTLED', message: 'Slow down.' } }, { 'Retry-After': '12' }),
      );
    const api = createApiClient('http://api.test', 'tok', fetchMock);

    const error = (await api.getAnalysis('j').catch((e: unknown) => e)) as ApiError;

    expect(error.status).toBe(429);
    expect(error.retryAfterMs).toBe(12_000);
  });

  it('explains authentication failures instead of showing the raw server message', async () => {
    const fetchMock = jest.fn().mockResolvedValue(
      jsonResponse(401, {
        error: { code: 'NOT_AUTHENTICATED', message: 'Invalid token header. No credentials provided.' },
      }),
    );
    const api = createApiClient('http://api.test', '', fetchMock);

    const error = (await api.getAnalysis('j').catch((e: unknown) => e)) as ApiError;

    expect(error.status).toBe(401);
    expect(error.message).toContain('EXPO_PUBLIC_API_TOKEN');
  });

  it('reports network failures as NETWORK_ERROR', async () => {
    const api = createApiClient('http://api.test', 'tok', jest.fn().mockRejectedValue(new TypeError('x')));

    const error = await api.getAnalysis('j').catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe('NETWORK_ERROR');
  });

  it('distinguishes a processing result (202) from a completed one', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(jsonResponse(202, { id: 'j', status: 'TRANSCRIBING' }))
      .mockResolvedValueOnce(jsonResponse(200, { id: 'j', status: 'COMPLETED' }));
    const api = createApiClient('http://api.test', 'tok', fetchMock);

    expect((await api.getResult('j')).state).toBe('processing');
    expect((await api.getResult('j')).state).toBe('completed');
  });
});
