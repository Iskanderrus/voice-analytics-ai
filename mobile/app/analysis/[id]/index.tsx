import { useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect } from 'react';
import { ActivityIndicator, Text, View } from 'react-native';

import type { JobStatus } from '../../../src/api/types';
import { Button, Card, colors, ErrorBox, Screen, styles } from '../../../src/ui';
import { useAnalysisStatus } from '../../../src/useAnalysisStatus';

// Display order only; the backend owns the state machine. `stage` matches the
// API's `current_stage` / `error.stage` names.
const STEPS: { status: JobStatus; stage: string; label: string }[] = [
  { status: 'QUEUED', stage: 'queued', label: 'Queued' },
  { status: 'TRANSCRIBING', stage: 'transcription', label: 'Transcribing audio' },
  { status: 'ANALYSING_STANDARD', stage: 'standard_analysis', label: 'Analysing conversation' },
  { status: 'ANALYSING_CUSTOM', stage: 'custom_analysis', label: 'Running specialised analysis' },
  { status: 'COMPLETED', stage: 'done', label: 'Done' },
];

export default function StatusScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const router = useRouter();
  const { status, error } = useAnalysisStatus(id);

  useEffect(() => {
    if (status?.status === 'COMPLETED') {
      router.replace(`/analysis/${id}/result`);
    }
  }, [status?.status, id, router]);

  const current = status?.status === 'CREATED' ? 'QUEUED' : status?.status;
  const failed = current === 'FAILED';
  // A failed job reports the stage it failed in; steps before it completed.
  const currentIndex = failed
    ? STEPS.findIndex((step) => step.stage === status?.error?.stage)
    : STEPS.findIndex((step) => step.status === current);

  return (
    <Screen>
      <Text style={styles.title}>Processing</Text>
      <Text style={styles.muted}>Analysis {id}</Text>

      <Card title="Progress">
        {STEPS.map((step, index) => {
          const done = currentIndex > index || current === 'COMPLETED';
          const failedHere = failed && index === currentIndex;
          const active = !failed && index === currentIndex && current !== 'COMPLETED';
          return (
            <View key={step.status} style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
              {active ? (
                <ActivityIndicator size="small" color={colors.accent} />
              ) : (
                <Text
                  style={{
                    width: 20,
                    color: failedHere ? colors.danger : done ? colors.accent : colors.muted,
                  }}
                >
                  {failedHere ? '✕' : done ? '✓' : '○'}
                </Text>
              )}
              <Text
                style={[
                  styles.body,
                  failedHere && { color: colors.danger },
                  !done && !active && !failedHere && { color: colors.muted },
                ]}
              >
                {step.label}
              </Text>
            </View>
          );
        })}
      </Card>

      {status?.status === 'FAILED' && status.error ? (
        <>
          <ErrorBox
            message={`Failed during ${status.error.stage}: ${status.error.message} (${status.error.code})`}
          />
          <Button label="Analyse another file" onPress={() => router.replace('/')} />
        </>
      ) : null}
      {error ? <ErrorBox message={error} /> : null}
    </Screen>
  );
}
