import { contentTypeFor, uploadToStorage } from '../upload';

describe('contentTypeFor', () => {
  it.each([
    [{ name: 'call.m4a', mimeType: 'audio/x-m4a' }, 'audio/x-m4a'],
    [{ name: 'call.m4a', mimeType: undefined }, 'audio/mp4'],
    [{ name: 'call.MP3', mimeType: 'application/octet-stream' }, 'audio/mpeg'],
    [{ name: 'notes.txt', mimeType: 'text/plain' }, 'text/plain'],
  ])('%j -> %s', (audio, expected) => {
    expect(contentTypeFor(audio)).toBe(expected);
  });
});

describe('uploadToStorage', () => {
  it('posts the presigned fields first and the file last', async () => {
    const fetchMock = jest.fn().mockResolvedValue(new Response(null, { status: 204 }));
    const file = new Blob(['audio-bytes'], { type: 'audio/mp4' });

    await uploadToStorage(
      {
        upload_id: 'u1',
        object_key: 'audio/1/u1/source.m4a',
        upload_method: 'POST',
        upload_url: 'http://storage.test/bucket',
        upload_fields: { key: 'audio/1/u1/source.m4a', 'Content-Type': 'audio/mp4', policy: 'p' },
        expires_at: '2030-01-01T00:00:00Z',
      },
      { name: 'call.m4a', size: 11, uri: 'blob:x', file },
      'audio/mp4',
      fetchMock,
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('http://storage.test/bucket');
    const keys = Array.from((init.body as FormData).keys());
    expect(keys).toEqual(['key', 'Content-Type', 'policy', 'file']);
  });

  it('fails loudly when storage rejects the upload', async () => {
    const fetchMock = jest.fn().mockResolvedValue(new Response('denied', { status: 403 }));

    await expect(
      uploadToStorage(
        {
          upload_id: 'u1',
          object_key: 'k',
          upload_method: 'POST',
          upload_url: 'http://storage.test/bucket',
          upload_fields: {},
          expires_at: '',
        },
        { name: 'a.m4a', size: 1, uri: 'blob:x', file: new Blob(['a']) },
        'audio/mp4',
        fetchMock,
      ),
    ).rejects.toThrow('HTTP 403');
  });
});
