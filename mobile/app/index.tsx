import * as DocumentPicker from 'expo-document-picker';
import { useRouter } from 'expo-router';
import { useState } from 'react';
import { Platform, Text } from 'react-native';

import { API_TOKEN, API_URL, api } from '../src/config';
import { Button, Card, ErrorBox, Screen, styles } from '../src/ui';
import { contentTypeFor, type PickedAudio, uploadToStorage } from '../src/upload';

type Step = 'idle' | 'requesting' | 'uploading' | 'confirming' | 'starting';

const STEP_LABEL: Record<Step, string> = {
  idle: '',
  requesting: 'Requesting upload URL…',
  uploading: 'Uploading audio to storage…',
  confirming: 'Confirming upload…',
  starting: 'Starting analysis…',
};

const formatSize = (bytes: number) =>
  bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;

export default function UploadScreen() {
  const router = useRouter();
  const [audio, setAudio] = useState<PickedAudio | null>(null);
  const [step, setStep] = useState<Step>('idle');
  const [error, setError] = useState<string | null>(null);

  const pick = async () => {
    setError(null);
    const picked = await DocumentPicker.getDocumentAsync({ type: 'audio/*', copyToCacheDirectory: true });
    if (picked.canceled) return;
    const asset = picked.assets[0];
    setAudio({
      name: asset.name,
      size: asset.size ?? asset.file?.size ?? 0,
      uri: asset.uri,
      mimeType: asset.mimeType,
      // The picker returns a `File` on native too (SDK 57), but React Native's
      // FormData only accepts {uri, name, type}; browsers need the File itself.
      file: Platform.OS === 'web' ? asset.file : undefined,
    });
  };

  const analyse = async () => {
    if (!audio) return;
    setError(null);
    try {
      const contentType = contentTypeFor(audio);
      setStep('requesting');
      const slot = await api.createUpload(audio.name, contentType, audio.size);
      setStep('uploading');
      await uploadToStorage(slot, audio, contentType);
      setStep('confirming');
      await api.completeUpload(slot.upload_id);
      setStep('starting');
      const job = await api.createAnalysis(slot.upload_id);
      setAudio(null);
      router.push(`/analysis/${job.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setStep('idle');
    }
  };

  const busy = step !== 'idle';

  return (
    <Screen>
      <Text style={styles.title}>Analyse a recording</Text>
      <Text style={styles.muted}>
        Pick an audio file. It is uploaded directly to storage, transcribed and analysed on the
        server.
      </Text>

      {!API_TOKEN ? (
        <ErrorBox message="EXPO_PUBLIC_API_TOKEN is not set. Run `make demo-user` and restart Expo." />
      ) : null}

      <Card title="Audio file">
        {audio ? (
          <>
            <Text style={styles.body}>{audio.name}</Text>
            <Text style={styles.muted}>
              {formatSize(audio.size)} · {contentTypeFor(audio)}
            </Text>
          </>
        ) : (
          <Text style={styles.muted}>No file selected</Text>
        )}
        <Button label={audio ? 'Choose another file' : 'Choose audio file'} onPress={pick} variant="secondary" disabled={busy} />
      </Card>

      <Button label="Upload and analyse" onPress={analyse} disabled={!audio} busy={busy} />
      {busy ? <Text style={styles.muted}>{STEP_LABEL[step]}</Text> : null}
      {error ? <ErrorBox message={error} /> : null}

      <Text style={styles.muted}>Server: {API_URL}</Text>
    </Screen>
  );
}
