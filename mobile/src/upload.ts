import { FileSystemUploadType, uploadAsync } from 'expo-file-system/legacy';

import type { UploadSlot } from './api/types';

export type PickedAudio = {
  name: string;
  size: number;
  uri: string;
  mimeType?: string;
  file?: Blob; // web only; native uploads stream from `uri`
};

const BY_EXTENSION: Record<string, string> = {
  m4a: 'audio/mp4',
  mp4: 'audio/mp4',
  mp3: 'audio/mpeg',
  wav: 'audio/wav',
  aac: 'audio/aac',
  ogg: 'audio/ogg',
  webm: 'audio/webm',
  flac: 'audio/flac',
};

/** Pickers often report no or a generic MIME type; fall back to the extension.
 * The backend still validates the type against its allowlist. */
export function contentTypeFor(audio: Pick<PickedAudio, 'name' | 'mimeType'>): string {
  if (audio.mimeType?.startsWith('audio/')) return audio.mimeType;
  const extension = audio.name.split('.').pop()?.toLowerCase() ?? '';
  return BY_EXTENSION[extension] ?? audio.mimeType ?? 'application/octet-stream';
}

/** Uploads the file straight to object storage with the presigned POST policy.
 * The API server never sees the audio bytes. */
export async function uploadToStorage(
  slot: UploadSlot,
  audio: PickedAudio,
  contentType: string,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  if (!audio.file) {
    // Native: Expo's fetch cannot send React Native `{uri}` form parts, and
    // reading the file into JS memory would not scale. uploadAsync streams the
    // file from disk as a multipart POST (fields first, file last, as S3 requires).
    const result = await uploadAsync(slot.upload_url, audio.uri, {
      httpMethod: 'POST',
      uploadType: FileSystemUploadType.MULTIPART,
      fieldName: 'file',
      mimeType: contentType,
      parameters: slot.upload_fields,
    });
    if (result.status < 200 || result.status >= 300) {
      throw new Error(`Storage upload failed (HTTP ${result.status}).`);
    }
    return;
  }

  const form = new FormData();
  for (const [key, value] of Object.entries(slot.upload_fields)) {
    form.append(key, value);
  }
  // S3 requires the file to be the last field.
  form.append('file', audio.file, audio.name);
  const response = await fetchImpl(slot.upload_url, { method: 'POST', body: form });
  if (!response.ok) {
    throw new Error(`Storage upload failed (HTTP ${response.status}).`);
  }
}
